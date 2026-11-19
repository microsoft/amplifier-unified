"""Metadata-only optional feature discovery shared by updates and emergency reset."""
from importlib import metadata

OPTIONAL_EXTRAS={
    'native-desktop': ('amplifier-module-tool-computer-use',),
    'tui': ('amplifier-app-tui',),
    'managed-browser': ('playwright',),
    'decisions-openai': ('amplifier-lib-decisions', 'amplifier-module-provider-openai'),
    'decisions-azure': ('amplifier-lib-decisions', 'amplifier-module-provider-azure-openai'),
    'decisions-jev': ('amplifier-lib-decisions', 'amplifier-module-provider-jev'),
    'decisions-local': ('amplifier-lib-decisions', 'amplifier-module-provider-local-decisions'),
}

def installed_extras(*, path=None):
    """Retain known optional features without loading clients or desktop backends."""
    rows = None if path is None else {d.metadata["Name"].lower().replace("_", "-"): d
                                      for d in metadata.distributions(path=path)}
    extras=[]
    present={}
    for extra,distributions in sorted(OPTIONAL_EXTRAS.items()):
        for distribution in distributions:
            if distribution not in present:
                try:present[distribution]=metadata.distribution(distribution) if rows is None else rows.get(distribution)
                except metadata.PackageNotFoundError:present[distribution]=None
        if not all(present[name] is not None for name in distributions):continue
        if extra.startswith('decisions-') and not any(
                entry.group == 'amplifier.decision_backends'
                for entry in getattr(present[distributions[-1]], 'entry_points', ())):
            continue
        extras.append(extra)
    return extras

