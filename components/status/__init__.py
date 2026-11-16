"""Read-only local status snapshots; no dependency on an Amplifier host."""
from .publisher import Publisher
from .reader import discover

__version__ = "0.1.0"
__all__ = ["Publisher", "discover"]

# Explicit local source composition for resolvers that require a source hint.
from .hook import mount
__amplifier_module_type__ = "hook"
