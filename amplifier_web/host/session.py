"""Standalone application session policy using public Foundation primitives."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
import json
import os
from pathlib import Path
import stat

from .config import HostConfig, expand_environment, load_config, merge, write_private
from .components import HostComponents, ComponentResolver, compose_bundles, installed_package_source
from ..provider_environment import iter_provider_rows, materialize_bundle_providers

LOOP_SOURCE = "git+https://github.com/microsoft/amplifier-module-loop-live@main"


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
        context = node.get("session", {}).get("context", {})
        if context.get("module") == "context-managed" and context.get("config", {}).get("engine") == "boundary":
            context.setdefault("config", {}).setdefault("durable_checkpoints", True)
        loop = node.get("session", {}).get("orchestrator", {})
        if loop.get("module") in {"loop-streaming", "loop-live"}:
            original = loop["module"]
            loop.update(module="loop-live", source=LOOP_SOURCE)
            loop.setdefault("config", {}).update(configured_bundle=True)
            loop["config"].setdefault("background_delegate", background_delegate)
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


class NativeTranscriptConflict(RuntimeError):
    """An older host changed native history outside the shared lock."""


class _NativeTranscriptGuard:
    """Fail closed when native history changes after admission.

    Foundation's lock coordinates participating hosts. Older CLI releases do
    not use it, so their native file must also be checked before each write.
    This is conflict detection, not a lock over non-cooperating processes.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()

    @staticmethod
    def conflict():
        return NativeTranscriptConflict(
            "The native CLI transcript changed outside this session's shared lock. "
            "Both histories were preserved. Resolve the conflicting transcript before continuing this session.")

    def _stamp(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise NativeTranscriptConflict("The native CLI transcript is not a regular file; it was not changed.")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _companions(self):
        values = []
        for name in ('transcript.jsonl.backup',):
            path = self.path.with_name(name)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise NativeTranscriptConflict('Native session history must use regular files; nothing was changed.')
                values.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                values.append(None)
        return tuple(values)

    def check(self):
        if self._stamp() != self.stamp or self._companions() != self.companion_stamps:
            raise self.conflict()

    def saved(self):
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()


class SelectedProvider:
    """Root-only model preference; worker routing remains bundle-owned."""
    def __init__(self, provider, selection, execution_adapter=None):
        self.original, self.selection = provider, selection
        self.execution_adapter = execution_adapter
        self._selected_requests = []
    def _execution_provider(self):
        return self.execution_adapter(self.original) if callable(self.execution_adapter) else self.original
    def __getattr__(self, name):
        method = getattr(self._execution_provider() if name in {'stream','request_budget'} else self.original, name)
        if name in {"stream", "request_budget"} and callable(method):
            # Keep feature detection honest: providers without stream still
            # raise AttributeError, while streaming providers receive the same
            # root-only overrides as complete().
            def selected_stream(request, **kwargs):
                selected, kwargs = self._selected_request(request, kwargs, consume=name == "stream")
                if name == "request_budget":
                    # The budget protocol carries completion overrides in
                    # request_options. Anthropic deliberately has no **kwargs.
                    # Inspect the original provider, before surface adapters.
                    parameters = inspect.signature(self.original.request_budget).parameters
                    if "request_options" in parameters:
                        options = dict(kwargs.get("request_options") or {})
                        options.update({key: kwargs.pop(key) for key in ("model", "reasoning_effort") if key in kwargs})
                        kwargs["request_options"] = options
                try:
                    result = method(selected, **kwargs)
                except BaseException:
                    self._forget_request(request)
                    raise
                if name == "request_budget" and inspect.isawaitable(result):
                    async def budget():
                        try:
                            return await result
                        except BaseException:
                            self._forget_request(request)
                            raise
                    return budget()
                return result
            return selected_stream
        return method
    def get_info(self):
        info = self.original.get_info()
        overrides = {key:self.selection[key] for key in ("model", "max_output_tokens") if key in self.selection}
        if self.selection.get("effort") is not None:
            overrides["reasoning_effort"] = self.selection["effort"]
        return info.model_copy(update={"defaults": {**info.defaults, **overrides}})
    def _forget_request(self, request):
        self._selected_requests = [row for row in self._selected_requests if row[0] is not request]

    def _selected_request(self, request, kwargs, *, consume=False):
        updates = {key:self.selection[key] for key in ("model", "max_output_tokens") if key in self.selection}
        # Community providers may read the model keyword rather than the
        # portable request field. Supply both without changing worker defaults.
        if "model" in self.selection:
            kwargs["model"] = self.selection["model"]
        effort = self.selection.get("effort")
        metadata = getattr(request, "metadata", None) or {}
        if metadata.get("purpose") == "context-compaction" and request.reasoning_effort is not None:
            # Summaries have their own effort budget. Keep the same explicit
            # value in preflight and dispatch, including keyword-only providers.
            effort = request.reasoning_effort
        if effort is not None:
            updates["reasoning_effort"] = effort
            kwargs["reasoning_effort"] = effort
        # Budget and dispatch must share one selected request so the host's
        # surface adapter can reuse its prepared observation. Retain a bounded
        # snapshot to reject in-place input/selection changes, then consume the
        # identity on dispatch; retries and later calls prepare afresh.
        selected = next((value for source, snapshot, choices, value in self._selected_requests
                         if source is request and choices == updates and snapshot == request), None)
        if selected is None:
            selected = request.model_copy(update=updates)
            self._forget_request(request)
            if not consume:
                self._selected_requests = (self._selected_requests + [
                    (request, copy.deepcopy(request), dict(updates), selected)])[-4:]
        if consume:
            self._forget_request(request)
        return selected, kwargs

    async def complete(self, request, **kwargs):
        request, kwargs = self._selected_request(request, kwargs, consume=True)
        return await self._execution_provider().complete(request, **kwargs)


_copilot_credential = None

def apply_provider_environment(plan):
    """The pinned Copilot SDK resolves its token from process env, not config.

    Workers are isolated per root session. Keep that process consistent rather
    than allowing an ambient default to override an explicit credential choice.
    """
    global _copilot_credential
    tokens={row.get('config',{}).get('github_token') for row in iter_provider_rows(plan)
            if row.get('module')=='provider-github-copilot' and row.get('enabled',True)}-{None,''}
    if len(tokens)>1 or (tokens and _copilot_credential is not None and _copilot_credential not in tokens):
        raise ValueError('Different Copilot credentials need separate conversations; this SDK shares authentication within one session process.')
    if tokens:
        _copilot_credential=next(iter(tokens))
        os.environ['COPILOT_AGENT_TOKEN']=_copilot_credential


def _apply_settings(bundle, config):
    settings = config.settings
    # Generic list merging replaces config arrays. Keep the bundle's explicit
    # filesystem denials across that merge so host policy can enforce them.
    filesystem_denials = {
        row.get("id") or row.get("instance_id") or row["module"]:
            copy.deepcopy(row["config"]["denied_write_paths"])
        for row in bundle.tools
        if row.get("module") == "tool-filesystem" and "denied_write_paths" in row.get("config", {})
    }
    bundle.providers = merge(bundle.providers, config.providers)
    bundle.providers.sort(key=lambda row: row.get("config", {}).get("priority", 100))
    for kind in ("tools", "hooks"):
        values = merge(getattr(bundle, kind), settings.get("modules", {}).get(kind, []))
        values = merge(values, settings.get("config", {}).get(kind, []))
        setattr(bundle, kind, values)
    bundle.session = merge(bundle.session, settings.get("config", {}).get("session", {}))
    routing = settings.get("routing", {})
    if routing or any(row.get("module") == "hooks-routing" for row in bundle.hooks):
        from ..shared_settings import routing_dirs
        for hook in bundle.hooks:
            if hook.get("module") == "hooks-routing":
                patch = {"custom_routing_dirs": [str(path) for path in routing_dirs(config.workspace, shared_home=getattr(config, "config_home", None), global_only=getattr(config, "global_only", False))]}
                if routing.get("matrix"):
                    patch["default_matrix"] = routing["matrix"]
                if routing.get("overrides"):
                    patch["overrides"] = routing["overrides"]
                hook["config"] = merge(hook.get("config", {}), patch)
    overrides = settings.get("overrides", {})
    for kind in ("providers", "tools", "hooks"):
        values = []
        for row in getattr(bundle, kind):
            override = merge(overrides.get(row.get("module"), {}), overrides.get(row.get("id") or row.get("instance_id"), {}))
            if override.get("enabled") is False or (kind == "providers" and (row.get("id") or row.get("instance_id") or row["module"].removeprefix("provider-")) in settings.get("configurator", {}).get("disabled", {}).get("providers", [])):
                continue
            row = merge(row, {key:value for key,value in override.items() if key in {"source", "config"}})
            identity = row.get("id") or row.get("instance_id") or row.get("module")
            if kind == "tools" and row.get("module") == "tool-filesystem" and identity in filesystem_denials:
                original = filesystem_denials[identity]
                effective = row.get("config", {}).get("denied_write_paths", [])
                if any(not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                       for paths in (original, effective)):
                    raise ValueError('File-access paths must be lists of strings.')
                row.setdefault("config", {})["denied_write_paths"] = list(dict.fromkeys(original + effective))
            if kind == "providers" and row.get("id") and not row.get("instance_id"):
                row["instance_id"] = row["id"]
            if kind == "providers":
                if "source" in row:
                    row["source"] = expand_environment(row["source"])
                values.append(row)
            else:
                values.append(expand_environment(row))
        setattr(bundle, kind, values)
    # Context and orchestrator are modules too (e.g. context-simple.token_meter).
    for kind in ("context", "orchestrator"):
        row = bundle.session.get(kind)
        if isinstance(row, dict):
            override = overrides.get(row.get("module"), {})
            bundle.session[kind] = merge(row, {key: value for key, value in override.items() if key in {"source", "config"}})
    return bundle


def is_snapshot(bundle):
    # Foundation preserves the public SemVer version field; arbitrary metadata
    # keys are discarded on load. Build metadata is portable to other hosts.
    from ..bundles import SNAPSHOT_VERSION
    return getattr(bundle, "version", None) == SNAPSHOT_VERSION


def _expand_module_configuration(node, in_provider=False):
    """Resolve declared configuration, leaving provider config for schema materialization."""
    if isinstance(node, list):
        return [_expand_module_configuration(value, in_provider=in_provider) for value in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    provider = in_provider or node.get("module", "").startswith("provider-")
    result = {}
    for key, value in node.items():
        if key in {"config", "source"}:
            result[key] = copy.deepcopy(value) if provider and key == "config" else expand_environment(value)
        elif key in {"instruction", "instructions", "system_prompt"}:
            result[key] = copy.deepcopy(value)
        else:
            result[key] = _expand_module_configuration(value, in_provider=provider)
    if result.get("module", "").startswith("provider-") and result.get("id") and not result.get("instance_id"):
        result["instance_id"] = result["id"]
    return result


def _apply_host_policy(bundle, config, *, execution_workspace=None):
    """Host write boundaries cover filesystem and patch tools, including snapshots."""
    settings = config.settings
    # A managed checkout can differ from the immutable history/config workspace.
    # Resolve only relative write policies against the actual execution folder.
    workspace = str(Path(execution_workspace or config.workspace).expanduser().resolve())
    policy_keys = {"allowed_write_paths", "denied_write_paths"}
    def paths(values):
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('File-access paths must be lists of strings.')
        result = []
        for value in values:
            path = Path(value).expanduser()
            result.append((path if path.is_absolute() else Path(workspace) / path).resolve())
        return result

    # Settings support both legacy module lists and current config/overrides.
    # Resolve their precedence before sharing any writer's effective boundary.
    configured = merge(settings.get("modules", {}).get("tools", []), settings.get("config", {}).get("tools", []))
    policies = {"tool-filesystem": {}}
    overrides = settings.get("overrides", {})
    def discover(rows, children):
        for row in rows:
            if isinstance(row, dict) and row.get('module') == 'tool-filesystem':
                policies.setdefault(row.get('id') or row.get('instance_id') or 'tool-filesystem', {})
        for child in children.values():
            if isinstance(child, dict):
                discover(child.get('tools', []), child.get('agents', {}))
    discover(bundle.tools, bundle.agents)
    for row in configured:
        if isinstance(row, dict) and row.get("module") == "tool-filesystem":
            policies[row.get("id") or row.get("instance_id") or "tool-filesystem"] = row.get("config", {})
    for identity, values in policies.items():
        values = merge(values, overrides.get("tool-filesystem", {}).get("config", {}))
        if identity != "tool-filesystem":
            values = merge(values, overrides.get(identity, {}).get("config", {}))
        policy = expand_environment({key: value for key, value in values.items() if key in policy_keys})
        # Match the CLI's _ensure_cwd_in_write_paths policy. Shared allowed
        # directories extend project access; they must not replace it. Use an
        # absolute session workspace, never the server process's directory.
        if "allowed_write_paths" in policy:
            policy["allowed_write_paths"] = list(dict.fromkeys([workspace, *(str(path) for path in paths(policy["allowed_write_paths"]))]))
        policies[identity] = policy

    def restrict(current, policy):
        policy = copy.deepcopy(policy)
        if "allowed_write_paths" in policy and "allowed_write_paths" in current:
            shared, patch = paths(policy['allowed_write_paths']), paths(current['allowed_write_paths'])
            policy['allowed_write_paths'] = list(dict.fromkeys(str(a if a.is_relative_to(b) else b)
                for a in shared for b in patch if a.is_relative_to(b) or b.is_relative_to(a)))
        elif "allowed_write_paths" in policy:
            policy['allowed_write_paths'] = [str(path) for path in paths(policy['allowed_write_paths'])]
        if "denied_write_paths" in policy:
            policy['denied_write_paths'] = list(dict.fromkeys(str(path) for path in
                paths(current.get('denied_write_paths', [])) + paths(policy['denied_write_paths'])))
        return merge(current, policy)

    def apply(rows):
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or row.get("module") not in {"tool-filesystem", "tool-apply-patch"}:
                continue
            current = copy.deepcopy(row.get("config", {}))
            # Snapshot and child module settings have not been expanded yet.
            # Normalize paths only after resolving their environment references.
            for key in policy_keys & current.keys():
                current[key] = expand_environment(current[key])
            if row["module"] == "tool-filesystem":
                identity = row.get("id") or row.get("instance_id") or "tool-filesystem"
                policy = policies.get(identity, policies["tool-filesystem"])
                # Keep the existing instance-override semantics for snapshots.
                specific = overrides.get(identity, {}).get("config", {})
                policy = merge(policy, expand_environment({key: value for key, value in specific.items() if key in policy_keys}))
                row["config"] = merge(current, policy)
                row["config"]["allowed_write_paths"] = list(dict.fromkeys([workspace,
                    *(str(path) for path in paths(row["config"].get("allowed_write_paths", [])))]))
                if "denied_write_paths" in row["config"]:
                    # Shared settings may add restrictions, but must not erase
                    # the declaration's explicitly denied directories.
                    row["config"]["denied_write_paths"] = list(dict.fromkeys(str(path) for path in
                        paths(current.get("denied_write_paths", [])) + paths(policy.get("denied_write_paths", []))))
            else:
                # Intersect each effective filesystem policy with any explicitly
                # narrower patch policy, retaining every denied subtree.
                for policy in policies.values():
                    if policy:
                        current = restrict(current, policy)
                row["config"] = current
    def agents(values):
        for agent in values.values():
            if isinstance(agent, dict):
                apply(agent.get("tools", []))
                agents(agent.get("agents", {}))
    apply(bundle.tools)
    agents(bundle.agents)
    return bundle


async def compose_configured_bundle(registry, loaded, config, *, execution_workspace=None):
    """Snapshots are complete plans; ordinary roots inherit host composition."""
    snapshot = is_snapshot(loaded)
    components = required_components()
    if not snapshot:
        from ..builtin_behaviors import resolve_builtin_behavior
        for behavior in config.app_bundles:
            selected, _ = await load_configured_bundle(registry, config, resolve_builtin_behavior(behavior))
            components.select_bundle(selected, config.module_sources)
            loaded = compose_bundles(loaded, selected)
        if not any(row.get('module') == 'hook-context-intelligence' for row in loaded.hooks):
            from ..session_files import capture_dir, project_slug
            # The community hook owns kernel capture, discovery and metadata.
            # App destinations are explicit; ambient server keys never enable
            # forwarding merely because this default hook is present.
            loaded.hooks.append({'module': 'hook-context-intelligence',
                'source': 'git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/hook-context-intelligence',
                'config': {'destinations': {}, 'base_path': str(capture_dir(config.workspace, 'root').parents[3]),
                           'project_slug': project_slug(config.workspace),
                           'additional_events': ['delegate:agent_spawned', 'delegate:agent_resumed', 'delegate:agent_completed', 'delegate:agent_cancelled', 'delegate:error']}})
        if config.settings.get("routing") and not any(row.get("module") == "hooks-routing" for row in loaded.hooks):
            selected, _ = await load_configured_bundle(registry, config, "git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=behaviors/routing.yaml")
            components.select_bundle(selected, config.module_sources)
            loaded = compose_bundles(loaded, selected)
        loaded = _apply_settings(loaded, config)
        # Existing root defaults are explicit selections, not a new forced
        # version. Reuse their effective source for child/sibling declarations.
        for row in loaded.hooks:
            if row.get("module") in {"hook-context-intelligence", "hooks-routing"}:
                components.select(row["module"], config.module_sources.get(row["module"]) or row.get("source"))
    loaded = components.apply(loaded)
    # Credentials and safety policy remain local host responsibilities. They
    # do not add modules or replace the saved source/model/routing selections.
    loaded = _apply_host_policy(loaded, config, execution_workspace=execution_workspace)
    for key in (("providers", "tools", "hooks", "session", "agents") if snapshot else ("session", "agents")):
        setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    return loaded


def installed_loop_source():
    return installed_package_source('amplifier-module-loop-live', 'amplifier_module_loop_live')


def required_components():
    return HostComponents({'loop-live': LOOP_SOURCE}, {'loop-live': installed_loop_source})


def module_source(config, snapshot, module, source, components=None):
    components = components or required_components()
    selected = components.source(module, source, installed=True)
    if components.owns(module, source):
        return selected
    return source if snapshot else config.module_sources.get(module) or source


async def load_configured_bundle(registry, config, reference):
    """Apply the same source selections to roots and app behaviors as includes."""
    replacement = config.resolve_source(reference)
    if replacement is None:
        registered = registry.find(reference)
        if registered:
            replacement = config.resolve_source(registered)
    reference = replacement or reference
    from .bundle_paths import local_bundle_path
    candidate = local_bundle_path(config, reference)
    chosen = str(candidate) if candidate is not None else reference
    return await registry.load(chosen), chosen


async def load_root_bundle(config, chosen, *, execution_workspace=None):
    """Compose in a session-local registry view; settings own registrations."""
    from amplifier_foundation import BundleRegistry
    registry = BundleRegistry(home=config.registry_home, strict=True,
        include_source_resolver=config.resolve_source, persist=False)
    registrations = dict(config.registrations)
    explicit = {**config.settings.get('bundle', {}).get('added', {}),
                **config.settings.get('sources', {}).get('bundles', {})}
    if "foundation" in registry.list_registered() and "foundation" not in explicit:
        registrations.pop("foundation", None)
    registry.register(registrations)
    loaded, chosen = await load_configured_bundle(registry, config, chosen)
    loaded = await compose_configured_bundle(registry, loaded, config, execution_workspace=execution_workspace)
    return registry, loaded, chosen


@dataclass
class ResolvedRoot:
    """A single apply's validated composition, never a cross-request cache."""
    config: HostConfig
    bundle: str
    root: tuple | None
    execution_workspace: Path | str | None = None

    def __post_init__(self):
        self.execution_workspace = Path(self.execution_workspace or self.config.workspace).expanduser().resolve()

    def take(self, config, bundle, *, execution_workspace=None):
        workspace = Path(execution_workspace or config.workspace).expanduser().resolve()
        if self.root is None or config != self.config or bundle != self.bundle or workspace != self.execution_workspace:
            raise ValueError("The bundle configuration changed while switching. Try again.")
        root, self.root = self.root, None
        return root


async def prepare_dependencies(workspace, *, bundle=None, install_overrides=None):
    """Prepare a fresh qualification worker before importing its live runtime.

    This first probe only installs configured dependencies. A separate process
    mounts the resulting frozen worker normally; source changes never mix an
    already imported wheel runtime with an editable cache in one interpreter.
    No conversation, history store, job recovery or model execution is opened.
    """
    config = load_config(workspace)
    from .config import prepare_registry
    prepare_registry(config)
    execution_workspace = Path(config.workspace).expanduser().resolve(strict=True)
    os.chdir(execution_workspace)
    _, loaded, _ = await load_root_bundle(config, bundle or config.active_bundle,
                                          execution_workspace=execution_workspace)
    snapshot = is_snapshot(loaded)
    adapted, _ = live_plan(loaded.to_mount_plan())
    components = getattr(loaded, '_host_components', None) or required_components()
    loaded.session = adapted['session']
    loaded.agents = adapted.get('agents', {})
    components.apply(loaded)
    await loaded.prepare(strict=True, refresh_dependencies=True,
        **({'install_overrides': Path(install_overrides)} if install_overrides is not None else {}),
        cache_dir=config.registry_home / 'cache',
        source_resolver=lambda module, source: module_source(config, snapshot, module, source, components))


async def prepare_manager(workspace, *, runtime=None, bundle=None, background_delegate=True,
                          ask=None, report_dir=None, resume=False, selection=None,
                          application_host="Amplifier Unified", shared_handle=None,
                          shared_handle_getter=None, shared_snapshot=None,
                          write_guard=None, resolved_root=None, execution_workspace=None,
                          refresh_dependencies=False, install_overrides=None, **kwargs):
    if refresh_dependencies and resume:
        raise ValueError("Dependency refresh is limited to a new isolated qualification session.")
    from amplifier_foundation import SessionConfigurator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_loop_live.job_store import JobStore
    from amplifier_core import HookResult
    from .approvals import Approvals
    from .children import install_children, StandaloneHostAdapter
    from .storage import SessionStore

    runtime = runtime or Runtime()
    config = load_config(workspace, session_id=runtime.session_id)
    from .config import prepare_registry
    prepare_registry(config)
    # Registry/cache ownership is passed explicitly below. AMPLIFIER_HOME must
    # remain the community data root, including for mounted CI logging hooks.
    execution_workspace = Path(execution_workspace or config.workspace).expanduser().resolve(strict=True)
    os.chdir(execution_workspace)
    from ..session_files import capture_dir
    if not os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'):
        os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'] = str(capture_dir(config.workspace, 'root').parents[3])
    store = SessionStore.for_app(config.home, config.workspace)
    # Native history is authoritative, including an intentionally empty file.
    # A common checkpoint is read only for an explicitly legacy-only session;
    # a stale or corrupt checkpoint cannot prevent opening valid native history.
    native_guard = _NativeTranscriptGuard(store.directory(runtime.session_id) / "transcript.jsonl")
    saved = store.load(runtime.session_id) if resume else None
    history_source = "native" if saved is not None else "new"
    if resume and saved is None:
        if shared_snapshot is None and shared_handle is not None:
            shared_snapshot = shared_handle.read()
        if shared_snapshot is not None:
            def legacy_value(name, default=None):
                return shared_snapshot.get(name, default) if isinstance(shared_snapshot, dict) else getattr(shared_snapshot, name, default)
            messages, metadata = legacy_value("messages"), legacy_value("metadata", {})
            if not isinstance(messages, list) or not isinstance(metadata, dict):
                raise ValueError("The legacy shared session checkpoint is malformed.")
            saved = messages, {**metadata, "bundle": legacy_value("bundle")}
            history_source = "legacy-checkpoint"
        else:
            store._migrate(runtime.session_id)
            # Migration intentionally writes once; take its stamp before the
            # read so a concurrent older CLI write cannot become our baseline.
            native_guard.saved()
            saved = store.load(runtime.session_id)
            if saved is None and shared_handle is None:
                saved = store.import_cli(runtime.session_id, workspace=config.workspace)
            if saved is not None:
                history_source = "native"
    native_guard.check()
    saved_bundle = (saved[1].get("bundle_name") or saved[1].get("bundle")) if saved else None
    if isinstance(saved_bundle, str):
        saved_bundle = saved_bundle.removeprefix("bundle:")
    if saved_bundle is not None and (not isinstance(saved_bundle, str) or not saved_bundle.strip()):
        raise ValueError("The saved session has no resolvable bundle.")
    chosen = saved_bundle or bundle or config.active_bundle
    bundle_identity = chosen
    directory = Path(report_dir or config.home / "runtime-reports" / runtime.session_id)
    registry, loaded, chosen = (resolved_root.take(config, chosen, execution_workspace=execution_workspace) if resolved_root is not None
                               else await load_root_bundle(config, chosen, execution_workspace=execution_workspace))
    snapshot = is_snapshot(loaded)
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
        loaded = _apply_host_policy(loaded, config, execution_workspace=execution_workspace)
        for key in ("providers", "tools", "hooks", "session", "agents"):
            if key in edited:
                setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    baseline = loaded.to_mount_plan()
    adapted, replacements = live_plan(baseline, background_delegate)
    # Modify the public Bundle fields before prepare(): loop-live and every
    # agent-specific source go through Foundation's normal activation mechanism.
    loaded.session = adapted["session"]
    loaded.agents = adapted.get("agents", {})
    components = getattr(loaded, "_host_components", None) or required_components()
    components.apply(loaded)
    adapted = components.normalize(adapted)
    write_private(directory / "baseline-mount-plan.json", json.dumps(redact(baseline), indent=2, default=str))
    write_private(directory / "live-mount-plan.json", json.dumps(redact(adapted), indent=2, default=str))
    def progress(action, detail):
        # The callback's detail can be a URL with credentials. Publish only the
        # documented phase, never arbitrary source strings or installer logs.
        if runtime.observer:
            runtime.observer({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Preparing community modules (" + str(action).replace("_", " ")[:60] + ")."})
    preparation_policy = {}
    if refresh_dependencies:
        preparation_policy["refresh_dependencies"] = True
    if install_overrides is not None:
        preparation_policy["install_overrides"] = Path(install_overrides)
    # Activate modules from the same generation as the bundle registry. The
    # shared AMPLIFIER_HOME still owns history/settings, not app module caches.
    prepared = await loaded.prepare(strict=True, **preparation_policy,
        cache_dir=config.registry_home / "cache",
        source_resolver=lambda module, source: module_source(config, snapshot, module, source, components),
        progress_callback=progress)
    if getattr(prepared, "resolver", None) is not None:
        prepared.resolver = ComponentResolver(prepared.resolver, components)
    await materialize_bundle_providers(loaded, prepared)
    apply_provider_environment(prepared.mount_plan)
    from ..session_files import project_slug
    prepared.mount_plan.update(application_host=application_host, root_session_id=runtime.session_id,
        project_slug=project_slug(config.workspace),
        bundle_name=chosen, project_dir=str(config.workspace), project_name=config.workspace.name)
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
            session_cwd=execution_workspace, approval_system=approvals, is_resumed=messages is not None)
        coordinator = session.coordinator
        coordinator.register_capability('web.history_workspace', str(config.workspace))
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
            raise RuntimeError("No provider is configured. Add config.providers to the shared Amplifier settings.yaml.")
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
                "display_name": getattr(info, "display_name", None),
                "model": defaults.get("model"), "effort": defaults.get("reasoning_effort"), "models": []})
        effective = selection or next((row for row in choices if providers[row["id"]] is selected), None)
        metadata = {**({key:saved[1][key] for key in ("fork","preserve_system") if key in saved[1]} if saved else {}),
            "session_id": runtime.session_id, "parent_id": None,
            "bundle_name": bundle_identity, "working_dir": str(config.workspace),
            "created": (saved[1].get("created") if saved else None) or datetime.now(UTC).isoformat(),
            "application_host": application_host, "config": redact(session.config)}
        continuity = None
        async def checkpoint(status="in_progress"):
            if write_guard:
                write_guard()
            persist_controls = coordinator.get_capability("web.controls.persist")
            if persist_controls:
                persist_controls()
            transcript = await coordinator.get("context").get_messages()
            held = shared_handle_getter() if shared_handle_getter else shared_handle
            native_guard.check()
            if held is not None:
                # Cross-host ownership remains Foundation's responsibility;
                # native transcript/metadata are the only newly written history.
                held.check()
            store.save(runtime.session_id, transcript, {**metadata, "status": status,
                "last_updated": datetime.now(UTC).isoformat(),
                "turn_count": sum(row.get("role") == "user" for row in transcript)})
            native_guard.saved()
            if continuity:
                continuity.save()
        coordinator.register_capability("live.checkpoint", checkpoint)
        from ..context_continuity import install as install_continuity
        continuity = install_continuity(coordinator, runtime.session_id, edited_path.parent, checkpoint)
        async def checkpoint_hook(event, data):
            if data.get("session_id", runtime.session_id) == runtime.session_id:
                await checkpoint()
            return HookResult()
        for event in ("tool:post", "tool:error", "orchestrator:complete", "context:compaction_finished"):
            coordinator.hooks.register(event, checkpoint_hook, name="unified-checkpoint-" + event)
        async def cleanup_jobs():
            jobs.close()
        coordinator.register_cleanup(cleanup_jobs)
        await checkpoint()
        failures = getattr(loop, "load_failures", [])
        if failures:
            from ..module_failures import persist_failures
            raise persist_failures(directory, failures)
        from ..module_failures import clear_failures
        clear_failures(directory)
        # Stamp only explicit, local bundle resources actually consumed by this
        # mount. Registry caches and reports are intentionally excluded because
        # they are rewritten by normal preparation.
        # Native history has its own worker stamp; these are additional mount
        # inputs, separate from files changed by normal saves.
        config_inputs = []
        for reference in (chosen, *config.app_bundles):
            path = Path(reference).expanduser()
            if not path.is_absolute():
                path = config.workspace / path
            if path.is_file():
                config_inputs.append(str(path.resolve()))
        report = {"bundle": chosen, "root_bundle": bundle_identity, "workspace": str(config.workspace),
            "contextIntelligence": {"enabled": bool((coordinator.get_capability('context_intelligence._hook_state') or {}).get('unregister_fns'))},
            "session_id": runtime.session_id, "resumed": messages is not None,
            "providers": list(providers), "tools": list(coordinator.get("tools") or {}),
            "agents": list(prepared.mount_plan.get("agents", {})), "provider_choices": choices,
            "selection": selection, "effective_selection": effective, "replaced": replacements,
            "steering": "request_boundary", "native": False, "standalone": True,
            "settings_file": str(config.settings_file),
            "capabilities": {name: coordinator.get_capability(name) is not None for name in
                ("session.spawn", "session.resume", "mention_resolver", "model_role_resolver")},
            "module_load_failures": failures}
        report["config_inputs"] = config_inputs
        report["history_source"] = history_source
        report["execution_workspace"] = str(execution_workspace)
        write_private(directory / "mounted.json", json.dumps(redact(report), indent=2, default=str))
        registry.save()
        return session, runtime, report
    except BaseException:
        if session:
            await session.cleanup()
        jobs.close()
        raise
