"""Client-local setup for a chat that does not exist until its first submission."""
import copy
import json

from jsonschema import validate
from .managed_chats import LOCATION, is_managed

SELECTION = {'type': 'object', 'properties': {
    'instance': {'type': 'string', 'maxLength': 200},
    'model': {'type': 'string', 'maxLength': 500},
    'effort': {'type': 'string', 'maxLength': 100}}, 'additionalProperties': False}
SETUP = {'type': 'object', 'properties': {
    'location': LOCATION,
    'title': {'type': 'string', 'maxLength': 200},
    'workspace': {'type': 'string', 'maxLength': 4000},
    'bundle': {'type': 'string', 'maxLength': 2000},
    'selection': SELECTION}, 'additionalProperties': False}


def defaults(state):
    view = state.get('view', {})
    surface = view.get('workSurface', 'chat')
    workspace_id = (view.get('workWorkspaceId') if surface == 'workspace'
                    else state.get('selectedWorkspaceId') if surface == 'chat' else None)
    workspace = next((w for w in state.get('workspaces', [])
                      if w['id'] == workspace_id), {})
    path = workspace.get('path') or ''
    return {'title': '', 'workspace': path, 'location': {'kind': 'workspace' if path else 'managed'},
            'bundle': '', 'selection': {}}


def validate_setup(value):
    validate(value, SETUP)
    return copy.deepcopy(value)


def open_draft(service, args):
    state = service.state
    setup = copy.deepcopy(state['view'].get('newSessionDraft') or defaults(state))
    # Returning to an unsent chat preserves its explicit choice. Starting from a
    # chat or browser uses that visible context, never a hidden global folder.
    if state.get('selectedSessionId') or state['view'].get('workSurface', 'chat') != 'chat':
        location = defaults(state)
        setup.update(workspace=location['workspace'], location=location['location'])
    if 'location' in args:
        setup['location'] = copy.deepcopy(args['location'])
    if 'workspace' in args:
        setup['workspace'] = args['workspace']
        if 'location' not in args:
            setup['location'] = {'kind': 'workspace'}
    if is_managed(setup):
        setup['workspace'] = ''
    state['selectedSessionId'] = None
    # A draft has no Canvas scope; saved artifacts and tabs remain in the chat.
    from .canvas_library import empty
    empty(state)
    state['view']['canvasFocused'] = False
    state['view'].update(newSessionDraft=setup, panel=None, toolbarMenuOpen=False,
                         composerModel={}, composerBundle={})
    client = service.clients.record()
    state['view']['draft'] = client.get('drafts', {}).get('', '') if client else state['view'].get('newChatText', '')


def selection(value):
    """An incomplete choice remains editable, but cannot silently use a different model."""
    validate(value, SELECTION)
    result = {key: item.strip() for key, item in value.items() if item.strip()}
    if value and (not result.get('instance') or not result.get('model')):
        raise ValueError('Choose a provider and model, or use the bundle default.')
    return result


def initial_model(state, args, workspace, bundle):
    """Keep the resolved draft label until the first runtime report arrives.

    This is presentation evidence, not a model pin or proof of worker readiness.
    Only the matching draft context may contribute; configuration changes clear
    this cache before another conversation can inherit it.
    """
    key = json.dumps(['' if is_managed(args) else workspace, args.get('bundle') or '']
                     + (['managed'] if is_managed(args) else []),
                     separators=(',', ':'), ensure_ascii=False)
    defaults = state.get('draftDefaults', {}).get(key, {})
    if defaults.get('phase') != 'ready' or defaults.get('bundle') != bundle:
        return {}
    effective = args.get('selection') or defaults.get('effective') or {}
    result = {key: effective[key] for key in ('instance', 'model', 'effort')
              if isinstance(effective.get(key), str) and effective[key]}
    if not result.get('instance') or not result.get('model'):
        return {}
    provider = next((row for row in defaults.get('providers', [])
                     if row.get('id') == result['instance']), {})
    result['providerLabel'] = provider.get('info', {}).get('display_name') or result['instance']
    return result
