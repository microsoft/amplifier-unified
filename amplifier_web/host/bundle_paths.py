"""Read-only local bundle lookup shared by preparation and source inventory."""
from pathlib import Path

from ..session_files import amplifier_home


def local_bundle_path(config, reference):
    if reference.startswith(('git+', 'https://', 'http://', 'ssh://')):
        return None
    candidate = Path(reference.removeprefix('file://')).expanduser()
    absolute = candidate.is_absolute()
    if not absolute:
        candidate = config.workspace / candidate
    # Bare names are also registry aliases (notably the default "work"). An
    # unrelated workspace folder must not shadow one merely by existing. Keep
    # explicit paths authoritative so malformed local bundles still fail at
    # their requested path rather than silently switching to another bundle.
    explicit = absolute or reference.startswith(('file://', './', '../')) or '/' in reference
    manifest = candidate.is_dir() and any((candidate / name).is_file()
        for name in ('bundle.md', 'bundle.yaml', 'bundle.yml'))
    if candidate.exists() and (explicit or manifest or candidate.is_file() and candidate.suffix in {'.md', '.yaml', '.yml'}):
        return candidate
    if absolute or reference.startswith('file://'):
        return None
    # Retain existing Unified precedence, then support the standard CLI project
    # and user bundle directories. Never fetch or inspect included bundles here.
    roots = (config.home / 'bundles', config.workspace / '.amplifier-unified/bundles',
             config.workspace / '.amplifier/bundles',
             (config.config_home or amplifier_home()) / 'bundles')
    for root in roots:
        for suffix in ('', '.md', '.yaml', '.yml'):
            path = root / (reference + suffix)
            if path.is_file() or path.is_dir() and any((path / name).is_file()
                    for name in ('bundle.md', 'bundle.yaml', 'bundle.yml')):
                return path
    return None
