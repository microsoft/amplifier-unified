"""Offline Work source composition, not an installed Worker or a new profile.

UNIFIED_WORK_MEDIA_SOURCES names a test-owned JSON receipt mapping preselected
repository names to immutable exports. No fetch/install/source learning here.
The default provider mount is deterministic; the SDK join instead uses the real
public provider mount with a supplied offline client setup. All Work tools,
hooks and agent declarations survive composition; no other tool is called.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from amplifier_core import AmplifierSession
from amplifier_core.loader import ModuleLoader
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources import SimpleSourceResolver
from amplifier_web.app_guidance import install_app_access
from amplifier_web.host import media
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import load_root_bundle, live_plan
from amplifier_web.host.storage import SessionStore
from test_tool_image_consumer_join import FiniteSourceHost

# These expectations describe the offline Work fixture, not media admission.
WORK_TOOLS = (
    "tool-transcript", "tool-skills", "tool-bash", "tool-exec", "tool-web",
    "tool-ui-use", "tool-filesystem", "tool-apply-patch", "tool-search",
    "tool-delegate", "tool-image", "tool-computer-use",
    "tool-context-intelligence-transcript", "tool-mode",
    "tool-context-intelligence-set-filters",
)
WORK_HOOKS = (
    "hook-computer-use", "hooks-mode", "hooks-approval",
    "hook-context-intelligence", "hooks-routing",
)
WORK_AGENTS = {
    "computer-use:computer-operator", "context-intelligence:graph-analyst",
    "context-intelligence:session-navigator", "context-intelligence:server-data-ops",
}

REPOSITORIES = {
    "amplifier-bundle-work": "work", "amplifier-foundation": "foundation",
    "amplifier-bundle-ui-use": "ui_use", "amplifier-bundle-skills": "skills",
    "amplifier-bundle-imagegen": "imagegen", "amplifier-bundle-computer-use": "computer_use",
    "amplifier-bundle-context-intelligence": "context_intelligence",
    "amplifier-bundle-routing-matrix": "routing", "amplifier-bundle-modes": "modes",
}


class WorkLoader(ModuleLoader):
    """Real Core loader; None selects the actual provider public entry point."""
    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    async def load(self, module_id, config=None, source_hint=None, coordinator=None):
        if module_id == "provider-openai" and self.provider is not None:
            async def mount(current):
                await current.mount("providers", self.provider, name="offline")
            return mount
        return await super().load(module_id, config=config, source_hint=source_hint, coordinator=coordinator)


async def compose_work(tmp_path, monkeypatch, *, actual_provider=False):
    receipt = os.environ.get("UNIFIED_WORK_MEDIA_SOURCES")
    if not receipt:
        pytest.skip("Requires explicit immutable Work source exports")
    rows = json.loads(Path(receipt).read_text())
    roots = {name: Path(row["export"]).resolve(strict=True) for name, row in rows.items()}
    for name, row in rows.items():
        for relative, digest in row["files"].items():
            assert hashlib.sha256((roots[name] / relative).read_bytes()).hexdigest() == digest
    for variable, directory in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
        ("AMPLIFIER_WEB_HOME", "app"), ("AMPLIFIER_SESSION_STATE_HOME", "state")):
        path = tmp_path / directory
        path.mkdir(exist_ok=True)
        monkeypatch.setenv(variable, str(path))
    for key in tuple(os.environ):
        if key.startswith(("AMPLIFIER_CONTEXT_INTELLIGENCE_", "AMPLIFIER_COMPUTER_")) or key in {"DISPLAY", "WAYLAND_DISPLAY"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "shared" / "projects"))
    # An empty PATH does not disable Quartz on macOS. Replace only hardware
    # discovery, never a mounted Work module or the file/media execution path.
    import amplifier_module_tool_computer_use as computer
    from amplifier_module_tool_computer_use.registry import NoBackendAvailable
    def no_desktop(*args, **kwargs):
        raise NoBackendAvailable([("offline-fixture", "No desktop target")])
    monkeypatch.setattr(computer, "select_backend", no_desktop)
    # No remaining local desktop/SSH probes can invoke installed native binaries.
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    monkeypatch.chdir(workspace)
    async def resolve(resolver, uri):
        if uri.startswith("git+https://github.com/microsoft/"):
            parsed = urlsplit(uri.removeprefix("git+"))
            repo = parsed.path.rsplit("/", 1)[-1].split("@", 1)[0].removesuffix(".git")
            assert repo in REPOSITORIES, uri
            root = roots[REPOSITORIES[repo]]
            relative = parse_qs(parsed.fragment).get("subdirectory", [""])[0]
            path = root / relative
        elif uri.startswith("file:"):
            parsed = urlsplit(uri)
            path = Path(parsed.path)
            root = next((p for p in (*roots.values(), Path(__file__).resolve().parents[2])
                         if path.is_relative_to(p)), None)
            assert root is not None, uri
            path = path / parse_qs(parsed.fragment).get("subdirectory", [""])[0]
        else:
            pytest.fail("Unexpected offline bundle source: " + uri)
        assert path.exists(), path
        return ResolvedSource(active_path=path, source_root=root)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    config = HostConfig(tmp_path / "app", workspace, {"config": {"providers": [{
        "module": "provider-openai", "config": {
            **({"api_key": "offline-fixture", "use_streaming": False,
                "max_retries": 0, "raw": True} if actual_provider else {}),
            "default_model": "gpt-6.1-sol"}}]}},
        tmp_path / "registry", tmp_path / "shared")
    assert config.active_bundle == "work"
    _, bundle, _ = await load_root_bundle(config, config.active_bundle)
    bundle.resolve_pending_context()
    plan, _ = live_plan(bundle.to_mount_plan())
    assert {row["module"] for row in plan["tools"]} == set(WORK_TOOLS)
    assert {row["module"] for row in plan["hooks"]} == set(WORK_HOOKS)
    assert set(plan["agents"]) == WORK_AGENTS
    assert len(config.app_bundles) == 5
    assert next(row for row in plan["tools"] if row["module"] == "tool-web")["config"]["working_dir"] == str(workspace)
    return bundle, plan, roots


async def work_root(tmp_path, monkeypatch, provider, *, store=None, post=None, deny_work=False,
                    prepared_host=False, configuration_only=False, worker_observers=False,
                    provider_setup=None, live=False):
    bundle, plan, roots = await compose_work(tmp_path, monkeypatch, actual_provider=provider is None)
    # Discovery sees the same selected public resources, mirrored offline. No
    # real home/project scans and no deferred Git source download at first turn.
    def local(value):
        if value.startswith("git+"):
            parsed = urlsplit(value.removeprefix("git+"))
            repo = parsed.path.rsplit("/", 1)[-1].split("@", 1)[0].removesuffix(".git")
            return str(roots[REPOSITORIES[repo]] / parse_qs(parsed.fragment)["subdirectory"][0])
        if value.startswith("@"):
            namespace, relative = value[1:].split(":", 1)
            key = {"work-skills": "work", "routing-matrix": "routing", "context-intelligence": "context_intelligence",
                   "computer-use": "computer_use", "unified": "unified"}.get(namespace, namespace)
            root = Path(__file__).resolve().parents[2] if key == "unified" else roots[key]
            return str(root / relative)
        return value
    for section in ("tools", "hooks"):
        for row in plan[section]:
            config = row.get("config", {})
            for key in ("skills", "search_paths"):
                if key in config:
                    config[key] = [local(v) for v in config[key]]
    store = store or SessionStore(tmp_path / "sessions")
    def initialize_media(session):
        session.coordinator.loader = WorkLoader(provider)
        media.bind_media(session, store)
    if prepared_host:
        from amplifier_foundation.bundle import PreparedBundle
        # Source exports/entry points are already selected above. The real
        # PreparedBundle lifecycle installs mentions, prompt factory and ready
        # metadata; no activator, installation or remote provider is required.
        prepared = PreparedBundle(plan, resolver=object(), bundle=bundle)
        async def before_initialize(session):
            initialize_media(session)
        session = await prepared.create_session(session_id="ordinary-work-media",
            session_cwd=tmp_path / "workspace", before_initialize=before_initialize)
    else:
        session = AmplifierSession(plan, session_id="ordinary-work-media")
        initialize_media(session)
    try:
        if not prepared_host:
            await session.initialize()
        coordinator = session.coordinator
        if live:
            from amplifier_module_loop_live.runtime import Runtime
            from amplifier_web.attachments import encode
            runtime = Runtime(session.session_id)
            coordinator.register_capability("live.runtime", runtime)
            coordinator.register_capability("live.attachments.encode", encode)
        else:
            runtime = SimpleNamespace(session_id=session.session_id, generation={})
        cap = coordinator.get_capability(media.NAMESPACE)
        assert cap is not None
        if provider is None:
            provider = coordinator.get("providers", "openai")
            assert type(provider).__name__ == "OpenAIProvider"
            provider_setup(provider)
        graph = coordinator.get_capability("web.media.construction").graph
        evidence = graph.participants_evidence()
        assert len(evidence["loads"]) == 23 and all(row["mounted"] for row in evidence["loads"])
        assert evidence["ready"] and all(row["completed"] for row in evidence["ready"])
        assert type(coordinator.get("context")).__name__ == "BoundaryContextManager"
        assert "computer" not in coordinator.get("tools") and "desktop" not in coordinator.get("tools")
        assert cap.report("test", event="readiness", outcome="inspect")["consumers"]["loop-live"]["ready"] is False
        if prepared_host:
            from amplifier_web.host.children import install_children
            from amplifier_foundation import SessionConfigurator
            delegate = coordinator.get("tools", "delegate")
            registry = await install_children(session, prepared, runtime, store, approvals=None)
            assert coordinator.get("tools", "delegate").original is delegate
            assert coordinator.get("tools", "live_worker").registry is registry
            configurator = SessionConfigurator(session, prepared)
            # This is the actual prepare_manager ordering, including the
            # configuration phase even when saved overrides are empty.
            with media.hook_configuration(coordinator):
                await configurator.apply_saved_settings({})
            if configuration_only:
                return session, store, None, [], None
        provider.capability = cap
        snapshots, checks = [], []
        async def checkpoint(**kwargs):
            rows = store.save(session.session_id, await coordinator.get("context").get_messages(), {})
            media.acknowledge_media(coordinator, store, session.session_id, rows)
            snapshots.append(copy.deepcopy(rows))
            return rows
        coordinator.register_capability("session.durable_checkpoint", checkpoint)
        if live:
            from amplifier_web.attachments import install_media_context
            await install_media_context(coordinator)
        from amplifier_web.context_continuity import ContextContinuity
        ContextContinuity(coordinator, session.session_id, store.directory(session.session_id), checkpoint)
        from amplifier_web.host.session import install_checkpoint_hooks
        install_checkpoint_hooks(coordinator, runtime, checkpoint)
        provider.checkpoint_seen = lambda: bool(snapshots and any(
            media.NAMESPACE in (row.get("metadata") or {}) for row in snapshots[-1]))
        async def work_check(name, arguments):
            checks.append((name, copy.deepcopy(arguments)))
            return {"allowed": not (deny_work and arguments.get("file_path") == str(provider.path))}
        async def app_bridge(operation, args):
            return {"surfaces": [], "inputIds": []} if operation == "context.manifest" else {}
        coordinator.register_capability("web.work_effect_check", work_check)
        await install_app_access(coordinator, app_bridge, execution_role="root")
        if worker_observers:
            from amplifier_web import runtime_worker
            from amplifier_web.execution_events import ExecutionEvents
            # Invoke the actual late Worker installers, in start() order,
            # without process pipes, preparation/install or any app operation.
            emitted = []
            monkeypatch.setattr(runtime_worker, "publish", emitted.append)
            worker = runtime_worker.Worker()
            worker.session, worker.runtime = session, runtime
            worker.bridge = app_bridge
            worker.telemetry = ExecutionEvents(session.session_id, emitted.append)
            ui_tool = coordinator.get("tools", "ui_use")
            assert type(ui_tool).__name__ == "WorkCheckedTool"
            assert not coordinator.get_capability("web.media.adapters")
            worker.computer_episodes.install(coordinator, lambda: None)
            # The sponsor runs inside the issued Work body, never as an
            # unrecorded raw replacement of a pending Work edge.
            assert coordinator.get("tools", "ui_use") is ui_tool
            coordinator.register_capability("live.checkpoint", checkpoint)
            worker.install_collaboration_checkpoint(coordinator)
            worker.install_activity(coordinator)
        await media.flush_adapters(coordinator)
        if post:
            media.registration_hooks(coordinator).register("tool:post", post, priority=900, name="ordinary-post")
        if worker_observers:
            class ObserverSourceHost(FiniteSourceHost):
                """Finite test host; production Worker installers are not mocked.

                This does not claim Worker.start/pipe/LIVE-manager execution.
                Reapply its late observer sequence within the actual loop-owned
                registration to cover first-turn and subsequent-turn closure.
                """
                async def prepare_execution(self, loop, view, providers):
                    result = await super().prepare_execution(loop, view, providers)
                    worker.install_activity(coordinator)
                    worker.install_collaboration_checkpoint(coordinator)
                    await install_app_access(coordinator, app_bridge, execution_role="root")
                    worker.computer_episodes.install(coordinator, lambda: None)
                    await media.flush_adapters(coordinator)
                    return result
            host = ObserverSourceHost(coordinator)
            host.worker, host.emitted = worker, emitted
        else:
            host = FiniteSourceHost(coordinator)
        coordinator.register_capability("live.host", host)
        assert type(coordinator.get_capability("tools.dispatch")).__name__ == "BoundDispatch"
        return session, store, checkpoint, checks, host
    except BaseException:
        await session.cleanup()
        raise
