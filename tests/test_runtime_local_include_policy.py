"""Real composition retains local source intent during update collection."""
import json

import pytest

from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources import SimpleSourceResolver
from amplifier_web.host.session import session_registry, load_configured_bundle
from test_runtime_policy_reconciliation import selected, NAME, URL, git


def local_bundle(s):
    (s.root / "bundle.yaml").write_text(
        "bundle:\n  name: local-leaf\ntools:\n  - module: fixture\n    source: .\n")
    git(s.root, "add", "bundle.yaml")
    git(s.root, "commit", "-m", "include")
    s.old = git(s.root, "rev-parse", "HEAD")
    s.marker.write_text(json.dumps({"git_url": URL, "ref": "main", "commit": s.old}))


@pytest.mark.parametrize("form", [
    "absolute", "relative", "file-subdirectory", "absolute-manifest",
    "relative-manifest", "file-manifest", "namespace-manifest",
])
async def test_real_local_include_override_is_not_branch_authority(selected, monkeypatch, form):
    s = selected
    local_bundle(s)
    monkeypatch.chdir(s.config.workspace)
    relative = "./" + s.root.relative_to(s.config.workspace).as_posix()
    references = {
        "absolute": str(s.root),
        "relative": relative,
        "file-subdirectory": s.root.parent.as_uri() + "#subdirectory=" + s.root.name,
        "absolute-manifest": str(s.root / "bundle.yaml"),
        "relative-manifest": relative + "/bundle.yaml",
        "file-manifest": s.root.as_uri() + "#subdirectory=bundle.yaml",
        "namespace-manifest": str(s.root),
    }
    include = "fixture:bundle.yaml" if form == "namespace-manifest" else "git+" + URL + "@main"
    override = "fixture" if form == "namespace-manifest" else include
    parent = s.stage / "outer.yaml"
    parent.write_text("bundle:\n  name: outer\nincludes:\n  - " + include + "\n")
    s.config.settings["sources"] = {"bundles": {override: references[form]}}
    s.config._source_policy_observer = s.collector
    loaded = await session_registry(s.config).load(str(parent))
    assert loaded.tools[0]["module"] == "fixture"
    report = await s.collector.report(s.batch, s.config)
    assert report["packages"][NAME]["status"] == "local"


async def test_explicit_local_root_manifest_protects_containing_package(selected):
    s = selected
    local_bundle(s)
    s.config._source_policy_observer = s.collector
    loaded, chosen = await load_configured_bundle(session_registry(s.config), s.config, str(s.root / "bundle.yaml"))
    assert loaded.tools[0]["module"] == "fixture"
    assert chosen == str(s.root / "bundle.yaml")
    report = await s.collector.report(s.batch, s.config)
    assert report["packages"][NAME]["status"] == "local"


@pytest.mark.parametrize("override", [False, True])
async def test_git_includes_still_provide_selected_branch_evidence(selected, monkeypatch, override):
    s = selected
    local_bundle(s)
    s.collector.references.clear()
    uri = "git+" + URL + "@main"
    include = "git+" + URL + "@original" if override else uri
    if override:
        s.config.settings["sources"] = {"bundles": {include: uri}}
    s.config._source_policy_observer = s.collector
    parent = s.stage / "outer.yaml"
    parent.write_text("bundle:\n  name: outer\nincludes:\n  - " + include + "\n")
    original = SimpleSourceResolver.resolve
    async def resolve(self, reference):
        if reference == uri:
            return ResolvedSource(active_path=s.root / "bundle.yaml", source_root=s.root)
        assert not reference.startswith("git+"), "Unexpected network request"
        return await original(self, reference)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    # The local outer bundle does not convert its unrelated Git include into
    # a local source, even when both live below the same candidate directory.
    loaded, _ = await load_configured_bundle(session_registry(s.config), s.config, str(parent))
    assert loaded.tools[0]["module"] == "fixture"
    report = await s.collector.report(s.batch, s.config)
    assert report["packages"][NAME]["status"] == "selected"
    assert report["packages"][NAME]["ref"] == "main"
