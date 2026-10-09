"""Expose app code to runtime scripts without exposing the host's dependencies."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def bootstrap_app_package():
    app_root = Path(__file__).resolve().parent.parent
    packages = ("amplifier_web", "amplifier_operations")
    # Validate every existing package before registering any missing sibling.
    for name in packages:
        if name in sys.modules:
            existing = sys.modules[name]
            origin = getattr(existing, "__file__", None)
            if not origin or Path(origin).resolve() != app_root / name / "__init__.py":
                raise ImportError(f"A different {name} package is already loaded")
    # Adding app_root to sys.path would expose the outer tool venv's
    # site-packages, including distribution metadata absent from this runtime.
    # Expose only these app-owned packages; dependencies still resolve in the
    # runtime environment. Operations is needed by checkpoints and peer input.
    before = set(sys.modules)
    try:
        for name in packages:
            if name in sys.modules:
                continue
            package_dir = app_root / name
            spec = spec_from_file_location(
                name, package_dir / "__init__.py",
                submodule_search_locations=[str(package_dir)],
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load the {name} package")
            package = module_from_spec(spec)
            sys.modules[name] = package
            spec.loader.exec_module(package)
    except BaseException:
        # Remove partial submodule imports too, but retain pre-existing modules.
        for name in set(sys.modules) - before:
            if any(name == root or name.startswith(root + ".") for root in packages):
                sys.modules.pop(name, None)
        raise
    return sys.modules["amplifier_web"]