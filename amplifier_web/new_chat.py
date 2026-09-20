"""Client-local setup for a chat that does not exist until its first submission."""
import copy

from jsonschema import validate

SELECTION = {'type': 'object', 'properties': {
    'instance': {'type': 'string', 'maxLength': 200},
    'model': {'type': 'string', 'maxLength': 500},
    'effort': {'type': 'string', 'maxLength': 100}}, 'additionalProperties': False}
SETUP = {'type': 'object', 'properties': {
    'title': {'type': 'string', 'maxLength': 200},
    'workspace': {'type': 'string', 'maxLength': 4000},
    'bundle': {'type': 'string', 'maxLength': 2000},
    'selection': SELECTION}, 'additionalProperties': False}


def defaults(state):
    workspace = next((w for w in state.get('workspaces', [])
                      if w['id'] == state.get('selectedWorkspaceId')), {})
    return {'title': '', 'workspace': workspace.get('path') or state['settings'].get('workspace', ''),
            'bundle': '', 'selection': {}}


def validate_setup(value):
    validate(value, SETUP)
    return copy.deepcopy(value)


def open_draft(service, args):
    state = service.state
    setup = copy.deepcopy(state['view'].get('newSessionDraft') or defaults(state))
    if 'workspace' in args:
        setup['workspace'] = args['workspace']
    state['selectedSessionId'] = None
    state['view'].update(newSessionDraft=setup, panel=None, toolbarMenuOpen=False,
                         composerModel={}, composerBundle={})
    client = service.clients.record()
    state['view']['draft'] = client.get('drafts', {}).get('', '') if client else state['view'].get('newChatText', '')


def selection(value):
    """An incomplete choice remains editable, but cannot silently use a different model."""
    validate(value, SELECTION)
    result = {key: item.strip() for key, item in value.items() if item.strip()}
    if result and (not result.get('instance') or not result.get('model')):
        raise ValueError('Choose a provider and model, or use the bundle default.')
    return result
