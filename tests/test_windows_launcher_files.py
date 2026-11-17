"""The launcher's measured closure and its installed-file comparison, without Windows."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from amplifier_web import windows_launcher_files as inventory

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "python scripts/windows_launcher_files.py --write"
SPEC = importlib.util.spec_from_file_location("launcher_file_tool", ROOT / "scripts/windows_launcher_files.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def test_lists_and_dynamic_decisions_match_source():
    computed = tool.compute(ROOT)
    assert computed["files"] == list(inventory.FILES), COMMAND
    assert computed["packages"] == list(inventory.PACKAGES), COMMAND
    assert set(computed["places"]) == set(inventory.DYNAMIC), COMMAND
    for place, paths in inventory.DYNAMIC.items():
        assert paths is not None, f"{place}: {COMMAND}, then decide this dynamic load"
        assert isinstance(paths, tuple), place
        for path in paths:
            assert path in inventory.FILES, (place, path, COMMAND)
            assert (ROOT / path).is_file(), (place, path)


@pytest.fixture
def source_copy(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(ROOT / "amplifier_web", root / "amplifier_web",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static", "bundle_data"))
    shutil.copy2(ROOT / "uv.lock", root / "uv.lock")
    return root


def test_new_import_in_listed_file_changes_computed_files(source_copy):
    path = source_copy / "amplifier_web/cli.py"
    with path.open("a") as stream:
        stream.write("\nfrom . import launcher_inventory_added\n")
    added = source_copy / "amplifier_web/launcher_inventory_added.py"
    added.write_text("value = 1\n")
    computed = tool.compute(source_copy)
    assert added.relative_to(source_copy).as_posix() in computed["files"]
    assert computed["files"] != list(inventory.FILES), COMMAND


def test_new_reachable_dynamic_place_needs_a_decision(source_copy):
    path = source_copy / "amplifier_web/cli.py"
    with path.open("a") as stream:
        stream.write('\n__import__("new_launcher_inventory_module")\n')
    computed = tool.compute(source_copy)
    place = 'amplifier_web/cli.py::<module>::__import__::__import__("new_launcher_inventory_module")'
    assert place in computed["places"]
    assert place not in inventory.DYNAMIC, COMMAND


def test_real_entry_imports_are_in_files():
    script = """
import json, sys
from pathlib import Path
import amplifier_web.windows_service
import amplifier_web.windows_consumer
import amplifier_web.cli
import amplifier_web
root = Path(amplifier_web.__file__).resolve().parent.parent
files = sorted({Path(module.__file__).resolve().relative_to(root).as_posix()
    for name, module in sys.modules.items()
    if (name == "amplifier_web" or name.startswith("amplifier_web.")) and getattr(module, "__file__", None)})
print(json.dumps({"root": str(root), "files": files}))
"""
    result = subprocess.run([sys.executable, "-I", "-c", script],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert Path(loaded["root"]) == ROOT, "The isolated import must check this working tree."
    assert set(loaded["files"]) <= set(inventory.FILES), (loaded["files"], COMMAND)


@pytest.fixture
def small_tree(tmp_path, monkeypatch):
    root = tmp_path / "small"
    package = root / "amplifier_web"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text("def entry():\n    pass\n")
    (root / "uv.lock").write_text("")
    (root / tool.TARGET).write_text(
        '"""Keep this header."""\n' + tool.BEGIN
        + "\nFILES = ()\nPACKAGES = ()\nDYNAMIC = {\n}\n"
        + tool.END + "\n\ndef keep_this():\n    return 'untouched'\n")
    monkeypatch.setattr(tool, "ENTRIES", {"core": [("amplifier_web.cli", "entry")], "rare": []})
    monkeypatch.setattr(tool, "__file__", str(root / "scripts/windows_launcher_files.py"))
    return root


def test_closure_keeps_measured_name_import_and_cut_rules(small_tree):
    package = small_tree / "amplifier_web"
    (package / "cli.py").write_text("""
import json
from . import helper as alias
from .exports import exported as renamed
from .windows_service import _host
def entry():
    from amplifier_web import nested
    import amplifier_web.extra as extra
    alias.used
    renamed
    nested.branch
    extra.called
    _host()
def unreachable():
    from . import unused
if __name__ == "__main__":
    from . import main_only
""")
    sources = {
        "helper.py": "def used():\n    from . import used_body\n"
                     "def unused():\n    from . import unused\n",
        "exports.py": "from .helper import used as exported\n",
        "nested/__init__.py": "",
        "nested/branch.py": "",
        "extra.py": "class called:\n    def method(self):\n        from . import class_body\n",
        "windows_service.py": "def _host():\n    from . import host_body\n",
    }
    for name in ("used_body", "class_body", "unused", "main_only", "host_body"):
        sources[name + ".py"] = ""
    for path, source in sources.items():
        target = package / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    closure = tool.Closure(small_tree, tool.ENTRIES["core"])
    assert closure.third_party() == []
    assert set(closure.files()) == {"amplifier_web/" + path for path in (
        "__init__.py", "cli.py", "helper.py", "exports.py", "nested/__init__.py",
        "nested/branch.py", "extra.py", "windows_service.py", "used_body.py", "class_body.py")}


def test_dynamic_places_only_in_reached_code_and_stable_across_line_moves(small_tree):
    path = small_tree / "amplifier_web/cli.py"
    source = """
import importlib
value = __file__
def entry():
    importlib.import_module("one")
    __import__("two")
    importlib.util.spec_from_file_location("three", __file__)
    args = ["-m", "-c"]
    Used
class Used:
    def method(self):
        return __file__
def unused():
    __import__("not_reached")
if __name__ == "__main__":
    __import__("not_loaded")
"""
    path.write_text(source)
    places = tool.compute(small_tree)["places"]
    assert {place.split("::")[1] for place in places} == {"<module>", "entry", "Used"}
    assert {place.split("::")[2] for place in places} == {
        "__file__", "__import__", "import_module", "spec_from_file_location", "-m", "-c"}
    assert not any("not_reached" in place or "not_loaded" in place for place in places)
    path.write_text("\n\n# A line move does not change an identity.\n" + source)
    assert tool.compute(small_tree)["places"] == places


def test_dynamic_place_source_is_stripped_and_bounded(small_tree):
    line = '__import__("' + "x" * 160 + '")'
    (small_tree / "amplifier_web/cli.py").write_text("def entry():\n    " + line + "\n")
    assert tool.compute(small_tree)["places"] == [
        "amplifier_web/cli.py::entry::__import__::" + line[:120]]


def test_dynamic_import_aliases_keep_their_kind(small_tree):
    (small_tree / "amplifier_web/cli.py").write_text("""
from importlib import import_module as load
def entry():
    from importlib.util import spec_from_file_location as spec
    from builtins import __import__ as builtin_load
    load("one")
    spec("two", "two.py")
    builtin_load("three")
""")
    assert tool.compute(small_tree)["places"] == [
        'amplifier_web/cli.py::entry::__import__::builtin_load("three")',
        'amplifier_web/cli.py::entry::import_module::load("one")',
        'amplifier_web/cli.py::entry::spec_from_file_location::spec("two", "two.py")']


def test_packages_map_imports_and_union_all_lock_markers(small_tree):
    (small_tree / "amplifier_web/cli.py").write_text("import yaml\n")
    (small_tree / "uv.lock").write_text("""
[[package]]
name = "pyyaml"
dependencies = [{name = "one_name", marker = "sys_platform == 'win32'"}]
[[package]]
name = "pyyaml"
dependencies = [{name = "two.name", marker = "sys_platform == 'linux'"}]
[[package]]
name = "one-name"
dependencies = [{name = "transitive"}]
[[package]]
name = "two-name"
[[package]]
name = "transitive"
dependencies = [{name = "pyyaml"}]
""")
    assert tool.compute(small_tree)["packages"] == ["one-name", "pyyaml", "transitive", "two-name"]


def test_unresolved_import_is_a_named_error(small_tree, capsys):
    (small_tree / "amplifier_web/cli.py").write_text("import not_in_the_lock\n")
    with pytest.raises(ValueError, match="not_in_the_lock"):
        tool.compute(small_tree)
    assert tool.main(["--check"]) == 1
    assert "not_in_the_lock" in capsys.readouterr().out


def test_unresolved_lock_dependency_is_a_named_error(small_tree):
    (small_tree / "amplifier_web/cli.py").write_text("import yaml\n")
    (small_tree / "uv.lock").write_text('[[package]]\nname = "pyyaml"\ndependencies = [{name = "absent"}]\n')
    with pytest.raises(ValueError, match="absent"):
        tool.compute(small_tree)


def test_write_keeps_code_and_decisions_adds_new_and_drops_gone(small_tree, capsys):
    path = small_tree / tool.TARGET
    (small_tree / "amplifier_web/cli.py").write_text(
        'def entry():\n    file = __file__\n    __import__("new")\n')
    kept = "amplifier_web/cli.py::entry::__file__::file = __file__"
    original = path.read_text().replace("DYNAMIC = {\n", "DYNAMIC = {\n"
        + f"    {kept!r}: ('amplifier_web/data.json',),  # Reads bundled data.\n"
        + "    'gone': (),  # Removed code.\n")
    path.write_text(original)
    (small_tree / "amplifier_web/data.json").write_text("{}")
    assert tool.main(["--write"]) == 0
    written = path.read_text()
    assert written.split(tool.BEGIN)[0] == original.split(tool.BEGIN)[0]
    assert written.split(tool.END)[1] == original.split(tool.END)[1]
    assert "# Reads bundled data." in written
    assert "'gone'" not in written
    values = tool._constants(small_tree)
    assert values["DYNAMIC"][kept] == ("amplifier_web/data.json",)
    assert "amplifier_web/data.json" in values["FILES"]
    assert list(values["DYNAMIC"].values()).count(None) == 1
    assert tool.main(["--check"]) == 1
    assert COMMAND in capsys.readouterr().out
    path.write_text(written.replace(": None,", ": (),  # Loads no package file."))
    assert tool.main(["--check"]) == 0
    assert tool.main(["--write"]) == 0
    once = path.read_bytes()
    assert tool.main(["--write"]) == 0
    assert path.read_bytes() == once


@pytest.mark.parametrize("constant", ["FILES", "PACKAGES", "DYNAMIC"])
def test_check_rejects_stale_constants(small_tree, constant, capsys):
    assert tool.main(["--write"]) == 0
    path = small_tree / tool.TARGET
    source = path.read_text()
    if constant == "FILES":
        source = source.replace("FILES = (", "FILES = ('extra', ")
    elif constant == "PACKAGES":
        source = source.replace("PACKAGES = ()", "PACKAGES = ('extra',)")
    else:
        source = source.replace("DYNAMIC = {\n", "DYNAMIC = {\n    'gone': (),\n")
    path.write_text(source)
    assert tool.main(["--check"]) == 1
    assert COMMAND in capsys.readouterr().out


def install(prefix, layout="Lib/site-packages", packages=None):
    """Only the files that the comparison is allowed to read."""
    site = prefix / layout
    info = site / "amplifier_unified-1.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Name: amplifier-unified\nVersion: 1.0.0\n")
    (info / "entry_points.txt").write_text("[console_scripts]\namplifier-unified = amplifier_web.cli:main\n")
    for path in inventory.FILES:
        target = site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('__version__ = "1.0.0"\nvalue = 1\n' if path.endswith("/__init__.py") else "same\n")
    for name, version in (packages or {}).items():
        metadata = site / f"{name}-{version}.dist-info/METADATA"
        metadata.parent.mkdir()
        metadata.write_text(f"Name: {name}\nVersion: {version}\n")
    return site, info


@pytest.fixture
def environments(tmp_path):
    launcher, app = tmp_path / "launcher", tmp_path / "app"
    left = install(launcher, packages={inventory.PACKAGES[0]: "1.0"})
    right = install(app, "lib/python3.13/site-packages", {inventory.PACKAGES[0]: "1.0"})
    return launcher, app, left, right


def test_equal_environments_and_distributions_both_missing(environments):
    launcher, app, _, _ = environments
    assert inventory.differs(launcher, app) == {
        "differs": False, "files": [], "packages": [], "unreadable": []}


def test_changed_listed_file(environments):
    launcher, app, _, (site, _) = environments
    path = inventory.FILES[1]
    (site / path).write_text("changed\n")
    assert inventory.differs(launcher, app) == {
        "differs": True, "files": [path], "packages": [], "unreadable": []}


def test_unlisted_files_interpreter_configuration_and_start_programs_are_not_read(environments, monkeypatch):
    launcher, app, _, (site, info) = environments
    for path in (site / "amplifier_web/not_listed.py", app / "pyvenv.cfg",
                 app / "Scripts/python.exe", app / "Scripts/amplifier-unified.exe",
                 site / "unrelated-9.dist-info/METADATA"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n")
    allowed = {site / path for site in (environments[2][0], site) for path in inventory.FILES}
    allowed.update({environments[2][1] / "entry_points.txt", info / "entry_points.txt"})
    allowed.update({site / f"{inventory.PACKAGES[0]}-1.0.dist-info/METADATA"
                    for site in (environments[2][0], site)})
    read = Path.read_bytes

    def checked(path):
        assert path in allowed, f"Read outside the inventory: {path}"
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", checked)
    assert inventory.differs(launcher, app)["differs"] is False


def test_only_version_assignment_is_ignored(environments):
    launcher, app, _, (site, _) = environments
    path = site / "amplifier_web/__init__.py"
    path.write_text('__version__: str = "2.0.0"\nvalue = 1\n')
    assert inventory.differs(launcher, app)["differs"] is False
    path.write_text('__version__ = "2.0.0"\nvalue = 2\n')
    assert inventory.differs(launcher, app)["files"] == ["amplifier_web/__init__.py"]


@pytest.mark.parametrize("line", ['__version__ == "{value}"', '# __version__ {value}'])
def test_version_comparisons_and_comments_are_not_assignments(environments, line):
    launcher, app, (left, _), (right, _) = environments
    for site, value in ((left, 1), (right, 2)):
        (site / "amplifier_web/__init__.py").write_text(
            '__version__ = "1.0.0"\n' + line.format(value=value) + "\n")
    assert inventory.differs(launcher, app)["files"] == ["amplifier_web/__init__.py"]


@pytest.mark.parametrize("change", ["version", "one_side"])
def test_distribution_difference(environments, change):
    launcher, app, _, (site, _) = environments
    name = inventory.PACKAGES[0]
    info = site / f"{name}-1.0.dist-info"
    if change == "version":
        (info / "METADATA").write_text(f"Name: {name}\nVersion: 2.0\n")
    else:
        shutil.rmtree(info)
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert result["packages"] == [name]
    assert result["unreadable"] == []


def test_metadata_names_use_pep503_normalization(environments, monkeypatch):
    launcher, app, (left, _), (right, _) = environments
    monkeypatch.setattr(inventory, "PACKAGES", ("some-package-name",))
    for site, name in ((left, "Some_Package.Name"), (right, "some_package_name")):
        info = site / f"{name}-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n")
    assert inventory.differs(launcher, app)["differs"] is False


@pytest.mark.parametrize("kind", ["changed", "missing", "unreadable"])
def test_entry_points_are_compared(environments, monkeypatch, kind):
    launcher, app, _, (_, info) = environments
    path = info / "entry_points.txt"
    if kind == "changed":
        path.write_text("new start program\n")
    elif kind == "missing":
        path.unlink()
    else:
        deny_read(monkeypatch, path)
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert result["files"] == ["amplifier_unified-*.dist-info/entry_points.txt"]
    assert result["unreadable"] == ([] if kind == "changed" else [str(path)])


def deny_read(monkeypatch, denied):
    read = Path.read_bytes

    def checked(path):
        if path == denied:
            raise PermissionError("Test refusal")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", checked)


@pytest.mark.parametrize("kind", ["missing", "unreadable", "both_missing"])
def test_missing_or_unreadable_listed_file_is_not_equal(environments, monkeypatch, kind):
    launcher, app, (left, _), (right, _) = environments
    name = inventory.FILES[0]
    path = right / name
    if kind in {"missing", "both_missing"}:
        path.unlink()
        if kind == "both_missing":
            (left / name).unlink()
    else:
        deny_read(monkeypatch, path)
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert result["files"] == [name]
    assert str(path) in result["unreadable"]


@pytest.mark.parametrize("kind", ["missing", "unreadable", "malformed", "wrong_name", "duplicate",
                                 "no_version", "empty_version"])
def test_unreadable_distribution_metadata_is_not_equal(environments, monkeypatch, kind):
    launcher, app, _, (site, _) = environments
    name = inventory.PACKAGES[0]
    path = site / f"{name}-1.0.dist-info/METADATA"
    if kind == "missing":
        path.unlink()
    elif kind == "unreadable":
        deny_read(monkeypatch, path)
    elif kind == "malformed":
        path.write_bytes(b"\xff")
    elif kind == "wrong_name":
        path.write_text("Name: another-package\nVersion: 1.0\n")
    elif kind == "no_version":
        path.write_text(f"Name: {name}\n")
    elif kind == "empty_version":
        path.write_text(f"Name: {name}\nVersion: \n")
    else:
        duplicate = site / f"{name}-2.0.dist-info"
        duplicate.mkdir()
        (duplicate / "METADATA").write_bytes(path.read_bytes())
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert result["unreadable"]


@pytest.mark.parametrize("kind", ["absent", "file", "unreadable", "ambiguous"])
def test_invalid_prefix_never_raises(environments, monkeypatch, kind, tmp_path):
    _, app, _, _ = environments
    prefix = tmp_path / "bad"
    if kind == "file":
        prefix.write_text("not an environment")
    elif kind == "unreadable":
        glob = Path.glob

        def checked(path, pattern):
            if path == prefix:
                raise PermissionError("Test prefix refusal")
            return glob(path, pattern)

        monkeypatch.setattr(Path, "glob", checked)
    elif kind == "ambiguous":
        install(prefix)
        install(prefix, "lib/python3.13/site-packages")
    assert inventory.differs(prefix, app) == {
        "differs": True, "files": [], "packages": [], "unreadable": [str(prefix)]}


def test_unreadable_distribution_directory_never_raises(environments, monkeypatch):
    launcher, app, _, (site, _) = environments
    iterdir = Path.iterdir

    def checked(path):
        if path == site:
            raise PermissionError("Test directory refusal")
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", checked)
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert str(site) in result["unreadable"]


def test_result_lists_are_bounded(environments, monkeypatch):
    launcher, app, (left, _), (right, _) = environments
    monkeypatch.setattr(inventory, "PACKAGES", tuple(f"package{number}" for number in range(25)))
    for name in inventory.PACKAGES:
        info = left / f"{name}-1.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: {name}\nVersion: 1\n")
    for name in inventory.FILES:
        (right / name).unlink()
    result = inventory.differs(launcher, app)
    assert result["differs"] is True
    assert len(result["files"]) == len(result["packages"]) == len(result["unreadable"]) == 20