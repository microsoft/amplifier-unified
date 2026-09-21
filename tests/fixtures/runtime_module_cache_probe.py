"""Disposable local Git fixtures, real Core/Foundation/host, no model/network calls.

Only dependency installation is disabled: the interpreter already has Core,
Foundation, loop-live and context-simple. The provider and capture hook are
synthetic. Bundle loading, cache resolution, module loading, session mounting,
worker startup and saved-history resume use their real implementations.
"""
# The standalone subprocess must add this checkout before importing app code.
# ruff: noqa: E402
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_module_loop_live.runtime import Input
from amplifier_web.host import session as host
from amplifier_web.host.storage import SessionStore


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL,
    ).strip()


def commit(path, label):
    git(path, "add", ".")
    git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", label)


async def no_install(*args, **kwargs):
    pass


async def setup(root):
    shared, app, workspace = root / "shared", root / "app", root / "workspace"
    for path in (shared, app, workspace):
        path.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(app),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared-state"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(shared / "projects"))
    ModuleActivator._install_dependencies = no_install
    origin = root / "hook-origin"
    package = origin / "amplifier_module_hook_context_intelligence"
    package.mkdir(parents=True)
    hook = package / "__init__.py"
    hook.write_text("__amplifier_module_type__ = 'hooks'\n")
    (origin / "pyproject.toml").write_text('[project]\nname="cache-hook-fixture"\nversion="0.0.1"\n')
    git(origin, "init", "-b", "main")
    commit(origin, "old invalid metadata")
    source = "git+" + origin.as_uri() + "@main"
    old = await ModuleActivator(install_deps=False).activate("hook-context-intelligence", source)
    old_head, old_content = git(old, "rev-parse", "HEAD"), (old / package.name / "__init__.py").read_bytes()
    hook.write_text("from pathlib import Path\n__amplifier_module_type__ = 'hook'\n"
        "async def mount(coordinator, config=None):\n"
        "    coordinator.register_capability('fixture.loaded_hook', str(Path(__file__).resolve()))\n")
    commit(origin, "validated metadata")
    generation = app / "updates/releases" / ("a" * 32) / "foundation"
    valid = await ModuleActivator(cache_dir=generation / "cache", install_deps=False).activate(
        "hook-context-intelligence", source)
    (app / "updates/active.json").write_text(json.dumps({"current": "a" * 32}))
    # Avoid importing the old shared registry into the base cache. This fixture
    # represents an already provisioned app with an activated release.
    (app / "foundation").mkdir()
    (app / "foundation/registry.json").write_text('{"version":1,"bundles":{}}')
    provider = root / "provider"
    provider_package = provider / "amplifier_module_provider_cache_fixture"
    provider_package.mkdir(parents=True)
    (provider_package / "__init__.py").write_text('''
from amplifier_core.models import ProviderInfo
__amplifier_module_type__ = "provider"
class FixtureProvider:
    name = "fixture"
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"offline"})
    def get_config_schema(self): return {"fields": []}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        raise AssertionError("The cache probe must never call a model")
async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(), name="fixture")
''')
    paths = {name: str(Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent.parent)
             for name in ("loop-live", "context-simple")}
    host.LOOP_SOURCE = paths["loop-live"]
    bundle = workspace / "bundle.yaml"
    # JSON is a YAML subset and avoids serializing host-specific Python objects.
    bundle.write_text(json.dumps({"bundle": {"name": "cache-fixture", "version": "1.0"},
        "session": {"orchestrator": {"module": "loop-live", "source": paths["loop-live"]},
                    "context": {"module": "context-simple", "source": paths["context-simple"]}},
        "providers": [{"module": "provider-cache-fixture", "source": str(provider)}],
        "hooks": [{"module": "hook-context-intelligence", "source": source}]}))
    (shared / "settings.yaml").write_text(json.dumps({"bundle": {"active": str(bundle), "app": []}}))
    return shared, app, workspace, bundle, old, old_head, old_content, valid


async def worker(workspace, bundle, resumed, expected):
    from amplifier_web import runtime_worker
    events = []
    runtime_worker.publish = events.append
    instance = runtime_worker.Worker()
    store = SessionStore.for_app(Path(os.environ["AMPLIFIER_WEB_HOME"]), workspace)
    rows = [{"role": "user", "content": "Preserve this saved conversation", "metadata": {"_seq": 0}},
            {"role": "assistant", "content": "Saved answer", "metadata": {"_seq": 1}}]
    transcript = store.directory("cache-fixture") / "transcript.jsonl"
    if resumed:
        store.save("cache-fixture", rows, {"bundle": str(bundle)})
        original = transcript.read_bytes()
    try:
        await instance.start({"id": "cache-fixture", "workspace": str(workspace), "bundle": str(bundle)}, raise_errors=True)
        await instance.runtime.wait_for(lambda event: event["type"] == "session.ready", timeout=8)
        loaded = Path(instance.session.coordinator.get_capability("fixture.loaded_hook"))
        assert loaded.is_relative_to(expected), loaded
        assert any(event["type"] == "runtime.ready" for event in events)
        if resumed:
            assert transcript.read_bytes() == original
            assert await instance.session.coordinator.get("context").get_messages() == rows
        assert not any(event["type"] == "generation.started" for event in events)
        await instance.runtime.submit(Input("stop"))
        await asyncio.wait_for(instance.execution, 5)
    finally:
        if instance.ownership.registration:
            await instance.ownership.registration.close()
        if instance.execution and not instance.execution.done():
            instance.execution.cancel()
            await asyncio.gather(instance.execution, return_exceptions=True)
        if instance.controls:
            await instance.controls.close()
        if instance.session:
            await instance.session.cleanup()
        if instance.shared_handle:
            instance.shared_handle.release()


mode = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    shared, app, workspace, bundle, old, old_head, old_content, valid = asyncio.run(setup(Path(tmp).resolve()))
    if mode == "probe":
        sys.path.insert(0, str(REPO / "amplifier_web"))
        sys.argv = [str(REPO / "amplifier_web/update_probe.py"), str(workspace), str(bundle)]
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        asyncio.run(worker(workspace, bundle, mode == "resumed-worker", valid))
    assert os.environ["AMPLIFIER_HOME"] == str(shared)
    assert git(old, "rev-parse", "HEAD") == old_head
    assert (old / "amplifier_module_hook_context_intelligence/__init__.py").read_bytes() == old_content
    print(json.dumps({"entrypoint": mode, "shared_cache_unchanged": True, "model_calls": 0}))
