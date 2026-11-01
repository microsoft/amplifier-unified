"""Synthetic supply guards only; no uv, runtime imports, or fixture main."""
import importlib.util
import hashlib
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import patch

import pytest


REVISION = "a" * 40
SOURCE = "https://example.invalid/approval?branch=main&subdirectory=modules/context"
CANONICAL = '''version = 1
revision = 3
requires-python = ">=3.13, <3.14"
resolution-markers = ["sys_platform == 'linux'"]

[[package]]
name = "synthetic-root"
version = "0.0.0"
source = { virtual = "." }
dependencies = [{ name = "canonical-dep" }]
[package.metadata]
requires-dist = [{ name = "canonical-dep", specifier = "==1.2.3" }]
provides-extras = ["retained-extra"]

[[package]]
name = "canonical-dep"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example.invalid/dep.whl", hash = "sha256:1234", size = 42 }]
'''
FIXTURES = (
    ("amplifier-module-context-omittedfixture", "context"),
    ("amplifier-module-provider-omittedfixture", "provider"),
    ("amplifier-module-hooks-omittedfixture", "hooks"),
)


@pytest.fixture(scope="module")
def probe():
    path = Path(__file__).parent / "fixtures/omitted_ref_worker_probe.py"
    spec = importlib.util.spec_from_file_location("supply_guard_fixture", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def lock_case(tmp_path, probe):
    dependencies, requirements, packages = [], [], []
    for name, kind in FIXTURES:
        source = f"https://example.invalid/approval?branch=main&subdirectory=modules/{kind}"
        dependencies.append(f'{{ name = "{name}" }}')
        requirements.append(f'{{ name = "{name}", git = "{source}" }}')
        packages.append(f'''\n[[package]]
name = "{name}"
version = "0.1.0"
source = {{ git = "{source}#{REVISION}" }}
''')
    expanded = CANONICAL.replace(
        'dependencies = [{ name = "canonical-dep" }]',
        'dependencies = [{ name = "canonical-dep" }, ' + ', '.join(dependencies) + ']',
    ).replace(
        'specifier = "==1.2.3" }]',
        'specifier = "==1.2.3" }, ' + ', '.join(requirements) + ']',
    ) + ''.join(packages)
    original_path, expanded_path = tmp_path / "canonical.lock", tmp_path / "expanded.lock"
    # Expected graph comes from explicit input, never the production identity filter.
    original_graph = tomllib.loads(CANONICAL)["package"][1:]

    def verify(candidate=expanded, original=CANONICAL):
        original_path.write_text(original)
        expanded_path.write_text(candidate)
        result = probe.verify_expanded_lock(
            SimpleNamespace(runtime_lock=original_path), expanded_path,
            "synthetic-root", original_graph, REVISION,
        )
        assert original_path.read_text() == original
        assert expanded_path.read_text() == candidate
        return result

    return expanded, verify


def test_exact_expansion_preserves_canonical_rows_and_root_metadata(lock_case):
    _, verify = lock_case
    assert verify() == 5


@pytest.mark.parametrize("location", ["package", "requirement"])
@pytest.mark.parametrize("before,after", [
    ("https://example.invalid/approval", "https://example.invalid/other"),
    ("branch=main", "branch=HEAD"),
    ("branch=main", "rev=main"),
    ("modules/context", "modules/provider"),
])
def test_fixture_source_identity_is_exact(lock_case, location, before, after):
    expanded, verify = lock_case
    source = SOURCE + (f"#{REVISION}" if location == "package" else "")
    old = f'git = "{source}"'
    assert expanded.count(old) == 1
    with pytest.raises(AssertionError, match="Fixture source differs"):
        verify(expanded.replace(old, f'git = "{source.replace(before, after)}"'))


@pytest.mark.parametrize("location", ["package", "requirement"])
def test_wrong_commit_or_pinned_requirement_is_rejected(lock_case, location):
    expanded, verify = lock_case
    source = SOURCE + (f"#{REVISION}" if location == "package" else "")
    old = f'git = "{source}"'
    assert expanded.count(old) == 1
    with pytest.raises(AssertionError, match="Fixture source differs"):
        verify(expanded.replace(old, f'git = "{SOURCE}#{"b" * 40}"'))


@pytest.mark.parametrize("before,after,message", [
    ('version = "1.2.3"', 'version = "1.2.4"', "original package graph"),
    ('size = 42', 'size = 43', "original package graph"),
    ('revision = 3', 'revision = 4', "canonical lock policy"),
    ('requires-python = ">=3.13, <3.14"', 'requires-python = ">=3.12"', "canonical lock policy"),
    ('specifier = "==1.2.3"', 'specifier = ">=1.2.3"', "original root declaration"),
    ('provides-extras = ["retained-extra"]', 'provides-extras = []', "original root declaration"),
    ('source = { virtual = "." }', 'source = { editable = "." }', "original root declaration"),
])
def test_canonical_rows_and_metadata_cannot_change(lock_case, before, after, message):
    expanded, verify = lock_case
    assert expanded.count(before) == 1
    with pytest.raises(AssertionError, match=message):
        verify(expanded.replace(before, after))


@pytest.mark.parametrize("document", ["original", "expanded"])
def test_duplicate_canonical_row_is_rejected(lock_case, document):
    expanded, verify = lock_case
    duplicate = '\n[[package]]\nname = "canonical-dep"\nversion = "1.2.3"\n'
    with pytest.raises(AssertionError, match="duplicate package rows"):
        if document == "original":
            verify(original=CANONICAL + duplicate)
        else:
            verify(expanded + duplicate)


def test_plain_path_accepts_regular_file_and_tree(tmp_path, probe):
    file = tmp_path / "lock"
    file.write_text("synthetic")
    assert probe.plain_path(file) == file.resolve()
    assert probe.plain_path(tmp_path, tree=True) == tmp_path.resolve()


@pytest.mark.parametrize("location", ["leaf", "ancestor", "tree"])
def test_plain_path_refuses_symlinks(tmp_path, probe, location):
    directory = tmp_path / "supply"
    directory.mkdir()
    file = directory / "lock"
    file.write_text("synthetic")
    link = tmp_path / "link"
    link.symlink_to(file if location == "leaf" else directory,
                    target_is_directory=location != "leaf")
    if location == "tree":
        target, tree = tmp_path, True
    else:
        target, tree = (link if location == "leaf" else link / "lock"), False
    with pytest.raises(AssertionError, match="symlink"):
        probe.plain_path(target, tree=tree)


@pytest.mark.parametrize("origin_kind", ["source", "installed", "build-view", "wrong-build", "unrelated", "wrong-source", "wrong-bytes"])
def test_adoption_binds_both_valid_origins_to_installed_source(tmp_path, probe, origin_kind):
    prefix = tmp_path / "project/.venv"
    source_root = tmp_path / "store/objects/repository" / REVISION
    prefix.mkdir(parents=True)
    rows, graph = [], []
    payload = b"PAYLOAD = 1\n"
    payload_hash = hashlib.sha256(payload).hexdigest()
    for name, kind in FIXTURES:
        module = name.replace("-", "_")
        source = source_root / "modules" / kind
        package = source / module
        installed = prefix / "lib/python3.13/site-packages" / module
        for target in (package, installed):
            target.mkdir(parents=True)
            (target / "__init__.py").write_text("# synthetic module\n")
            (target / "_proof.py").write_text("# proof\n")
            (target / "_payload.py").write_bytes(payload)
        installed_source = source
        if origin_kind in ("build-view", "wrong-build"):
            build = tmp_path / ("store/builds" if origin_kind == "build-view" else "elsewhere") / "build-id"
            installed_source = build / "modules" / kind
            target = installed_source / module
            target.mkdir(parents=True)
            for filename in ("__init__.py", "_proof.py", "_payload.py"):
                (target / filename).write_bytes((package / filename).read_bytes())
            (build / ".amplifier_cache_meta.json").write_text('{"buildInput":true}')
        origin = installed if origin_kind == "installed" else package
        if origin_kind == "unrelated":
            origin = tmp_path / "unrelated" / module
            origin.mkdir(parents=True)
            (origin / "__init__.py").write_text("# synthetic module\n")
            (origin / "_payload.py").write_bytes(payload)
        if origin_kind == "wrong-bytes":
            (installed / "__init__.py").write_text("# different bytes\n")
        rows.append({"kind": "worker", "event": "mount", "pid": 123,
                     "module": module, "generation": "g", "revision": REVISION,
                     "payloadSha256": payload_hash, "prefix": str(prefix),
                     "executable": str(prefix / "bin/python"), "origin": str(origin / "__init__.py")})
        graph.append({"name": name, "path": str(installed_source),
                      "cacheSource": {"url": probe.URL, "revision": "b" * 40 if origin_kind == "wrong-source" else REVISION,
                                      "subdirectory": "modules/" + kind, "dirty": False}})
    kwargs = dict(generation="g", revision=REVISION, payload_hash=payload_hash,
                  prefix=prefix, pid=123, source_root=source_root, graph=graph)
    if origin_kind in ("source", "installed", "build-view"):
        assert probe.adopted(rows, **kwargs) == rows
    else:
        with pytest.raises(AssertionError):
            probe.adopted(rows, **kwargs)
