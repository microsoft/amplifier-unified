"""Use Unified's owned Windows worker job, not the shell tool's PID walk.

Standard-library only: runtime_worker also imports this as a direct sibling
before bootstrapping the application package. Call install only in a Windows
worker already contained by the host's WindowsOwnedProcess.
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


def neutralize(module):
    """Replace present entry points; report missing upstream names to the caller."""
    replaced, missing = [], []
    for name, replacement in _REPLACEMENTS.items():
        if name in vars(module):
            setattr(module, name, replacement)
            replaced.append(name)
        else:
            missing.append(name)
    report = {"replaced": replaced, "missing": missing}
    previous = getattr(module, _REPORT, None)
    if missing and "BashTool" in vars(module) and report != previous:
        print("Unified shell tool containment: missing " + ", ".join(missing),
              file=sys.stderr, flush=True)
    setattr(module, _REPORT, report)
    return report


class _Loader:
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        create = getattr(self.loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self.loader.exec_module(module)
        neutralize(module)

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
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                if spec.loader is not None:
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