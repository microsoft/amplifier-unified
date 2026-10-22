"""Configuration-only public read projection; storage and user JSON stay intact.

Apply before previews or pointer descent. Resources remain lazy until the reader
actually follows them, carrying the module context of a standalone config body.
"""


_OPERATIONS = frozenset({'configuration.inspect', 'configuration.apply',
                         'configuration.toggle', 'configuration.providers'})


def _tokens(path):
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]]


def is_configuration(path):
    parts = _tokens(path)
    return (len(parts) >= 3 and parts[0] == 'sessions' and parts[2] == 'configuration'
            or len(parts) >= 3 and parts[0] == 'runtimeControl' and parts[2] in _OPERATIONS
            or len(parts) >= 4 and parts[0] == 'runtimeControl'
            and parts[2:4] == ['catalog.inspect', 'agents'])


def is_provider_metadata(path):
    parts = _tokens(path)
    return len(parts) >= 3 and parts[0] == 'runtimeControl' and parts[2] == 'configuration.providers'


def project(value, path, *, reference_key='$statePath'):
    """Sanitize the selected preview only, without reading unrelated cold bodies."""
    from .agent_state import _pointer
    from .cold_display import ColdRecord
    from .runtime_controls import public_config

    if is_configuration(path):
        return public_config(value, metadata=is_provider_metadata(path))
    parts = _tokens(path)
    if len(parts) == 1 and parts[0] in {'sessions', 'runtimeControl'}:
        if isinstance(value, list):
            return [project(row, _pointer(path, i), reference_key=reference_key)
                    for i, row in enumerate(value)]
        if isinstance(value, dict):
            return {key: project(row, _pointer(path, key), reference_key=reference_key)
                    for key, row in value.items()}
    owner = len(parts) == 2 and parts[0] in {'sessions', 'runtimeControl'}
    catalog = len(parts) == 3 and parts[0] == 'runtimeControl' and parts[2] == 'catalog.inspect'
    if (owner or catalog) and isinstance(value, dict):
        result = {}
        for key in value:
            child_path = _pointer(path, key)
            if isinstance(value, ColdRecord) and not dict.__contains__(value, key):
                # A preview is not a request to hydrate every historical body.
                result[key] = {reference_key: child_path}
            else:
                result[key] = project(value[key], child_path, reference_key=reference_key)
        return result
    return value
