"""Opt-in browser transport. Authoritative app/agent snapshots stay unchanged."""


def changes(previous, current, path=()):
    """Replace changed leaves, preserving large unchanged catalogs and assets.

    Arrays are patched by index only when their shape is unchanged. Inserts,
    removals and reordering therefore cannot leave dangling indexed records.
    Each stream owns its baseline; reconnect always starts with a full snapshot.
    """
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        result = [{'path': [*path, key], 'remove': True} for key in previous.keys() - current.keys()]
        for key, value in current.items():
            if key not in previous:
                result.append({'path': [*path, key], 'value': value})
            else:
                result.extend(changes(previous[key], value, (*path, key)))
        return result
    if (isinstance(previous, list) and isinstance(current, list) and len(previous) == len(current)
            and all(not isinstance(old, dict) or not isinstance(new, dict)
                    or old.get('id') == new.get('id') for old, new in zip(previous, current))):
        result = []
        for index, value in enumerate(current):
            result.extend(changes(previous[index], value, (*path, index)))
        return result
    return [{'path': list(path), 'value': current}]


def delta(previous, current):
    return {'baseRevision': previous['revision'], 'revision': current['revision'],
            'changes': changes(previous, current)}
