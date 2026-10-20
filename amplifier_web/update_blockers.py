"""The update safety gate and its user-visible reasons share one source."""


def auxiliary_blockers(service):
    """Bounded categories from canonical host owners, without hydrating chats."""
    state = service.state
    result = []
    if state.get('voicePreviewBusy'):
        result.append({'kind': 'voice', 'label': 'Voice preview is playing'})
    voice = state.get('voice', {})
    if voice.get('status') not in {None, 'disconnected', 'idle', 'ended', 'error'}:
        result.append({'kind': 'voice', 'label': 'A voice call is active',
                       **({'sessionId': voice['sessionId']} if voice.get('sessionId') else {})})
    operations = state.get('smartTools', {}).get('operations', [])
    if (any(op.get('status') in {'queued', 'running'} for op in operations)
            or any(not task.done() for task in getattr(service, 'smart_tool_tasks', ()))):
        result.append({'kind': 'smart-tools', 'label': 'A Smart Tool operation is still running'})
    naming = getattr(service, 'naming_backfill', None)
    if naming is not None and naming.running:
        result.append({'kind': 'naming', 'label': 'Chat naming is still running'})
    return result


def blockers(service):
    state = service.state
    result = []
    sessions = {row['id']: row for row in state.get('sessions', [])}

    def add(kind, label, sid=None):
        session = sessions.get(sid, {})
        target = session.get('parentId') or sid
        parent = sessions.get(target, session)
        row = {'kind': kind, 'label': label}
        if target:
            row.update(sessionId=target, title=parent.get('title') or 'Open conversation')
        if row not in result:
            result.append(row)

    for row in auxiliary_blockers(service):
        add(row['kind'], row['label'], row.get('sessionId'))
    runtime = getattr(service, 'runtime', None)
    pending = getattr(runtime, 'has_pending_operations', None)
    if pending and pending():
        identify = getattr(runtime, 'pending_session_ids', None)
        ids = identify() if identify else []
        for sid in ids:
            add('runtime', 'Preparing or finishing conversation work', sid)
        if not ids:
            add('runtime', 'Preparing or finishing background runtime work')
    for key in ('requests', 'followups'):
        if any(row.get('status') in {'queued', 'sending'} for row in state.get('feedback', {}).get(key, [])):
            add('feedback', 'Feedback delivery is in progress')
    for session in sessions.values():
        sid = session['id']
        if session.get('configurationBusy'):
            unknown = any(row.get('phase') == 'unknown' for row in session.get('worktreeHandoffs', []))
            if unknown:
                add('checkout-handoff', 'Checkout handoff needs inspection; restarting will not resolve it', sid)
            else:
                add('configuration', 'Conversation settings are being applied', sid)
        if session.get('status') in {'working', 'starting', 'stopping'} or (session.get('status') == 'ready' and
                any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', []))):
            add('conversation', 'Conversation work is running or finishing', sid)
        for worker in session.get('workers', []):
            if worker.get('status') in {'starting', 'running', 'stopping', 'queued'}:
                add('worker', 'Background worker work is running or queued', sid)
            elif worker.get('persistent') and worker.get('status') == 'idle':
                add('worker', 'A persistent background worker is still open', sid)
    return result
