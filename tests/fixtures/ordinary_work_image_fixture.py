"""Offline Work source composition, not an installed Worker or a new profile.

UNIFIED_WORK_MEDIA_SOURCES names a test-owned JSON receipt mapping preselected
repository names to immutable exports. No fetch/install/source learning here.
The only synthetic mount is the deterministic provider endpoint. All Work tools,
hooks and agent declarations survive composition; no other tool is called.
"""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from amplifier_core import AmplifierSession
from amplifier_core.loader import ModuleLoader
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources import SimpleSourceResolver
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import load_root_bundle, live_plan
from amplifier_web.host import media
from amplifier_web.host.media_assembly import ReviewedRootRecipe, WORK_TOOLS, WORK_HOOKS, WORK_AGENTS
from amplifier_web.host.storage import SessionStore
from amplifier_web.app_guidance import install_app_access
from test_tool_image_consumer_join import FiniteSourceHost

REPOSITORIES = {
    "amplifier-bundle-work": "work", "amplifier-foundation": "foundation",
    "amplifier-bundle-ui-use": "ui_use", "amplifier-bundle-skills": "skills",
    "amplifier-bundle-imagegen": "imagegen", "amplifier-bundle-computer-use": "computer_use",
    "amplifier-bundle-context-intelligence": "context_intelligence",
    "amplifier-bundle-routing-matrix": "routing", "amplifier-bundle-modes": "modes",
}


class WorkLoader(ModuleLoader):
    """Real Core entry-point loader; substitute only the no-network provider."""
    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    async def load(self, module_id, config=None, source_hint=None, coordinator=None):
        if module_id == "provider-openai-chatgpt":
            async def mount(current):
                await current.mount("providers", self.provider, name="offline")
            return mount
        return await super().load(module_id, config=config, source_hint=source_hint, coordinator=coordinator)


async def compose_work(tmp_path, monkeypatch):
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
    # No local desktop backend/SSH probes can invoke installed native binaries.
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
        "module": "provider-openai-chatgpt", "config": {
            "auth_mode": "chatgpt_codex", "default_model": "gpt-6.1-sol"}}]}},
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
                    prepared_host=False, configuration_only=False):
    bundle, plan, roots = await compose_work(tmp_path, monkeypatch)
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
    # Explicit package owner list, never inferred from the mounted graph.
    names = ["amplifier_module_" + name.replace("-", "_") for name in
             ("loop-live", "loop-streaming", "context-managed", "context-simple", *WORK_TOOLS, *WORK_HOOKS)]
    files = [Path(__file__).resolve(), Path(__file__).parents[1] / "test_ordinary_work_tool_images.py",
             Path(importlib.import_module("test_tool_image_consumer_join").__file__).resolve(),
             Path(importlib.import_module("amplifier_core.loader").__file__).resolve()]
    for name in names:
        package = importlib.import_module(name)
        files.extend(Path(package.__file__).parent.rglob("*.py"))
    files += [Path(importlib.import_module(name).__file__) for name in (
        "amplifier_web.host.media", "amplifier_web.host.session", "amplifier_web.app_guidance",
        "amplifier_web.runtime_worker")]
    store = store or SessionStore(tmp_path / "sessions")
    def initialize_media(session):
        session.coordinator.loader = WorkLoader(provider)
        selection = ReviewedRootRecipe(session.config, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        media.bind_media(session, store, selection=selection)
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
        cap = coordinator.get_capability(media.NAMESPACE)
        assert cap is not None
        bridge, graph, _ = coordinator.get_capability("web.media.construction")
        evidence = graph.participants_evidence()
        assert len(evidence["loads"]) == 23 and all(row["mounted"] for row in evidence["loads"])
        assert evidence["ready"] and all(row["completed"] for row in evidence["ready"])
        assert type(coordinator.get("context")).__name__ == "BoundaryContextManager"
        assert "computer" not in coordinator.get("tools") and "desktop" not in coordinator.get("tools")
        assert cap.report("test", event="readiness", outcome="inspect")["consumers"]["loop-live"]["ready"] is False
        if prepared_host:
            from amplifier_web.host.children import install_children
            from amplifier_foundation import SessionConfigurator
            from types import SimpleNamespace
            runtime = SimpleNamespace(session_id=session.session_id, generation={})
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
        from amplifier_web.context_continuity import ContextContinuity
        ContextContinuity(coordinator, session.session_id, store.directory(session.session_id), checkpoint)
        from amplifier_web.host.session import install_checkpoint_hooks
        from types import SimpleNamespace
        runtime = SimpleNamespace(session_id=session.session_id, generation={})
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
        await media.flush_adapters(coordinator)
        if post:
            media.registration_hooks(coordinator).register("tool:post", post, priority=900, name="ordinary-post")
        host = FiniteSourceHost(coordinator)
        coordinator.register_capability("live.host", host)
        assert type(coordinator.get_capability("tools.dispatch")).__name__ == "BoundDispatch"
        return session, store, checkpoint, checks, host
    except BaseException:
        await session.cleanup()
        raise
