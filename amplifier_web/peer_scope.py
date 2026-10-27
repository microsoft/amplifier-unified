"""Host-derived peer locations; messaging is not placement or file authority."""
from pathlib import PurePath


def location(session):
    """Do not expose a managed chat's private storage path as a workspace."""
    if (session.get('location') or {}).get('kind') == 'managed':
        return {'kind': 'managed'}
    return {'kind': 'workspace', 'path': session.get('workspace')}


def location_label(value):
    if value.get('kind') == 'managed':
        return 'No workspace'
    path = value.get('path')
    return (PurePath(path).name or path) if path else 'Workspace unavailable'


def descriptor(session):
    value = location(session)
    title = ''.join(c for c in (session.get('title') or 'Untitled chat')
                    if ord(c) >= 32 and ord(c) != 127)[:200]
    return {'sessionId': session['id'], 'title': title or 'Untitled chat',
            'location': value, 'workspaceLabel': location_label(value),
            'status': session.get('status', 'unknown')}


def participant_workspace(request, identity):
    """Old receipts bind both participants to their original single workspace."""
    if not valid_binding(request):
        raise ValueError('The exact request location binding is invalid.')
    if identity == request.get('senderSessionId'):
        return request.get('workspace')
    if identity == (request.get('target') or {}).get('sessionId'):
        return request['target'].get('workspace', request.get('workspace'))
    raise ValueError('This conversation is not a participant in the exact request.')


def valid_binding(request):
    """Partial new records must not downgrade into broader legacy authority."""
    if not isinstance(request, dict) or not isinstance(request.get('target'), dict):
        return False
    target = request['target']
    if not all(isinstance(value, str) and value.strip() for value in
               (request.get('senderSessionId'), target.get('sessionId'), request.get('workspace'))):
        return False
    if 'endpointBindingVersion' not in request:
        return not any(key in request for key in ('senderLocation', 'recipientLocation')) and 'workspace' not in target
    if type(request['endpointBindingVersion']) is not int or request['endpointBindingVersion'] != 1:
        return False
    if not isinstance(target.get('workspace'), str) or not target['workspace'].strip():
        return False
    for key, workspace in (('senderLocation', request['workspace']), ('recipientLocation', target['workspace'])):
        value = request.get(key)
        if value not in ({'kind': 'managed'}, {'kind': 'workspace', 'path': workspace}):
            return False
    if request['senderSessionId'] == target['sessionId']:
        return request['workspace'] == target['workspace'] and request['senderLocation'] == request['recipientLocation']
    return True


def participant_matches(request, session):
    if not isinstance(session, dict) or not session.get('id'):
        return False
    try:
        if session.get('workspace') != participant_workspace(request, session['id']):
            return False
    except ValueError:
        return False
    key = 'senderLocation' if session['id'] == request.get('senderSessionId') else 'recipientLocation'
    return key not in request or location(session) == request[key]