"""Normal import/reload checks; no native APIs, host, worker loop or providers."""

import asyncio
import importlib
import importlib.util
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web import shell_tool_containment as containment


PACKAGE = Path(__file__).resolve().parents[1] / "amplifier_web"
TARGET = "amplifier_module_tool_bash"
NAMES = (
    "_get_windows_job_object",
    "_assign_to_windows_job",
    "_spawn_descendant_sweep",
    "_protect_windows_descendants",
)
SOURCE = """
from . import helper
executions = globals().get("executions", 0) + 1
class BashTool: pass
calls = []
def _get_windows_job_object():
    calls.append("job")
    return "original-job"
def _assign_to_windows_job(pid):
    calls.append("assign")
    return True
def _spawn_descendant_sweep(root_pid):
    calls.append("sweep")
    return "original-task"
async def _protect_windows_descendants(root_pid):
    calls.append("protect")
    return "original-walk"
def untouched():
    return "unchanged"
"""


@pytest.fixture(autouse=True)
def clean_imports(monkeypatch):
    # Restore the actual list object and prior module entries even on failure.
    # Direct-script worker imports must not leave sibling aliases behind either.
    siblings = {"shell_tool_containment", "runtime_protocol", "message_delivery",
                "response_completion", "ownership", "computer_episodes"}
    def owned(name):
        return (name == TARGET or name.startswith(TARGET + ".")
                or name in siblings or name == "other_shell_fixture")
    saved = {name: module for name, module in sys.modules.items() if owned(name)}
    for name in saved:
        del sys.modules[name]
    monkeypatch.setattr(sys, "meta_path", [
        finder for finder in sys.meta_path
        if not getattr(finder, "_unified_shell_tool_containment", False)
    ])
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if owned(name):
                del sys.modules[name]
        sys.modules.update(saved)
        importlib.invalidate_caches()


@pytest.fixture
def standin(tmp_path, monkeypatch):
    package = tmp_path / TARGET
    package.mkdir()
    (package / "__init__.py").write_text(SOURCE, encoding="utf-8")
    (package / "helper.py").write_text("VALUE = 'child unchanged'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return package


async def assert_disabled(module, monkeypatch):
    # Fail on delegation even if a replacement discards the original's result.
    assert module._get_windows_job_object() is None
    assert module._assign_to_windows_job(123) is False
    task_factory = Mock(side_effect=AssertionError("sweep created an asyncio task"))
    with monkeypatch.context() as scoped:
        scoped.setattr(asyncio, "create_task", task_factory)
        assert module._spawn_descendant_sweep(123) is None
        assert await module._protect_windows_descendants(123) is None
    task_factory.assert_not_called()
    assert module.calls == []
    assert module.untouched() == "unchanged"
    assert module.helper.VALUE == "child unchanged"


async def test_import_after_install_is_neutralized(standin, monkeypatch, capsys):
    assert containment.install() == {"module": TARGET, "importHook": True, "loaded": None}
    module = importlib.import_module(TARGET)
    assert module.executions == 1
    await assert_disabled(module, monkeypatch)
    assert getattr(module, "__unified_shell_tool_containment__") == {
        "replaced": list(NAMES), "missing": [], "failed": []}
    assert capsys.readouterr().err == ""


async def test_already_imported_module_is_neutralized(standin, monkeypatch):
    module = importlib.import_module(TARGET)
    original = module._assign_to_windows_job
    result = containment.install()
    assert module._assign_to_windows_job is not original
    assert result["loaded"] == {"replaced": list(NAMES), "missing": [], "failed": []}
    assert sys.modules[TARGET] is module
    await assert_disabled(module, monkeypatch)


def test_install_and_neutralize_are_idempotent(standin, monkeypatch):
    containment.install()
    module = importlib.import_module(TARGET)
    before = tuple(sys.meta_path)
    functions = [getattr(module, name) for name in NAMES]
    result = containment.neutralize(module)
    assert containment.install()["loaded"] == result
    assert tuple(sys.meta_path) == before
    assert [getattr(module, name) for name in NAMES] == functions
    # The isolated sibling and package import paths share one hook.
    monkeypatch.syspath_prepend(str(PACKAGE))
    importlib.import_module("shell_tool_containment").install()
    assert tuple(sys.meta_path) == before


async def test_reload_is_neutralized_after_original_code(standin, monkeypatch):
    containment.install()
    module = importlib.import_module(TARGET)
    assert importlib.reload(module) is module
    assert module.executions == 2
    await assert_disabled(module, monkeypatch)


@pytest.mark.parametrize("has_tool", [False, True])
def test_missing_names_are_returned_and_reported_once(standin, capsys, has_tool):
    source = SOURCE + "\ndel _assign_to_windows_job, _protect_windows_descendants\n"
    if not has_tool:
        source += "del BashTool\n"
    (standin / "__init__.py").write_text(source, encoding="utf-8")
    containment.install()
    module = importlib.import_module(TARGET)
    result = containment.neutralize(module)
    # The job's entry point is required. The walk's name is not: see the next test.
    assert result == {"replaced": [NAMES[0], NAMES[2]], "missing": [NAMES[1]], "failed": []}
    error = capsys.readouterr().err
    assert error == (containment.MESSAGE + "missing _assign_to_windows_job\n" if has_tool else "")
    assert containment.MESSAGE == "Unified could not turn off the shell tool's own Windows job: "
    assert not hasattr(module, NAMES[1]) and not hasattr(module, NAMES[3])


async def test_a_tool_without_the_walk_is_complete_and_silent(standin, monkeypatch, capsys):
    """The tool's own repair removes the walk. Its two job entry points are what the guard needs."""
    (standin / "__init__.py").write_text(
        SOURCE + "\ndel _spawn_descendant_sweep, _protect_windows_descendants\n", encoding="utf-8")
    containment.install()
    module = importlib.import_module(TARGET)
    assert module.__unified_shell_tool_containment__ == {
        "replaced": [NAMES[0], NAMES[1]], "missing": [], "failed": []}
    assert module._get_windows_job_object() is None and module._assign_to_windows_job(123) is False
    assert module.calls == [] and capsys.readouterr().err == ""


REFUSING = """
import sys, types
class _Refusing(types.ModuleType):
    def __setattr__(self, name, value):
        if name == "_assign_to_windows_job":
            raise AttributeError("this module refuses " + name)
        super().__setattr__(name, value)
sys.modules[__name__].__class__ = _Refusing
"""


class _ClosedStream:
    def write(self, text):
        raise BrokenPipeError("closed")

    def flush(self):
        raise BrokenPipeError("closed")


@pytest.mark.parametrize("fault", ["closed-stderr", "no-stderr", "refused-replacement"])
def test_a_guard_fault_after_the_tool_code_never_fails_the_import(standin, monkeypatch, capsys, fault):
    """The tool's code has run. Whatever the guard cannot do then, the tool must still load."""
    if fault == "refused-replacement":
        (standin / "__init__.py").write_text(SOURCE + REFUSING, encoding="utf-8")
    else:
        # A required name is missing, so the guard has a message, and the message cannot be written.
        (standin / "__init__.py").write_text(SOURCE + "\ndel _assign_to_windows_job\n", encoding="utf-8")
        monkeypatch.setattr(sys, "stderr", _ClosedStream() if fault == "closed-stderr" else None)
    containment.install()
    module = importlib.import_module(TARGET)               # Must not raise.
    assert sys.modules[TARGET] is module and module.BashTool and module.untouched() == "unchanged"
    report = module.__unified_shell_tool_containment__
    if fault == "refused-replacement":
        assert report == {"replaced": [NAMES[0], NAMES[2], NAMES[3]], "missing": [], "failed": [NAMES[1]]}
        assert module._assign_to_windows_job(123) is True  # Not replaced: the result and stderr say so.
        assert capsys.readouterr().err == (
            containment.MESSAGE + "could not replace _assign_to_windows_job\n")
        assert module._get_windows_job_object() is None    # Every other name is replaced.
    else:
        assert report == {"replaced": [NAMES[0], NAMES[2], NAMES[3]], "missing": [NAMES[1]], "failed": []}
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""   # Not on stdout in place of stderr either.
    # A second look gives the same result and does not raise either.
    assert containment.install()["loaded"] == report


def test_neutralize_does_not_raise_for_an_odd_object_or_an_odd_earlier_report(standin, capsys):
    """A direct call must hold too, not only a call that a caller contains."""
    class Slots:                                            # vars() refuses this object.
        __slots__ = ("BashTool",)
    assert containment.neutralize(Slots()) == {
        "replaced": [], "missing": [NAMES[0], NAMES[1]], "failed": list()}
    class Unequal:
        def __ne__(self, other):
            raise RuntimeError("this report cannot be compared")
        __eq__ = __ne__
        __hash__ = None
    class NoTruth:
        def __bool__(self):
            raise RuntimeError("this answer is neither true nor false")
    class OddAnswer:                                        # The comparison works; its answer cannot be used.
        def __ne__(self, other):
            return NoTruth()
        __hash__ = None
    (standin / "__init__.py").write_text(SOURCE + "\ndel _assign_to_windows_job\n", encoding="utf-8")
    module = importlib.import_module(TARGET)                # No hook is installed in this test.
    for earlier in (Unequal(), OddAnswer()):
        setattr(module, "__unified_shell_tool_containment__", earlier)
        assert containment.neutralize(module) == {
            "replaced": [NAMES[0], NAMES[2], NAMES[3]], "missing": [NAMES[1]], "failed": []}
        assert capsys.readouterr().err == containment.MESSAGE + "missing _assign_to_windows_job\n"


def test_loader_without_exec_module_is_left_as_it_is():
    """The import system runs such a loader in another way. A wrapper with exec_module would break it."""
    class Legacy:
        def load_module(self, fullname):
            raise AssertionError("not loaded in this test")
    legacy = Legacy()
    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            return importlib.util.spec_from_loader(fullname, legacy) if fullname == TARGET else None
    sys.meta_path.insert(0, Finder())
    assert containment._Finder().find_spec(TARGET).loader is legacy


def test_other_modules_and_submodules_are_untouched(standin, tmp_path):
    (tmp_path / "other_shell_fixture.py").write_text(
        SOURCE.replace("from . import helper", "helper = None"), encoding="utf-8")
    before = list(sys.meta_path)
    containment.install()
    other = importlib.import_module("other_shell_fixture")
    assert other._get_windows_job_object() == "original-job"
    assert other._assign_to_windows_job(123) is True
    assert not hasattr(other, "__unified_shell_tool_containment__")
    assert containment._Finder().find_spec(TARGET + ".helper") is None
    assert sys.meta_path[1:] == before
    importlib.import_module(TARGET)
    assert not hasattr(sys.modules[TARGET + ".helper"], "__unified_shell_tool_containment__")


def test_absent_module_still_raises_import_error(monkeypatch):
    monkeypatch.setattr(sys, "path", [])  # No ambient installation can mask absence.
    containment.install()
    with pytest.raises(ModuleNotFoundError) as caught:
        importlib.import_module(TARGET)
    assert caught.value.name == TARGET
    assert TARGET not in sys.modules


def test_module_import_error_is_not_hidden(standin):
    (standin / "__init__.py").write_text(
        "raise ImportError('fixture dependency failed')\n", encoding="utf-8")
    containment.install()
    with pytest.raises(ImportError, match="fixture dependency failed"):
        importlib.import_module(TARGET)
    assert TARGET not in sys.modules


def test_existing_meta_finder_and_loader_are_preserved(monkeypatch):
    events = []
    class Loader:
        def create_module(self, spec):
            events.append("create")
            return None
        def exec_module(self, module):
            events.append("exec")
            exec(SOURCE.replace("from . import helper", "helper = None"), vars(module))
        def get_source(self, fullname):
            return SOURCE
    loader = Loader()
    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == TARGET:
                events.append(("find", target))
                return importlib.util.spec_from_loader(fullname, loader)
            return None
    sys.meta_path.insert(0, Finder())
    containment.install()
    module = importlib.import_module(TARGET)
    assert events == [("find", None), "create", "exec"]
    assert module._get_windows_job_object() is None
    assert module.__loader__.get_source(TARGET) == SOURCE
    importlib.reload(module)
    assert events[-2:] == [("find", module), "exec"]
    assert module._assign_to_windows_job(123) is False


@pytest.mark.parametrize("platform", ["nt", "posix", "other"])
@pytest.mark.parametrize("direct", [False, True])
async def test_worker_installs_only_on_windows_before_sibling_construction(
        standin, monkeypatch, capsys, platform, direct):
    # Import definitions, never execute the worker's __main__, run or start.
    monkeypatch.syspath_prepend(str(PACKAGE))
    namespace = runpy.run_path(str(PACKAGE / "runtime_worker.py"),
        run_name="containment_worker_test" if direct else "amplifier_web._containment_worker_test")
    namespace["Worker"].__init__.__globals__["os"] = SimpleNamespace(name=platform)
    blocks = importlib.import_module("response_completion" if direct else "amplifier_web.response_completion")
    observed = []
    original = blocks.ResponseBlocks
    def construct():
        # This is the first existing sibling construction in __init__.
        # The import must already be guarded, not just after Worker returns.
        module = importlib.import_module(TARGET)
        observed.append(hasattr(module, "__unified_shell_tool_containment__"))
        return original()
    monkeypatch.setattr(blocks, "ResponseBlocks", construct)
    worker = namespace["Worker"]()
    assert observed == [platform == "nt"]
    module = sys.modules[TARGET]
    stderr = capsys.readouterr().err
    if platform == "nt":
        assert worker.shell_tool_containment == {
            "module": TARGET, "importHook": True, "loaded": None}
        assert stderr == ""                                 # Silent when all is well.
        await assert_disabled(module, monkeypatch)
    else:
        assert not hasattr(worker, "shell_tool_containment")
        assert stderr == ""
        assert module._get_windows_job_object() == "original-job"
        assert not any(getattr(finder, "_unified_shell_tool_containment", False)
                       for finder in sys.meta_path)


def test_finder_without_find_spec_is_passed_over(standin):
    """The import system skips a meta-path finder that has no find_spec. The guard must do the same."""
    class Legacy:                                           # No find_spec at all.
        pass
    sys.meta_path.insert(0, Legacy())
    containment.install()
    module = importlib.import_module(TARGET)
    assert module._get_windows_job_object() is None
    assert module.__unified_shell_tool_containment__ == {"replaced": list(NAMES), "missing": [], "failed": []}


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("fault", ["install", "import", "install-and-stderr"])
def test_a_failed_guard_never_ends_a_worker(standin, monkeypatch, capsys, direct, fault):
    """A worker without the guard runs as before the guard existed. It says so once, and goes on."""
    monkeypatch.syspath_prepend(str(PACKAGE))
    namespace = runpy.run_path(str(PACKAGE / "runtime_worker.py"),
        run_name="containment_worker_test" if direct else "amplifier_web._containment_worker_test")
    scope = namespace["Worker"].__init__.__globals__
    scope["os"] = SimpleNamespace(name="nt")
    name = "shell_tool_containment" if direct else "amplifier_web.shell_tool_containment"
    guard = importlib.import_module(name)
    if fault == "import":
        monkeypatch.delattr(guard, "install")              # The sibling is there, its entry point is not.
    else:
        monkeypatch.setattr(guard, "install", Mock(side_effect=OSError("no guard")))
    if fault == "install-and-stderr":
        class Closed:
            def write(self, text):
                raise ValueError("I/O operation on closed file")
            def flush(self):
                raise ValueError("I/O operation on closed file")
        scope["sys"] = SimpleNamespace(stderr=Closed())
    worker = namespace["Worker"]()                          # Must not raise.
    assert worker.shell_tool_containment == {"error": "ImportError" if fault == "import" else "OSError"}
    assert worker.session is None and worker.approvals == {}   # Construction went on to its end.
    stderr = capsys.readouterr().err
    if fault == "install-and-stderr":
        assert stderr == ""
    else:
        assert stderr == ("Unified could not turn off the shell tool's own Windows job: "
                          + ("ImportError" if fault == "import" else "OSError") + "\n")
