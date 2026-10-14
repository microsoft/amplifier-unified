"""Browser-only observation summaries; durable workers/receipts stay untouched."""

TERMINAL = {'completed', 'cancelled', 'error', 'failed', 'stopped', 'interrupted'}
OVERVIEW_ACTIVE = {'queued', 'starting', 'working', 'running', 'stopping'}


def key(row, index):
    return row.get('callId') or row.get('call_id') or row.get('id') or ('ordinal', index)


def project(rows, execution):
    # This is exactly the existing frontend legacy-timeline condition. A focused
    # empty window later remains authoritative via the bounded marker.
    if not execution.get('nodes') and not execution.get('turns'):
        return rows, None
    retained = {key(row, i) for i, row in enumerate(rows)
                if row.get('status') not in TERMINAL or not row.get('id')}
    # Keep the entire call group: terminal job evidence overrides late progress,
    # while the session observation supplies the child identity for controls.
    visible = [row for i, row in enumerate(rows) if key(row, i) in retained]
    finished = [row for row in rows if row.get('status') not in OVERVIEW_ACTIVE]
    summary = {'bounded': True, 'total': len(rows), 'finished': len(finished),
               'recentFinished': [{k: row[k] for k in ('id', 'name', 'status') if k in row}
                                  for row in finished[-5:]]}
    return visible, summary


def recovery_workers(session, messages):
    """First-match identity for each requested recovery window, not live status."""
    calls = {m.get('observation', {}).get('recovery', {}).get('call_id')
             or m.get('observation', {}).get('call_id') for m in messages
             if m.get('observation', {}).get('source') == 'local-job-recovery'}
    calls.discard(None)
    if not calls:
        return
    found = {}
    for row in session.get('workers', []):
        for call in (row.get('callId'), row.get('toolCallId')):
            if call in calls and call not in found:
                found[call] = {key: row[key] for key in ('id', 'sessionId', 'name', 'agent', 'kind') if key in row}
                if row.get('kind') == 'session' or row.get('sessionId'):
                    from .session_identity import project
                    scope = project(session)
                    if scope:
                        found[call]['nativeProject'] = scope
    for message in messages:
        observation = message.get('observation', {})
        if observation.get('source') == 'local-job-recovery':
            call = observation.get('recovery', {}).get('call_id') or observation.get('call_id')
            if call in found:
                message['recoveryWorker'] = dict(found[call])