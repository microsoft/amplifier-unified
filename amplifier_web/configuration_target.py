"""Explicit configuration ownership for global and workspace control surfaces."""
from pathlib import Path

PREFIXES = ('providers.', 'routing.', 'modules.', 'sources.')


def resolve(service, args):
    target = args.get('configurationTarget')
    if target is None:
        return None  # Existing agents and chat controls retain their contract.
    if args.get('sessionId') or args.get('workspace') or args.get('nativeProject') or args.get('location'):
        raise ValueError('Choose one configuration target.')
    scope = args.get('scope', 'global' if target['kind'] == 'global' else 'local')
    if target['kind'] == 'global':
        if scope != 'global':
            raise ValueError('Global Settings can only change global defaults.')
        return {'workspace': str(service.data_dir), 'global_only': True, 'scope': scope}
    if scope not in {'local', 'project'}:
        raise ValueError('Workspace settings can only change this workspace or project.')
    workspace = next((row for row in service.state.get('workspaces', [])
                      if row['id'] == target['workspaceId']), None)
    if not workspace or not Path(workspace['path']).is_dir():
        raise ValueError('Choose an existing workspace before changing its settings.')
    return {'workspace': workspace['path'], 'global_only': False, 'scope': scope}


def state_for(service, field, args):
    """Never label data from a previous owner as the current target's data."""
    state = service.state.setdefault(field, {})
    target = args.get('configurationTarget')
    if state.get('configurationTarget') != target:
        operations = state.get('operations', {})
        state.clear()
        if field == 'setup':
            state['operations'] = operations
    state['configurationTarget'] = target
    return state


def operation_key(args, key):
    target = args.get('configurationTarget')
    if not target:
        return key
    owner = 'global' if target['kind'] == 'global' else 'workspace:' + target['workspaceId']
    return owner + '\n' + key
