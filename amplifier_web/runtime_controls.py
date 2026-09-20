"""Application controls on public Core/Foundation interfaces, shared by UI and agent."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict, is_dataclass
import inspect
import json
from pathlib import Path
import re
import subprocess
import uuid

from .host.config import app_home, write_private

REDACTED = "[REDACTED]"
SECTIONS = ("providers", "tools", "hooks")
PLAN_KEYS = {*SECTIONS, "session", "agents", "spawn", "context", "instruction"}


def public_config(value):
    if is_dataclass(value):
        value = asdict(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): REDACTED if any(term in str(key).lower() for term in
                    ("api_key", "secret", "password", "authorization", "cookie", "access_token", "refresh_token", "github_token", "copilot_agent_token"))
                else public_config(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_config(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def identity(row):
    return row.get("instance_id") or row.get("id") or row.get("module")


def restore_redactions(value, old):
    if value == REDACTED:
        if old is None:
            raise ValueError("A redacted value cannot be used for a new setting")
        return copy.deepcopy(old)
    if isinstance(value, dict):
        return {key:restore_redactions(item, old.get(key) if isinstance(old, dict) else None)
                for key,item in value.items()}
    if isinstance(value, list):
        previous = {identity(row):row for row in old or [] if isinstance(row, dict) and identity(row)} if isinstance(old, list) else {}
        return [restore_redactions(row, previous.get(identity(row)) if isinstance(row, dict) else None) for row in value]
    return copy.deepcopy(value)


def override_path(session_id):
    # Session IDs originate in Core or the application; reject path syntax here
    # as well because this helper owns a private configuration file.
    if not session_id or Path(session_id).name != session_id or session_id in {".", ".."}:
        raise ValueError("Invalid session identity")
    return app_home() / "sessions" / session_id / "configuration.json"


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("Configuration must be a mount-plan object")
    for section in SECTIONS:
        rows = plan.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} must be a module list")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("module"), str) or not row["module"]:
                raise ValueError(f"Each {section} entry needs a module name")
            key = identity(row)
            if key in seen:
                raise ValueError(f"Duplicate module instance: {key}")
            seen.add(key)
            if not isinstance(row.get("config", {}), dict) or not isinstance(row.get("enabled", True), bool):
                raise ValueError("Module config must be an object and enabled a boolean")
    session = plan.get("session", {})
    if not isinstance(session, dict) or not isinstance(plan.get("agents", {}), dict):
        raise ValueError("Session and agents must be objects")
    loop = session.get("orchestrator", {}).get("module")
    if loop not in {"loop-live", "loop-streaming"}:
        raise ValueError("The main session requires loop-live or loop-streaming")
    if not session.get("context", {}).get("module"):
        raise ValueError("A context module is required")
    if not any(row.get("enabled", True) for row in plan.get("providers", [])):
        raise ValueError("At least one provider must remain enabled")


class RuntimeControls:
    def __init__(self, session, runtime, telemetry=None):
        self.session, self.runtime, self.telemetry = session, runtime, telemetry
        self.coordinator = session.coordinator
        self.configurator = self.coordinator.get_capability("web.configurator")
        self.prepared = self.coordinator.get_capability("web.prepared")
        self.lock = asyncio.Lock()
        self.selection = None
        self.selection_cleared = False
        self.max_output_tokens = None
        self.logins = {}
        from .provider_catalog import ProviderCatalog
        self.model_catalog=ProviderCatalog()
        self.catalog_revision=str(uuid.uuid4())
        self.coordinator.register_capability("web.controls.persist", self.persist)

    async def close(self):
        await self.model_catalog.close()
        tasks = [row["task"] for row in self.logins.values() if not row["task"].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    def state_path(self):
        return override_path(self.session.session_id).with_name("control-state.json")

    async def restore(self):
        if not self.state_path().exists():
            return
        saved = json.loads(self.state_path().read_text())
        if self.configurator:
            await self.configurator.apply_saved_settings(saved.get("configurator", {}))
        self.coordinator.session_state["goal"] = saved.get("goal")
        # Older snapshots recorded None for module-managed/automatic limits.
        # Absence of an override must leave the newly mounted module's default
        # intact, rather than passing null to the explicit budget-edit API.
        budget={key:value for key,value in saved.get("budget",{}).items() if value is not None}
        if budget:
            await self._perform("budget.set", budget)
        if saved.get("selection"):
            await self._perform("provider.select", await self.restore_selection(saved["selection"]))
        if saved.get("mode"):
            await self.mode("mode.set", {"name":saved["mode"]})

    async def restore_selection(self, selection):
        """Resolve old provider-family IDs only when the same model is unambiguous."""
        providers = self.coordinator.get("providers") or {}
        instance = selection.get("instance") or selection.get("provider")
        if instance in providers:
            return selection
        candidates = []
        for name, provider in providers.items():
            info = provider.get_info()
            if inspect.isawaitable(info):
                info = await info
            family = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
            defaults = (info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})) or {}
            if family == instance and selection.get("model") == (defaults.get("model") or defaults.get("default_model")):
                candidates.append(name)
        if len(candidates) == 1:
            return {**selection, "instance": candidates[0]}
        # Do not silently move an old pin to another model, account or backend.
        return selection

    def persist(self):
        snapshot = self.configurator.snapshot() if self.configurator else {}
        loop, context = self.coordinator.get("orchestrator"), self.coordinator.get("context")
        budget = {key:getattr(target, attr) for key,target,attr in
                  (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")) if getattr(target,attr,None) is not None}
        if self.max_output_tokens is not None:
            budget["maxOutputTokens"] = self.max_output_tokens
        previous = json.loads(self.state_path().read_text()) if self.state_path().exists() else {}
        write_private(self.state_path(), json.dumps({"configurator":{"disabled":{key:row.get("disabled",[]) for key,row in snapshot.items()}},
            "goal":self.coordinator.session_state.get("goal"),"mode":self.coordinator.session_state.get("active_mode"),
            "selection":None if self.selection_cleared else self.selection or previous.get("selection"),"budget":budget},default=str))
        write_private(self.state_path().with_name("effective-configuration.json"),json.dumps(
            {key:value for key,value in self.coordinator.config.items() if key in PLAN_KEYS},default=str))

    def require_idle(self):
        if getattr(self.runtime, "generation", None) or getattr(self.runtime, "queued_inputs", 0):
            raise ValueError("Wait for the current response before changing session configuration")
        loop = self.coordinator.get("orchestrator")
        if any(not job["task"].done() for job in getattr(loop, "jobs", {}).values()):
            raise ValueError("Wait for delegated work to finish before changing session configuration")
        children = self.coordinator.get_capability("live.children")
        if children and any(row.get("status") in {"starting", "running", "working", "idle"} for row in children.rows.values()):
            raise ValueError("Finish or stop the active workers before changing session configuration")

    def configuration(self):
        plan = copy.deepcopy(self.coordinator.config)
        stored = override_path(self.session.session_id)
        saved = json.loads(stored.read_text()) if stored.exists() else None
        if saved:
            for section in SECTIONS:
                disabled = [row for row in saved.get(section, []) if row.get("enabled") is False]
                current = {identity(row) for row in plan.get(section, [])}
                plan.setdefault(section, []).extend(row for row in disabled if identity(row) not in current)
        snapshot = self.configurator.snapshot() if self.configurator else {}
        modules = []
        for section in SECTIONS:
            for row in plan.get(section, []):
                key = identity(row)
                enabled = row.get("enabled", True) and key not in snapshot.get(section, {}).get("disabled", [])
                row["enabled"] = enabled
                modules.append({"section": section, "module": row["module"], "id": key, "enabled": enabled,
                                "source": row.get("source"), "config": public_config(row.get("config", {}))})
        provenance = {}
        if self.configurator:
            for section in ("tools", "providers", "hooks", "agents", "context", "behaviors"):
                method = getattr(self.configurator, section + "_list", None)
                if method:
                    provenance[section] = public_config(method())
        return {"bundle": plan.get("bundle_name"), "plan": public_config(plan), "modules": modules,
            "disabledModules": {key:value.get("disabled", []) for key,value in snapshot.items()},
            "provenance": provenance, "capabilities": self.capabilities()}

    def capabilities(self):
        return {"configuration": True, "configurationApplyRequiresRestart": True,
            "modes": bool(self.coordinator.session_state.get("mode_discovery")),
            "goals": hasattr(self.coordinator.get("orchestrator"), "goal_stall_threshold"),
            "skills": bool(self.coordinator.get_capability("skills_discovery")),
            "tools": True, "usage": self.telemetry is not None}

    async def checkpoint(self):
        self.persist()
        callback = self.coordinator.get_capability("live.checkpoint")
        if callback:
            await callback()

    async def perform(self, operation, args=None):
        args = args or {}
        if not isinstance(args, dict):
            raise ValueError("Control arguments must be an object")
        if operation == "operations.cancel":
            coordinator = self.coordinator
            target = args.get("runtimeSessionId")
            if target != self.session.session_id:
                children = coordinator.get_capability("live.children")
                child = children.sessions.get(target) if children else None
                if child is None:
                    raise ValueError("The operation's owning session is no longer mounted")
                coordinator = child.coordinator
            arguments = {"action": "terminate", "process_id": args.get("processId")}
            return await self.invoke({"name": "bash", "arguments": arguments},
                coordinator=coordinator, bound_arguments=arguments,
                provenance={"source": "operations.cancel", "actor": args.get("actor", "ui"),
                            "operation_id": args.get("operationId")}, checkpoint=False)
        async with self.lock:
            return await self._perform(operation, args)

    async def _perform(self, operation, args):
        if operation == "configuration.inspect":
            return self.configuration()
        if operation == "history.snapshot":
            self.require_idle()
            await self.checkpoint()
            return {"messages":await self.coordinator.get("context").get_messages()}
        if operation == "configuration.exportResources":
            return self.export_resources()
        if operation in {"configuration.providers", "configuration.providerModels", "configuration.providerTest", "configuration.providerLogin"}:
            return await self.provider_control(operation,args)
        if operation == "configuration.apply":
            self.require_idle()
            previous=copy.deepcopy(self.coordinator.config)
            stored=override_path(self.session.session_id)
            if stored.exists():
                saved=json.loads(stored.read_text())
                for section in SECTIONS:
                    current={identity(row) for row in previous.get(section,[])}
                    previous.setdefault(section,[]).extend(row for row in saved.get(section,[]) if identity(row) not in current)
            plan = restore_redactions(args.get("config"), previous)
            validate_plan(plan)
            plan = {key:value for key,value in plan.items() if key in PLAN_KEYS}
            write_private(override_path(self.session.session_id), json.dumps(plan, indent=2))
            # A full mount-plan edit supersedes previous live module toggles.
            if self.state_path().exists():
                controls=json.loads(self.state_path().read_text())
                disabled=controls.get('configurator',{}).get('disabled',{})
                for section in SECTIONS:disabled.pop(section,None)
                write_private(self.state_path(),json.dumps(controls))
            return {"requiresRestart": True, "configuration": public_config(plan)}
        if operation == "configuration.toggle":
            self.require_idle()
            section, name, enabled = args.get("section"), args.get("name"), args.get("enabled")
            if section not in {"tools", "providers", "agents", "context"} or not isinstance(enabled, bool):
                raise ValueError("Live toggles support tools, providers, agents, and context; edit hooks with configuration.apply")
            if not self.configurator:
                raise ValueError("Session configurator is unavailable")
            if section == "providers" and not enabled and len(self.coordinator.get("providers")) <= 1:
                raise ValueError("At least one provider must remain enabled")
            singular = {"tools":"tool", "providers":"provider", "agents":"agent", "context":"context"}[section]
            suffix = "_enable" if enabled else "_disable"
            if section == "tools" and any(identity(row)==name for row in self.coordinator.config.get("tools",[])):
                function = getattr(self.configurator,"tool" + suffix + "_module")
            else:
                function = getattr(self.configurator, singular + suffix)
            result = function(name)
            if inspect.isawaitable(result):
                await result
            await self.checkpoint()
            return self.configuration()
        if operation == "catalog.inspect":
            discovery = self.coordinator.get_capability("skills_discovery")
            skills = discovery.list_skills() if discovery else []
            return {"tools": [{"name":name,"description":getattr(tool,"description", ""),
                                "inputSchema":public_config(getattr(tool,"input_schema", {}))}
                               for name,tool in (self.coordinator.get("tools") or {}).items()],
                    "agents": public_config(self.coordinator.config.get("agents", {})),
                    "skills": [{"name":row[0],"description":row[1] if len(row)>1 else ""} for row in skills],
                    "capabilities": self.capabilities()}
        if operation == "usage.inspect":
            result = self.telemetry.usage() if self.telemetry else {"calls": 0, "usage": {"costType":"unavailable"}, "trace": []}
            messages = await self.coordinator.get("context").get_messages()
            result.update(messages=len(messages), userTurns=sum(row.get("role")=="user" for row in messages),
                          contextCharacters=sum(len(str(row.get("content", ""))) for row in messages))
            return result
        if operation == "context.clear":
            self.require_idle()
            await self.coordinator.get("context").clear()
            self.coordinator.session_state["goal"] = None
            await self.checkpoint()
            return {"cleared":True}
        if operation.startswith("mode."):
            return await self.mode(operation, args)
        if operation in {"goals.get", "goals.set", "goals.clear"}:
            if operation != "goals.get":
                self.require_idle()
                if not self.capabilities()["goals"]:
                    raise ValueError("This orchestrator does not support goal continuation")
                condition = args.get("condition")
                cap = args.get("maxTurns")
                if operation == "goals.set" and (not isinstance(condition, str) or not condition.strip()):
                    raise ValueError("A nonempty goal condition is required")
                if cap is not None and (type(cap) is not int or cap < 1):
                    raise ValueError("maxTurns must be a positive integer or null")
                self.coordinator.session_state["goal"] = None if operation == "goals.clear" else {
                    "condition":condition,"cap":cap,"turns_used":0,"last_reason":None,"reasons":[],"continuations":0}
                await self.checkpoint()
            return {"goal":public_config(self.coordinator.session_state.get("goal"))}
        if operation in {"budget.get", "budget.set"}:
            loop = self.coordinator.get("orchestrator")
            context = self.coordinator.get("context")
            if operation == "budget.set":
                self.require_idle()
                if "maxOutputTokens" in args and (type(args["maxOutputTokens"]) is not int or args["maxOutputTokens"] < 1):
                    raise ValueError("maxOutputTokens must be a positive integer")
                for key, target, attr in (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")):
                    if key in args:
                        value = args[key]
                        if type(value) is not int or (value < 1 and not (key == "maxIterations" and value == -1)):
                            raise ValueError(f"{key} must be a positive integer" + (" or -1 for unlimited" if key == "maxIterations" else ""))
                        if not hasattr(target, attr):
                            raise ValueError(f"The mounted module does not expose {key}")
                if "maxOutputTokens" in args:
                    from .host.session import SelectedProvider
                    providers = self.coordinator.get("providers") or {}
                    current = (getattr(loop,"root_provider",None) or loop._select_provider(providers)) if providers else None
                    if current is None:
                        raise ValueError("No provider is available for an output token limit")
                for key, target, attr in (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")):
                    if key in args:
                        setattr(target, attr, args[key])
                if "maxOutputTokens" in args:
                    selection = dict(current.selection) if isinstance(current,SelectedProvider) else {}
                    original = current.original if isinstance(current,SelectedProvider) else current
                    selection["max_output_tokens"] = args["maxOutputTokens"]
                    loop.root_provider = SelectedProvider(original,selection)
                    self.max_output_tokens = args["maxOutputTokens"]
                self.persist()
            return {"maxIterations":getattr(loop,"max_iterations",None), "contextTokens":getattr(context,"max_tokens",None),
                    "maxOutputTokens":self.max_output_tokens,
                    "scope":"current session", "contextNote":"The provider context window can further constrain the effective budget."}
        if operation == "provider.reset":
            self.require_idle()
            from .host.session import SelectedProvider
            loop = self.coordinator.get('orchestrator')
            previous = getattr(loop, 'root_provider', None)
            loop.root_provider = None
            try:
                providers = self.coordinator.get('providers') or {}
                if self.max_output_tokens is not None and providers:
                    automatic = loop._select_provider(providers)
                    if automatic is not None:
                        loop.root_provider = SelectedProvider(automatic, {'max_output_tokens': self.max_output_tokens})
            except Exception:
                loop.root_provider = previous
                raise
            self.selection=None
            self.selection_cleared=True
            self.persist()
            return {'selection':None,'scope':'main session'}
        if operation == "provider.select":
            self.require_idle()
            from .host.session import SelectedProvider
            providers = self.coordinator.get("providers") or {}
            args = {**args,"instance":args.get("instance") or args.get("provider")}
            if args.get("instance") not in providers or not isinstance(args.get("model"), str) or not args["model"].strip():
                raise ValueError("Select an available provider instance and model")
            selected = {key:args[key] for key in ("instance","model","effort") if key in args}
            effective = {**selected, **({"max_output_tokens":self.max_output_tokens} if self.max_output_tokens else {})}
            self.coordinator.get("orchestrator").root_provider = SelectedProvider(providers[args["instance"]], effective)
            self.selection = selected
            self.selection_cleared = False
            self.persist()
            return {"selection":selected,"scope":"main session"}
        if operation == "tool.invoke":
            self.require_idle()
            return await self.invoke(args)
        raise ValueError(f"Unsupported runtime control: {operation}")

    async def provider_control(self, operation, args):
        providers = self.coordinator.get("providers") or {}
        if operation == "configuration.providers":
            rows = []
            for name,provider in providers.items():
                info = provider.get_info()
                if inspect.isawaitable(info):
                    info = await info
                schema = getattr(provider,"get_config_schema",None)
                schema = schema() if callable(schema) else {"fields":getattr(info,"config_fields",[])}
                if inspect.isawaitable(schema):
                    schema = await schema
                from .provider_catalog import fingerprint
                mounted=next((row for row in getattr(self.session,'config',{}).get('providers',[]) if (row.get('id') or row.get('instance_id') or row.get('module','').removeprefix('provider-'))==name),{})
                catalog_key=fingerprint([name,mounted or getattr(provider,'config',{}),public_config(info)])
                rows.append({"id":name,"catalogKey":catalog_key,"info":public_config(info),"configSchema":public_config(schema),
                    "supports":{"models":callable(getattr(provider,"list_models",None)),
                                "test":callable(getattr(provider,"list_models",None)),
                                "login":callable(getattr(provider,"login",None))}})
            loop=self.coordinator.get('orchestrator')
            selected=loop._select_provider(providers) if providers else None
            original=getattr(selected,'original',selected)
            name=next((key for key,value in providers.items() if value is original),None)
            info=selected.get_info() if selected is not None else None
            if inspect.isawaitable(info):info=await info
            defaults=(info.get('defaults',{}) if isinstance(info,dict) else getattr(info,'defaults',{})) or {}
            selected_config=next((row.get('config',{}) for row in getattr(self.session,'config',{}).get('providers',[]) if (row.get('id') or row.get('instance_id') or row.get('module','').removeprefix('provider-'))==name),{})
            effective={'instance':name,'model':defaults.get('model') or defaults.get('default_model'),'effort':(self.selection or {}).get('effort') or defaults.get('reasoning_effort') or selected_config.get('reasoning_effort')}
            return {"catalogRevision":self.catalog_revision,"providers":rows,'selection':self.selection,'effective':effective,'pinned':bool(self.selection)}
        name = args.get("provider") or args.get("instance")
        provider = providers.get(name)
        if provider is None:
            raise ValueError("Provider instance is not mounted")
        if operation in {"configuration.providerModels", "configuration.providerTest"}:
            method = getattr(provider,"list_models",None)
            if not callable(method):
                if operation=='configuration.providerModels':return {'provider':name,'models':[],'supported':False}
                raise ValueError("This provider does not expose a model-list API for connection testing")
            async def load():
                result=method()
                if inspect.isawaitable(result):result=await asyncio.wait_for(result,120)
                return public_config(result)
            from .provider_catalog import fingerprint
            key=(name,id(provider),fingerprint(getattr(provider,'config',{})))
            result=await load() if operation=='configuration.providerTest' else await self.model_catalog.get(key,load,refresh=args.get('refresh',False))
            if operation == "configuration.providerTest":
                return {"provider":name,"reachable":True,"modelCount":len(result),"method":"provider.list_models"}
            return {"provider":name,"models":result,'supported':True}
        method = getattr(provider,"login",None)
        if not callable(method):
            raise ValueError("This provider does not expose an interactive login API; configure its credential fields instead")
        if name not in self.logins or args.get("restart"):
            self.require_idle()
            previous = self.logins.get(name)
            if previous and not previous["task"].done():
                raise ValueError("A login is already in progress")
            row = {"provider":name,"loginId":str(uuid.uuid4()),"phase":"waiting","instructions":[]}
            def display(text):
                row["instructions"] = (row["instructions"] + [str(text)[:2000]])[-20:]
            async def login():
                try:
                    parameters = inspect.signature(method).parameters
                    kwargs = {"print_fn":display} if "print_fn" in parameters else {}
                    response = method(**kwargs)
                    if inspect.isawaitable(response):
                        response = await response
                    row["phase"] = "completed" if response else "failed"
                except asyncio.CancelledError:
                    row["phase"] = "cancelled"
                    raise
                except Exception as exc:
                    row.update(phase="failed",error=f"Provider login failed ({type(exc).__name__}). Check its login instructions and try again.")
            row["task"] = asyncio.create_task(login())
            self.logins[name] = row
            await asyncio.sleep(0)
        return {key:value for key,value in self.logins[name].items() if key != "task"}

    def export_resources(self):
        registry = self.coordinator.get_capability("web.registry")
        bundle = self.prepared.bundle if self.prepared else None
        result = {"instruction":getattr(bundle,"instruction",None) or "", "context":[], "namespaces":[], "warnings":[]}
        resolver = self.coordinator.get_capability("model_role_resolver")
        # This metadata belongs to the pinned routing-matrix implementation.
        # Other resolver implementations need not expose their strategy; never
        # fabricate a matrix by flattening model resolution results.
        matrix = getattr(resolver,"_matrix_roles",None)
        if isinstance(matrix,dict):
            result["routingMatrix"] = {"name":getattr(resolver,"name",None),"roles":public_config(matrix)}
        elif resolver:
            result["warnings"].append("The active model routing strategy does not expose an exportable matrix.")
        roots = []
        for namespace,state in (registry.get_state() if registry else {}).items():
            if not state.local_path or not state.uri.startswith("git+"):
                continue
            path = Path(state.local_path).resolve()
            if path.is_file():
                path = path.parent
            uri = state.uri
            try:
                commit = subprocess.run(["git","-C",str(path),"rev-parse","HEAD"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
                repository = Path(subprocess.run(["git","-C",str(path),"rev-parse","--show-toplevel"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()).resolve()
                base = uri.split("#",1)[0]
                if "@" in base.split("github.com/",1)[-1]:
                    base = base.rsplit("@",1)[0]
                uri = base + "@" + commit
                roots.append((repository,uri))
            except (subprocess.SubprocessError, OSError):
                roots.append((path,uri))
        if "routingMatrix" in result:
            import yaml
            module_path = Path(inspect.getfile(type(resolver))).resolve()
            for root,_ in roots:
                balanced = root / "routing" / "balanced.yaml"
                if module_path.is_relative_to(root) and balanced.is_file():
                    value = yaml.safe_load(balanced.read_text()) or {}
                    result["routingMatrix"]["baseRoles"] = list(value.get("roles",{}))
                    break
        namespaces = {}
        # Packaged behavior resources live outside Foundation's Git cache.
        from .builtin_behaviors import resource_root, SHELL_BEHAVIOR_URI
        builtin_root = resource_root().resolve()
        if any(Path(value).resolve() == builtin_root for value in
               (getattr(bundle, "source_base_paths", {}) or {}).values()):
            roots.append((builtin_root, SHELL_BEHAVIOR_URI.split("#", 1)[0]))
            result["warnings"].append("Unified skill sources in this export follow the repository's main branch; pin that source to a published revision for reproducible skill content.")
        for namespace,value in (getattr(bundle,"source_base_paths",{}) or {}).items():
            path = Path(value).resolve()
            match = next(((root,uri) for root,uri in roots if path.is_relative_to(root)),None)
            if match:
                relative = str(path.relative_to(match[0]))
                uri = match[1].split("#",1)[0] + ("#subdirectory=" + relative if relative != "." else "")
                namespaces[namespace] = path
                result["namespaces"].append({"namespace":namespace,"uri":uri,"localRoot":str(path)})
        pending = list((getattr(bundle,"context",{}) or {}).items())
        reference = re.compile(r"@([\w.-]+):([\w./-]+)")
        def references(text):
            for namespace,relative in reference.findall(text):
                if namespace in namespaces:
                    candidate = (namespaces[namespace] / relative.rstrip(".")).resolve()
                    if candidate.is_relative_to(namespaces[namespace]) and candidate.is_file():
                        pending.append((namespace + ":" + relative, candidate))
        references(result["instruction"])
        references(json.dumps(self.coordinator.config.get("agents",{})))
        seen = set()
        total = 0
        while pending and len(seen) < 500:
            name,value = pending.pop(0)
            path = Path(value).resolve()
            if name in seen:
                continue
            seen.add(name)
            match = next(((root,uri) for root,uri in sorted(roots,key=lambda row:len(str(row[0])),reverse=True) if path.is_relative_to(root)),None)
            if not match or not path.is_file() or path.stat().st_size > 1_000_000:
                result["warnings"].append(f"Context {name} is not a portable cached bundle resource; it was not embedded.")
                continue
            text = path.read_text()
            total += len(text.encode())
            if total > 8_000_000:
                raise ValueError("Bundle resources exceed the 8 MB portable export limit")
            result["context"].append({"name":name,"text":text,"uri":match[1],"relativePath":str(path.relative_to(match[0]))})
            references(text)
        if pending:
            raise ValueError("Bundle references exceed the 500 file portable export limit")
        return result

    async def mode(self, operation, args):
        state = self.coordinator.session_state
        discovery = state.get("mode_discovery")
        if operation == "mode.list":
            rows = discovery.list_modes() if discovery else []
            return {"available":bool(discovery), "active":state.get("active_mode"), "modes":[
                {"name":row[0],"description":row[1] if len(row)>1 else "","source":row[2] if len(row)>2 else ""} for row in rows]}
        if operation not in {"mode.set", "mode.clear"}:
            raise ValueError("Unknown mode operation")
        self.require_idle()
        previous = state.get("active_mode")
        if operation == "mode.clear":
            if previous:
                await self.coordinator.hooks.emit("mode:cleared", {"name":previous,"previous_mode":previous})
            state["active_mode"] = None
        else:
            definition = discovery.find(args.get("name")) if discovery else None
            if not definition:
                raise ValueError("Mode is not available in this bundle")
            name = args["name"]
            if name != previous:
                payload = {key:getattr(definition,key) for key in ("description","default_action","safe_tools","warn_tools","confirm_tools","block_tools") if hasattr(definition,key)}
                payload.update({"old":previous,"new":name,"from_mode":previous,"to_mode":name} if previous else {"name":name,"mode":name})
                await self.coordinator.hooks.emit("mode:changed" if previous else "mode:activated",payload)
                state["active_mode"] = name
        hooks = state.get("mode_hooks")
        if hooks and hasattr(hooks, "reset_warnings"):
            hooks.reset_warnings()
        await self.checkpoint()
        return await self.mode("mode.list", {})

    async def invoke(self, args, *, coordinator=None, bound_arguments=None, provenance=None, checkpoint=True):
        coordinator = coordinator or self.coordinator
        bound_arguments = copy.deepcopy(bound_arguments)
        import jsonschema
        name, arguments = args.get("name"), args.get("arguments", {})
        tool = (coordinator.get("tools") or {}).get(name)
        if tool is None:
            raise ValueError("Tool is not mounted")
        jsonschema.validate(arguments, getattr(tool,"input_schema",{}))
        call = str(uuid.uuid4())
        data = {"tool_name":name,"tool_call_id":call,"tool_input":arguments,"source":"user", "tool_obj":tool, **(provenance or {})}
        hooks = coordinator.hooks
        pre = await hooks.emit("tool:pre", data)
        pre = await coordinator.process_hook_result(pre,"tool:pre",name)
        if pre.action == "deny":
            await hooks.emit("tool:error",{**data,"error":{"type":"Denied"}})
            return {"success":False,"error":{"message":pre.reason or "Denied by session policy"},"callId":call}
        if pre.action == "modify" and isinstance(pre.data,dict) and "tool_input" in pre.data:
            arguments = pre.data["tool_input"]
            jsonschema.validate(arguments,getattr(tool,"input_schema",{}))
            data = {**data,"tool_input":arguments}
        if bound_arguments is not None and arguments != bound_arguments:
            await hooks.emit("tool:error", {**data, "error": {"type": "Denied"}})
            raise ValueError("A policy modification cannot redirect operation cancellation")
        from amplifier_module_loop_live.scope import JOB_CALL
        ownership = JOB_CALL.set(call)
        try:
            result = await tool.execute(arguments)
            await hooks.emit("tool:post",{**data,"tool_result":result.model_dump() if hasattr(result,"model_dump") else result})
            if checkpoint:
                await self.checkpoint()
            return {"callId":call,"result":public_config(result)}
        except Exception as exc:
            await hooks.emit("tool:error",{**data,"error":{"type":type(exc).__name__}})
            raise
        finally:
            JOB_CALL.reset(ownership)
