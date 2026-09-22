"""Real Foundation composition with private settings and no model or downloads."""

import asyncio
import copy
from urllib.parse import parse_qs

import pytest
import yaml

from amplifier_foundation import BundleRegistry
from amplifier_foundation.exceptions import BundleDependencyError
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources import SimpleSourceResolver
from amplifier_web.host.config import read_config
from amplifier_web.host.session import load_root_bundle
from amplifier_web.shared_settings import settings_paths


ORIGIN = "git+https://github.com/example/portable@main"
TARGET = "git+https://github.com/example/portable@reviewed"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


@pytest.fixture(autouse=True, params=['current', 'legacy'])
def registry_api(request, monkeypatch):
    """Exercise the older constructor and its eager cleanup save path too."""
    if request.param == 'legacy':
        class LegacyRegistry(BundleRegistry):
            def __init__(self, home=None, *, strict=False, include_source_resolver=None):
                super().__init__(home, strict=strict, include_source_resolver=include_source_resolver)
                self.save()
        monkeypatch.setattr('amplifier_foundation.BundleRegistry', LegacyRegistry)


@pytest.fixture
def source_fixture(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app, shared = tmp_path / "app", tmp_path / "shared"
    roots = {uri: tmp_path / label for uri, label in [(ORIGIN, "original"), (TARGET, "target")]}
    for uri, root in roots.items():
        for name in ("root", "app", "leaf"):
            write(root / f"{name}.yaml", {
                "bundle": {"name": f"portable-{name}"},
                "tools": [{"module": f"tool-{name}", "config": {"source": root.name}}],
                "includes": ["portable:leaf.yaml"] if name == "root" else [],
            })
        write(root / "bundle.yaml", {"bundle": {"name": "portable"}})
    root_uri, app_uri = (ORIGIN + "#subdirectory=" + name + ".yaml" for name in ("root", "app"))
    paths = settings_paths(workspace, shared_home=shared, session_id="session-one")
    write(paths["global"], {"bundle": {"active": "selected", "added": {"selected": root_uri}, "app": [app_uri]}})
    calls = []
    original_resolve = SimpleSourceResolver.resolve

    async def resolve(self, uri):
        if not uri.startswith("git+"):
            return await original_resolve(self, uri)
        calls.append(uri)
        await asyncio.sleep(0)
        origin, _, fragment = uri.partition("#")
        assert origin in roots, f"Unexpected network source: {origin}"
        relative = parse_qs(fragment).get("subdirectory", ["bundle.yaml"])[0]
        active = roots[origin] / relative
        if not active.exists():
            raise FileNotFoundError(active)
        return ResolvedSource(active_path=active, source_root=roots[origin])

    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    baseline = BundleRegistry(app / "foundation")
    baseline.register({"portable": ORIGIN, "selected": root_uri})
    baseline.save()
    overrides = {"portable": TARGET, root_uri: TARGET + "#subdirectory=root.yaml",
                 app_uri: TARGET + "#subdirectory=app.yaml"}
    return workspace, app, shared, roots, paths, overrides, calls


@pytest.mark.parametrize("scope", ["project", "local", "session"])
@pytest.mark.parametrize("concurrent", [False, True])
async def test_two_sessions_keep_scoped_root_app_and_namespace_sources(source_fixture, scope, concurrent):
    workspace, app, shared, roots, paths, overrides, calls = source_fixture
    write(paths[scope], {"sources": {"bundles": overrides}})
    first = read_config(workspace, home=app, shared_home=shared, session_id="session-one")
    # Another workspace does not inherit project/local settings. Same workspace
    # for the session case proves a session-only override is not inherited.
    other_workspace = workspace if scope == "session" else workspace.parent / "other"
    other_workspace.mkdir(exist_ok=True)
    second = read_config(other_workspace, home=app, shared_home=shared, session_id="session-two")
    before = (app / "foundation/registry.json").read_bytes()
    original_settings = copy.deepcopy(first.settings)
    async def compose(config):
        registry, loaded, _ = await load_root_bundle(config, "selected")
        registry.save()
        return loaded
    if concurrent:
        first_bundle, second_bundle = await asyncio.gather(compose(first), compose(second))
    else:
        first_bundle = await compose(first)
        # Reread configuration/registry after the first session prepared.
        second = read_config(other_workspace, home=app, shared_home=shared, session_id="session-two")
        second_bundle = await compose(second)
    for loaded, label in [(first_bundle, "target"), (second_bundle, "original")]:
        assert {row["module"]: row["config"]["source"] for row in loaded.tools} == {
            "tool-root": label, "tool-app": label, "tool-leaf": label}
        assert loaded.source_base_paths["portable-root"] == roots[TARGET if label == "target" else ORIGIN]
    assert first.settings == original_settings
    assert (app / "foundation/registry.json").read_bytes() == before
    restored = BundleRegistry(app / "foundation")
    assert restored.find("portable") == ORIGIN
    assert restored.find(ORIGIN + "#subdirectory=app.yaml") is None


@pytest.mark.parametrize("selection", ["direct", "named"])
async def test_override_is_used_by_transitive_include_and_failed_load_is_isolated(source_fixture, selection):
    workspace, app, shared, roots, paths, overrides, _ = source_fixture
    root_uri = ORIGIN + "#subdirectory=root.yaml"
    outer = workspace / "outer.yaml"
    write(outer, {"bundle": {"name": "outer"}, "includes": [root_uri]})
    if selection == "named":
        overrides = {**overrides, "outer": str(outer)}
    write(paths["session"], {"sources": {"bundles": overrides}})
    config = read_config(workspace, home=app, shared_home=shared, session_id="session-one")
    before = (app / "foundation/registry.json").read_bytes()
    _, loaded, _ = await load_root_bundle(config, "outer" if selection == "named" else str(outer))
    assert all(row["config"]["source"] == "target" for row in loaded.tools)
    assert (app / "foundation/registry.json").read_bytes() == before
    config.settings['sources']['bundles'][root_uri] = TARGET + "#subdirectory=missing.yaml"
    with pytest.raises(BundleDependencyError):
        await load_root_bundle(config, str(outer))
    assert (app / "foundation/registry.json").read_bytes() == before


async def test_explicit_global_settings_remain_available_to_future_sessions(source_fixture):
    workspace, app, shared, _, paths, overrides, _ = source_fixture
    global_settings = yaml.safe_load(paths["global"].read_text())
    global_settings["sources"] = {"bundles": overrides}
    write(paths["global"], global_settings)
    before = paths["global"].read_bytes()
    for session_id in ("first", "second"):
        config = read_config(workspace, home=app, shared_home=shared, session_id=session_id)
        _, loaded, _ = await load_root_bundle(config, "selected")
        assert all(row["config"]["source"] == "target" for row in loaded.tools)
    assert paths["global"].read_bytes() == before
