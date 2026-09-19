"""Expose app code to runtime scripts without exposing the host's dependencies."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def bootstrap_app_package():
    package_dir = Path(__file__).resolve().parent
    init = package_dir / "__init__.py"
    existing = sys.modules.get("amplifier_web")
    if existing is not None:
        if Path(getattr(existing, "__file__", "")).resolve() != init:
            raise ImportError("A different amplifier_web package is already loaded")
        return existing
    # Adding package_dir.parent to sys.path would expose the outer tool venv's
    # site-packages, including distribution metadata absent from this runtime.
    spec = spec_from_file_location(
        "amplifier_web", init, submodule_search_locations=[str(package_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the Amplifier Unified package")
    package = module_from_spec(spec)
    sys.modules["amplifier_web"] = package
    try:
        spec.loader.exec_module(package)
    except BaseException:
        sys.modules.pop("amplifier_web", None)
        raise
    return package