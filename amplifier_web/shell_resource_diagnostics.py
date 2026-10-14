"""Explain existing page-key changes without retaining key values or identities."""


def rebuild_reasons(previous, current, *, workspace_page=False):
    if previous is None:
        return ('initial',), ()
    reasons, globals_changed = [], []
    old_bootstrap, old = previous
    bootstrap, new = current
    if old_bootstrap != bootstrap:
        reasons.append('bootstrap')
    old_navigation, old_attention = old[0]
    navigation, attention = new[0]
    if old_navigation != navigation:
        if (isinstance(old_navigation, tuple) and isinstance(navigation, tuple)
                and len(old_navigation) == len(navigation) == 6):
            for index, name in enumerate(('source', 'native', 'local', 'overlay', 'read', 'suppression')):
                if old_navigation[index] != navigation[index]:
                    reasons.append('navigation.' + name)
        else:
            reasons.append('navigation')
    if old_attention != attention:
        reasons.append('attention')
    if old[1][0] != new[1][0]:
        reasons.append('globals.unknown')
    old_globals, new_globals = dict(old[1][1]), dict(new[1][1])
    for name in sorted(new_globals):
        if old_globals.get(name) != new_globals[name]:
            globals_changed.append(name)
    if globals_changed:
        reasons.append('globals')
    if old[2] != new[2]:
        reasons.append('workspace')
    if old[3] != new[3]:
        reasons.append('scope' if workspace_page else 'selection')
    if old[4] != new[4]:
        reasons.append('query')
    return tuple(reasons), tuple(globals_changed)
