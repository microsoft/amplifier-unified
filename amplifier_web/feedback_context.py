"""Existing Full authority retained by feedback, independent of a browser draft."""
from .collaboration import PRINCIPAL


def enabled(service, origin, caller):
    return bool(origin in {'agent', 'system'} and caller
                and service.access_mode.full(service._session(caller, hydrate=False)))


def capture(service, origin, caller):
    if not enabled(service, origin, caller):
        return None
    source = service._session(caller, hydrate=False)
    authority = {
        'origin': origin, 'sessionId': source['id'],
        'accessRevision': service.access_mode.snapshot(source)['revision'],
        'workspace': source['workspace'],
        'nativeSessionId': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'],
        'executionRevision': source.get('executionRevision', 0),
        'interruptionRevision': source.get('interruptionRevision', 0),
    }
    if origin == 'agent':
        binding, active = service.collaboration.active_binding(source)
        authority.update(runtimeSessionId=PRINCIPAL.get(), generationId=active['id'],
                         inputIds=[row['inputId'] for row in binding.get('_inputBindings', [])
                                   if row.get('inputId') in active.get('inputIds', [])])
    return authority


def check(service, authority):
    """Accepted background delivery outlives a turn, but never its Stop boundary."""
    from .service import AppError
    source = service._session(authority['sessionId'], hydrate=False)
    if (not service.access_mode.full(source, authority['accessRevision'])
            or source['workspace'] != authority['workspace']
            or (source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']) != authority['nativeSessionId']
            or any(source.get(key, 0) != authority[key]
                   for key in ('executionRevision', 'interruptionRevision'))):
        raise AppError('Feedback authority changed or its work was stopped. No further writes were started.', 409)


def rows(service, owner):
    """Only the bounded selected initial files; never hydrate another draft."""
    import json
    return [json.loads(row[0]) for row in service.db.execute(
        "SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$._sessionId')=? "
        "AND json_extract(metadata,'$._selected')=1 AND json_extract(metadata,'$._feedbackId') IS NULL",
        (owner,))]


def public_file(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}
