"""Standalone application session policy using public Foundation primitives."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
import inspect
import json
import os
from pathlib import Path

from .config import expand_environment, load_config, merge, write_private

VENDORED_LOOP = Path(__file__).resolve().parents[1] / "runtime_deps" / "vendor" / "loop-live"
LOOP_SOURCE = str(VENDORED_LOOP) if (VENDORED_LOOP / "pyproject.toml").exists() else "git+https://github.com/bkrabach/amplifier-module-loop-live@bb9f5966d285ee4a93f4d84309aacf4bd9a09b5a"


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if any(term in key.lower() for term in ("api_key", "token", "secret", "password", "authorization", "cookie")) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def live_plan(plan, background_delegate=True):
    """Overlay supported streaming loops while preserving the bundle contract."""
    result = copy.deepcopy(plan)
    changed = []
    root_loop = result.get("session", {}).get("orchestrator", {}).get("module")
    if root_loop not in {"loop-streaming", "loop-live"}:
        raise ValueError(f"Main session orchestrator {root_loop!r} is not compatible with live input. Select a bundle using loop-streaming or loop-live; custom orchestrators are not silently replaced.")
    def visit(node, prefix=""):
        loop = node.get("session", {}).get("orchestrator", {})
        if loop.get("module") in {"loop-streaming", "loop-live"}:
            original = loop["module"]
            loop.update(module="loop-live", source=LOOP_SOURCE)
            loop.setdefault("config", {}).update(configured_bundle=True, background_delegate=background_delegate)
            changed.append({"path": prefix + "session.orchestrator", "from": original, "to": "loop-live"})
        for name, agent in node.get("agents", {}).items():
            if isinstance(agent, dict):
                visit(agent, prefix + "agents." + name + ".")
    visit(result)
    return result, changed


def repair_interrupted_receipts(messages):
    result = copy.deepcopy(messages)
    for row in result:
        if row.get("role") != "tool" or not isinstance(row.get("content"), str):
            continue
        try:
            receipt = json.loads(row["content"])
        except ValueError:
            continue
        if isinstance(receipt, dict) and receipt.get("status") == "queued" and receipt.get("job_id") and receipt.get("call_id") == row.get("tool_call_id"):
            row["content"] = json.dumps({"status": "interrupted", "job_id": receipt["job_id"],
                "call_id": receipt["call_id"], "outcome": "unconfirmed", "effects": "not_rolled_back",
                "instruction": "This receipt was imported from a stopped host. Inspect actual state; do not automatically repeat the operation."})
    return result


class SelectedProvider:
    """Root-only model preference; worker routing remains bundle-owned."""
    def __init__(self, provider, selection):
        self.original, self.selection = provider, selection
    def __getattr__(self, name):
        return getattr(self.original, name)
    def get_info(self):
        info = self.original.get_info()
        return info.model_copy(update={"defaults": {**info.defaults, **{key:self.selection[key]
            for key in ("model", "max_output_tokens") if key in self.selection}}})
    async def complete(self, request, **kwargs):
        updates = {key:self.selection[key] for key in ("model", "max_output_tokens") if key in self.selection}
        if self.selection.get("effort") is not None:
            updates["reasoning_effort"] = self.selection["effort"]
            kwargs["reasoning_effort"] = self.selection["effort"]
        return await self.original.complete(request.model_copy(update=updates), **kwargs)


def _apply_settings(bundle, config):
    settings = config.settings
    bundle.providers = merge(bundle.providers, config.providers)
    for kind in ("tools", "hooks"):
        values = merge(getattr(bundle, kind), settings.get("modules", {}).get(kind, []))
        values = merge(values, settings.get("config", {}).get(kind, []))
        setattr(bundle, kind, values)
    bundle.session = merge(bundle.session, settings.get("config", {}).get("session", {}))
    routing = settings.get("routing", {})
    if routing:
        for hook in bundle.hooks:
            if hook.get("module") == "hooks-routing":
                patch = {"custom_routing_dirs": [str(config.workspace / ".amplifier-unified" / "routing.local"),
                    str(config.workspace / ".amplifier-unified" / "routing"), str(config.home / "config" / "routing"), str(config.registry_home / "routing")]}
                if routing.get("matrix"):
                    patch["default_matrix"] = routing["matrix"]
                if routing.get("overrides"):
                    patch["overrides"] = routing["overrides"]
                hook["config"] = merge(hook.get("config", {}), patch)
    overrides = settings.get("overrides", {})
    for kind in ("providers", "tools", "hooks"):
        values = []
        for row in getattr(bundle, kind):
            override = overrides.get(row.get("id"), overrides.get(row.get("module"), {}))
            if override.get("enabled") is False:
                continue
            row = merge(row, {key:value for key,value in override.items() if key in {"source", "config"}})
            if kind == "providers" and row.get("id") and not row.get("instance_id"):
                row["instance_id"] = row["id"]
            values.append(expand_environment(row))
        setattr(bundle, kind, values)
    return bundle


def is_snapshot(bundle):
    # Foundation preserves the public SemVer version field; arbitrary metadata
    # keys are discarded on load. Build metadata is portable to other hosts.
    from ..bundles import SNAPSHOT_VERSION
    return getattr(bundle, "version", None) == SNAPSHOT_VERSION


def _expand_module_configuration(node):
    """Resolve declared configuration, never expand prompt/instruction text."""
    if isinstance(node, list):
        return [_expand_module_configuration(value) for value in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    result = {}
    for key, value in node.items():
        if key in {"config", "source"}:
            result[key] = expand_environment(value)
        elif key in {"instruction", "instructions", "system_prompt"}:
            result[key] = copy.deepcopy(value)
        else:
            result[key] = _expand_module_configuration(value)
    if result.get("module", "").startswith("provider-") and result.get("id") and not result.get("instance_id"):
        result["instance_id"] = result["id"]
    return result


def _apply_host_policy(bundle, config):
    """Host filesystem boundaries also apply to a portable snapshot's tools."""
    settings = config.settings
    policy_keys = {"allowed_write_paths", "denied_write_paths"}
    def apply(rows):
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or row.get("module") != "tool-filesystem":
                continue
            generic = settings.get("overrides", {}).get("tool-filesystem", {}).get("config", {})
            specific = settings.get("overrides", {}).get(row.get("id") or row.get("instance_id"), {}).get("config", {})
            policy = {key:value for key,value in {**generic, **specific}.items() if key in policy_keys}
            if policy:
                row["config"] = merge(row.get("config", {}), expand_environment(policy))
    def agents(values):
        for agent in values.values():
            if isinstance(agent, dict):
                apply(agent.get("tools", []))
                agents(agent.get("agents", {}))
    apply(bundle.tools)
    agents(bundle.agents)
    return bundle


async def compose_configured_bundle(registry, loaded, config):
    """Snapshots are complete plans; ordinary roots inherit host composition."""
    snapshot = is_snapshot(loaded)
    if not snapshot:
        for behavior in config.app_bundles:
            loaded = loaded.compose(await registry.load(behavior))
        if config.settings.get("routing") and not any(row.get("module") == "hooks-routing" for row in loaded.hooks):
            loaded = loaded.compose(await registry.load("git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=behaviors/routing.yaml"))
        loaded = _apply_settings(loaded, config)
    # Credentials and safety policy remain local host responsibilities. They
    # do not add modules or replace the saved source/model/routing selections.
    loaded = _apply_host_policy(loaded, config)
    for key in (("providers", "tools", "hooks", "session", "agents") if snapshot else ("session", "agents")):
        setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    return loaded


def module_source(config, snapshot, module, source):
    if module == "loop-live":
        return LOOP_SOURCE
    return source if snapshot else config.module_sources.get(module) or source


async def prepare_manager(workspace, *, runtime=None, bundle=None, background_delegate=True,
                          ask=None, report_dir=None, resume=False, selection=None,
                          application_host="Amplifier Unified", **kwargs):
    from amplifier_foundation import BundleRegistry, SessionConfigurator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_loop_live.job_store import JobStore
    from amplifier_core import HookResult
    from .approvals import Approvals
    from .children import install_children, StandaloneHostAdapter
    from .storage import SessionStore

    config = load_config(workspace)
    # One process owns each session, including cwd and Foundation's source cache.
    # No reads/writes of former-host settings happen after the import above.
    os.chdir(config.workspace)
    os.environ["AMPLIFIER_HOME"] = str(config.registry_home)
    runtime = runtime or Runtime()
    chosen = bundle or config.active_bundle
    directory = Path(report_dir or config.home / "runtime-reports" / runtime.session_id)
    registry = BundleRegistry(home=config.registry_home, strict=True, include_source_resolver=config.resolve_source)
    registrations = dict(config.registrations)
    if "foundation" in registry.list_registered():
        registrations.pop("foundation", None)
    registry.register(registrations)
    # Search app-owned named bundle files before registry aliases. Direct paths
    # are resolved from the chosen workspace, matching community bundle usage.
    candidate = Path(chosen).expanduser()
    if not candidate.is_absolute():
        candidate = config.workspace / candidate
    if candidate.exists():
        chosen = str(candidate)
    else:
        for path in (config.home / "bundles" / chosen, config.home / "bundles" / (chosen + ".md"),
                     config.workspace / ".amplifier-unified" / "bundles" / chosen):
            if path.exists():
                chosen = str(path)
                break
    loaded = await registry.load(chosen)
    snapshot = is_snapshot(loaded)
    loaded = await compose_configured_bundle(registry, loaded, config)
    from ..runtime_controls import override_path, validate_plan
    edited_path = override_path(runtime.session_id)
    if edited_path.exists():
        edited = json.loads(edited_path.read_text())
        validate_plan(edited)
        for key in ("providers", "tools", "hooks"):
            if key in edited:
                setattr(loaded, key, [{k:v for k,v in row.items() if k != "enabled"}
                    for row in edited[key] if row.get("enabled", True)])
        for key in ("session", "agents", "context", "instruction"):
            if key in edited:
                setattr(loaded, key, edited[key])
        loaded = _apply_host_policy(loaded, config)
        for key in ("providers", "tools", "hooks", "session", "agents"):
            if key in edited:
                setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    baseline = loaded.to_mount_plan()
    adapted, replacements = live_plan(baseline, background_delegate)
    # Modify the public Bundle fields before prepare(): loop-live and every
    # agent-specific source go through Foundation's normal activation mechanism.
    loaded.session = adapted["session"]
    loaded.agents = adapted.get("agents", {})
    write_private(directory / "baseline-mount-plan.json", json.dumps(redact(baseline), indent=2, default=str))
    write_private(directory / "live-mount-plan.json", json.dumps(redact(adapted), indent=2, default=str))
    def progress(action, detail):
        # The callback's detail can be a URL with credentials. Publish only the
        # documented phase, never arbitrary source strings or installer logs.
        if runtime.observer:
            runtime.observer({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Preparing community modules (" + str(action).replace("_", " ")[:60] + ")."})
    prepared = await loaded.prepare(strict=True,
        source_resolver=lambda module, source: module_source(config, snapshot, module, source),
        progress_callback=progress)
    prepared.mount_plan.update(application_host=application_host, root_session_id=runtime.session_id,
        bundle_name=bundle or config.active_bundle, project_dir=str(config.workspace), project_name=config.workspace.name)
    store = SessionStore(config.home / "sessions")
    saved = store.load(runtime.session_id) if resume else None
    if resume and saved is None:
        saved = store.import_cli(runtime.session_id, workspace=config.workspace)
    messages = saved[0] if saved else None
    jobs = JobStore(store.base_dir / runtime.session_id / "live-jobs")
    recovered = []
    if jobs.rows:
        if not resume:
            jobs.close()
            raise RuntimeError("Existing job evidence requires explicit resume")
        messages, recovered = jobs.recover(messages or [])
    if messages is not None:
        messages = repair_interrupted_receipts(messages)
    approvals = Approvals(runtime, ask)
    session = None
    try:
        session = await prepared.create_session(session_id=runtime.session_id,
            session_cwd=config.workspace, approval_system=approvals, is_resumed=messages is not None)
        coordinator = session.coordinator
        coordinator.register_capability("live.runtime", runtime)
        coordinator.register_capability("live.jobs", jobs)
        coordinator.register_capability("live.recovered_jobs", [row["job_id"] for row in recovered])
        adapter = StandaloneHostAdapter()
        coordinator.register_capability("live.host", adapter)
        register = coordinator.get_capability("approval.register_provider")
        if register:
            register(approvals)
        if messages is not None:
            await coordinator.get("context").set_messages(messages)
        await install_children(session, prepared, runtime, store, approvals, host_adapter=adapter)
        configurator = SessionConfigurator(session, prepared)
        coordinator.register_capability("web.configurator", configurator)
        coordinator.register_capability("web.prepared", prepared)
        coordinator.register_capability("web.registry", registry)
        if not snapshot:
            await configurator.apply_saved_settings(config.settings.get("configurator") or {})
        configurator.take_snapshot()
        providers = coordinator.get("providers") or {}
        if not providers:
            raise RuntimeError("No provider is configured. Add config.providers to the app-owned config/settings.yaml.")
        loop = coordinator.get("orchestrator")
        if selection:
            identity = selection.get("instance")
            if identity not in providers or not selection.get("model"):
                raise ValueError("Root selection needs an available provider instance and model")
            loop.root_provider = SelectedProvider(providers[identity], selection)
        selected = loop._select_provider(providers)
        choices = []
        for identity, provider in providers.items():
            info = provider.get_info()
            if inspect.isawaitable(info):
                info = await info
            defaults = getattr(info, "defaults", {}) or {}
            choices.append({"id": identity, "provider": getattr(info, "id", identity),
                "model": defaults.get("model"), "effort": defaults.get("reasoning_effort"), "models": []})
        effective = selection or next((row for row in choices if providers[row["id"]] is selected), None)
        metadata = {**({key:saved[1][key] for key in ("fork","preserve_system") if key in saved[1]} if saved else {}),
            "session_id": runtime.session_id, "parent_id": None,
            "bundle_name": bundle or config.active_bundle, "working_dir": str(config.workspace),
            "created": (saved[1].get("created") if saved else None) or datetime.now(UTC).isoformat(),
            "application_host": application_host, "config": redact(session.config)}
        async def checkpoint(status="in_progress"):
            persist_controls = coordinator.get_capability("web.controls.persist")
            if persist_controls:
                persist_controls()
            transcript = await coordinator.get("context").get_messages()
            store.save(runtime.session_id, transcript, {**metadata, "status": status,
                "last_updated": datetime.now(UTC).isoformat(),
                "turn_count": sum(row.get("role") == "user" for row in transcript)})
        coordinator.register_capability("live.checkpoint", checkpoint)
        async def checkpoint_hook(event, data):
            if data.get("session_id", runtime.session_id) == runtime.session_id:
                await checkpoint()
            return HookResult()
        for event in ("tool:post", "tool:error", "orchestrator:complete"):
            coordinator.hooks.register(event, checkpoint_hook, name="unified-checkpoint-" + event)
        async def cleanup_jobs():
            jobs.close()
        coordinator.register_cleanup(cleanup_jobs)
        await checkpoint()
        failures = getattr(loop, "load_failures", [])
        if failures:
            write_private(directory / "module-load-failures.json", json.dumps(redact(failures), indent=2, default=str))
            raise RuntimeError("Configured modules failed to mount: " + ", ".join(str(row.get("module_id", row.get("module", "unknown"))) for row in failures))
        report = {"bundle": bundle or config.active_bundle, "workspace": str(config.workspace),
            "session_id": runtime.session_id, "resumed": messages is not None,
            "providers": list(providers), "tools": list(coordinator.get("tools") or {}),
            "agents": list(prepared.mount_plan.get("agents", {})), "provider_choices": choices,
            "selection": selection, "effective_selection": effective, "replaced": replacements,
            "steering": "request_boundary", "native": False, "standalone": True,
            "settings_file": str(config.home / "config" / "settings.yaml"),
            "capabilities": {name: coordinator.get_capability(name) is not None for name in
                ("session.spawn", "session.resume", "mention_resolver", "model_role_resolver")},
            "module_load_failures": failures}
        write_private(directory / "mounted.json", json.dumps(redact(report), indent=2, default=str))
        registry.save()
        return session, runtime, report
    except BaseException:
        if session:
            await session.cleanup()
        jobs.close()
        raise
