"""One selector for the authenticated user's existing registered history.

This is a workspace/library read scope, not a new permission or tenant system.
Mutation, request-participant and output-owner authority remain independent.
"""

SCOPE = {'enum': ['workspace', 'all']}


def selected(args, default='workspace'):
    value = args.get('scope', default)
    if value not in {'task', 'workspace', 'all'}:
        raise ValueError('Choose task, workspace or all registered-history scope.')
    return value


def includes(owner, target, scope='workspace'):
    return (scope == 'all' or scope == 'task' and target['id'] == owner['id']
            or scope == 'workspace' and target.get('workspace') == owner.get('workspace'))


def require(service, owner_id, target_id, scope='workspace'):
    owner = service._session(owner_id, hydrate=False)
    target = service._session(target_id, hydrate=False)
    if not includes(owner, target, scope):
        from .service import AppError
        raise AppError('Conversation is outside the requested history scope. Use explicit scope=all for registered own-history.', 403)
    return target


# These peer-read actions expose registered conversation text or identity.
# Other peer-read policies (settings, diagnostics, live capabilities) retain
# their own domain checks. Exports/output libraries retain narrower ownership.
SESSION_READS = {
    'session.inspect': 'id', 'session.history': 'id',
    'session.titlePreview': 'sessionId', 'message.copy': 'sessionId',
    'conversation.delivery': 'sessionId', 'history.export': 'sessionId',
}


def guard_action(service, action, args, origin, caller):
    key = SESSION_READS.get(action)
    if key is None or origin in {'ui', 'user'} and 'scope' not in args:
        return
    if origin in {'ui', 'user'}:
        caller = service.state.get('selectedSessionId') or args.get(key)
    if not caller:
        from .service import AppError
        raise AppError('History reads require a registered calling conversation.', 403)
    require(service, caller, args.get(key) or caller, selected(args))
