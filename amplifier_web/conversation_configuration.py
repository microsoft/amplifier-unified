"""Explicit Full-root administration of saved choices, never another execution."""
from pathlib import Path


def binding(service, session):
    from .next_message_configuration import target
    return {**service.management.configuration_target_binding(session), **target(session),
            'nativeSessionId': session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']}


def saved_binding(value):
    from .next_message_configuration import target
    # Normalize only legacy placement defaults, preserving native identity,
    # interruption and every other originally captured authority witness.
    return {**value, **target(value)}


def capture(service, action, args, origin, caller):
    """Opt in only next-message choices and registered workspace bundle defaults."""
    if origin in {'ui', 'user'}:
        return None
    peer = action == 'conversation.configure' and args.get('sessionId') != caller
    workspace = action == 'bundle.default' and args.get('scope') == 'workspace' and args.get('workspace')
    if not peer and not workspace:
        return None
    source = service.configuration_authority.source(
        {'sessionId': caller}, origin, caller, active=True, allow_descendant=True)
    destination = str(Path(workspace).expanduser().resolve()) if workspace else None
    if not peer and destination == source['workspace']:
        return None  # Existing owner-local grants and children retain their path.
    from .collaboration import BINDING
    if origin != 'agent' or '_childOrigin' in (BINDING.get() or {}):
        service.configuration_authority.error('Other-chat configuration requires an authenticated Full root operator.')
    service.collaboration.active_binding(source)  # No descendant root impersonation.
    authority = service.access_mode.capture_full(source, origin)
    if authority is None or authority.get('inputCoverage') != 'complete':
        service.configuration_authority.error('Other-chat configuration requires current Full access and authenticated assigned inputs.')
    effect, scope = ('bundles' if 'bundle' in args else 'model_defaults', 'session') if peer else ('bundles', 'local')
    authority.update(workspace=source['workspace'], interruptionRevision=source.get('interruptionRevision', 0),
                     effect=effect, scope=scope)
    witness = {'caller': binding(service, source)}
    if peer:
        target = service.collaboration.authorize(source, args, hydrate=False, cross_workspace=True)
        witness['target'] = binding(service, target)
        destination = target['workspace']
    else:
        row = next((row for row in service.state.get('workspaces', [])
                    if row.get('path') == destination and row.get('available')), None)
        if row is None:
            service.configuration_authority.error('Choose an existing registered workspace.')
        witness['workspaceId'] = row['id']
    witness['workspace'] = destination
    peers = service.configuration_authority.full_peer_authorities(
        source, authority['inputIds'], effect=effect, scope=scope, require_full=True)
    return {'operatorConfiguration': witness, 'chain': [authority, *peers]}


def check(service, authority, *, workspace=None, scope=None):
    """Check frozen identities before commit and again on genuine target input."""
    witness = authority['operatorConfiguration']
    source = service._session(witness['caller']['sessionId'], hydrate=False)
    if (binding(service, source) != saved_binding(witness['caller'])
            or source.get('configurationBusy') or source.get('fileAccessPending')
            or service.management.unresolved_configuration(source) or service.assignments.blocked(source)
            or workspace is not None and str(workspace) != witness['workspace']
            or scope is not None and scope != authority['chain'][0]['scope']):
        service.configuration_authority.error('The configuration operator or explicit target changed. Nothing was repeated.')
    if 'target' in witness:
        target = service._session(witness['target']['sessionId'], hydrate=False)
        if (binding(service, target) != saved_binding(witness['target']) or target.get('sessionKind', 'root') != 'root'
                or target.get('fileAccessPending') or target.get('historyError')
                or not Path(target['workspace']).is_dir()):
            service.configuration_authority.error('The saved configuration target moved or crossed Stop.')
    else:
        row = next((row for row in service.state.get('workspaces', [])
                    if row['id'] == witness['workspaceId']), None)
        if not row or row.get('path') != witness['workspace'] or not Path(row['path']).is_dir():
            service.configuration_authority.error('The registered workspace target changed.')
    for item in authority['chain']:
        # The actor's workspace is not the destination. Do not lend target identity.
        service.configuration_authority.check(item)


def saved_session(service, identity):
    """Read one committed owner without admitting it or following body pointers."""
    from .errors import AppError
    resident = next((row for row in service.state['sessions'] if row['id'] == identity), None)
    if resident is not None:
        if resident.get('_deleting'):
            raise AppError('This chat is being deleted.', 409)
        return resident
    import json
    limit = 1024 * 1024
    row = service.db.execute(
        """SELECT length(CAST(value AS BLOB)),
           CASE WHEN length(CAST(value AS BLOB))<=? THEN value END
           FROM state_records WHERE kind='session' AND id=?""", (limit, identity)).fetchone()
    try:
        if row and row[0] > limit:
            raise ValueError('Oversized settings manifest')
        record = json.loads(row[1]) if row else None
    except (ValueError, TypeError):
        raise AppError('Saved conversation settings are unavailable within the bounded read.', 409) from None
    if record is not None:
        if not isinstance(record, dict) or record.get('id') != identity:
            raise AppError('Saved conversation settings cannot be verified.', 409)
        reference = record.get('$viewPayload')
        if record.get('$view') and not reference:
            raise AppError('Saved conversation settings need an ordinary legacy view migration before bounded read.', 409)
        if reference:
            from .resource_files import detach, read_detached
            # Bounded single-owner metadata, never referenced transcript/execution
            # resources. Oversized/unavailable evidence stays explicitly unreadable.
            try:
                record = read_detached(detach(service.db, reference['$resource']), max_bytes=limit)
            except (OSError, ValueError, KeyError):
                raise AppError('Saved conversation settings are unavailable within the bounded read.', 409) from None
        if not isinstance(record, dict) or record.get('id') != identity:
            raise AppError('Saved conversation settings cannot be verified.', 409)
        return record
    rows = service.directory.summary_ids([identity])
    if not rows:
        raise AppError('Choose a registered conversation.', 404)
    return rows[0]  # Native-only root has no app-owned next-message override.


def read(service, args):
    """Small saved-state projection; no private plans, authority or cold history."""
    import copy
    session = saved_session(service, args['sessionId'])
    pending = session.get('pendingBundleSelection')
    blocked = ('read-only' if session.get('historyReadOnlyReason') or session.get('sessionKind', 'root') != 'root'
               else 'configuration-held' if session.get('configurationBusy') or session.get('configurationOperationFence')
               or service.management.unresolved_configuration(session)
               or session.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}
               else 'ownership' if session.get('ownership', {}).get('status') in
               {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'} else None)
    return {'sessionId': session['id'], 'nextMessageRevision': session.get('nextMessageRevision', 0),
            'bundle': session.get('bundle'), 'selection': copy.deepcopy(session.get('selection')),
            'pendingBundleSelection': ({'bundle': pending['bundle'], 'resetModel': pending.get('resetModel', False)}
                                       if pending else None),
            'pendingModelSelection': copy.deepcopy(session.get('pendingModelSelection')),
            'status': session.get('nextMessageStatus'), 'blockedReason': blocked,
            'applies': 'next-message', 'mounted': False}