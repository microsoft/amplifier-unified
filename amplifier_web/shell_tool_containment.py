"""Use Unified's owned Windows worker job, not the shell tool's PID walk.

Standard-library only: runtime_worker also imports this as a direct sibling
before bootstrapping the application package. Call install only in a Windows
worker already contained by the host's WindowsOwnedProcess.

Nothing here may raise after the shell tool's own code ran, and nothing here
may end a worker. A guard step that fails leaves the tool as it was, and the
result and one stderr line say so.
"""

import sys


_MODULE = "amplifier_module_tool_bash"
_REPORT = "__unified_shell_tool_containment__"


def _nothing(*args, **kwargs):
    return None


def _no_assignment(*args, **kwargs):
    return False


async def _no_protection(*args, **kwargs):
    return None


_REPLACEMENTS = {
    "_get_windows_job_object": _nothing,
    "_assign_to_windows_job": _no_assignment,
    "_spawn_descendant_sweep": _nothing,
    "_protect_windows_descendants": _no_protection,
}
# The job's two entry points. With both replaced the tool makes no job and
# assigns no process, whatever else it does. A tool that lacks one has changed.
_REQUIRED = ("_get_windows_job_object", "_assign_to_windows_job")
# The other two names are the walk over the process table. Without a job the
# walk can assign nothing; it is replaced to save its work. The tool's own
# repair (its commit bf36c12) removes the walk, so its absence is no fault
# and gets no message.


# One beginning for every message, so that a reader can find them all.
MESSAGE = "Unified could not turn off the shell tool's own Windows job: "


def _say(detail):
    """One stderr line, or nothing. A message must never fail an import or a worker."""
    try:
        if sys.stderr is not None:
            print(MESSAGE + detail, file=sys.stderr, flush=True)
    except Exception:
        pass


def neutralize(module):
    """Replace the present entry points and report the result.

    It does not raise: each step that touches the module is contained, and
    both callers contain it once more. "missing" lists required names that
    the module does not have. "failed" lists names that could not be
    replaced. With either, the tool's own job can be active in this process.
    """
    replaced, missing, failed = [], [], []
    try:
        present = dict(vars(module))  # A copy that is a real dict, whatever the object is.
    except Exception:
        present = {}
    for name, replacement in _REPLACEMENTS.items():
        if name not in present:
            if name in _REQUIRED:
                missing.append(name)
            continue
        try:
            setattr(module, name, replacement)
            replaced.append(name)
        except Exception:
            failed.append(name)
    report = {"replaced": replaced, "missing": missing, "failed": failed}
    try:
        changed = bool(getattr(module, _REPORT, None) != report)
        setattr(module, _REPORT, report)
    except Exception:
        changed = True
    if (missing or failed) and "BashTool" in present and changed:
        _say("; ".join(text for text in (
            "missing " + ", ".join(missing) if missing else "",
            "could not replace " + ", ".join(failed) if failed else "") if text))
    return report


class _Loader:
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        create = getattr(self.loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self.loader.exec_module(module)  # The tool's own errors go to its importer.
        try:
            neutralize(module)
        except Exception as error:  # Nothing known reaches this. It keeps the import whole.
            _say(type(error).__name__)

    def __getattr__(self, name):
        return getattr(self.loader, name)


class _Finder:
    # Recognize the same hook if imported as both a sibling and a package module.
    _unified_shell_tool_containment = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname != _MODULE:
            return None
        # Delegate in normal order without re-entering this finder. In
        # particular, reload must find a fresh spec and wrap its loader again.
        for finder in tuple(sys.meta_path):
            if finder is self:
                continue
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue  # The import system also passes over such a finder.
            spec = find(fullname, path, target)
            if spec is not None:
                # Only a loader that the import system runs through exec_module.
                # Any other one stays as it is, and the import stays as it was.
                if getattr(spec.loader, "exec_module", None) is not None:
                    spec.loader = _Loader(spec.loader)
                return spec
        return None  # Preserve the normal ModuleNotFoundError.


def install():
    """Cover an existing module and later normal imports, once per process."""
    if not any(getattr(finder, "_unified_shell_tool_containment", False) is True
               for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
    module = sys.modules.get(_MODULE)
    return {"module": _MODULE, "importHook": True,
            "loaded": neutralize(module) if module is not None else None}