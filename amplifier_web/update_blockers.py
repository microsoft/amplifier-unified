"""The update safety gate and its user-visible reasons share one source."""


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

    if state.get('voicePreviewBusy'):
        add('voice', 'Voice preview is playing')
    runtime = getattr(service, 'runtime', None)
    pending = getattr(runtime, 'has_pending_operations', None)
    if pending and pending():
        identify = getattr(runtime, 'pending_session_ids', None)
        ids = identify() if identify else []
        for sid in ids:
            add('runtime', 'Preparing or finishing conversation work', sid)
        if not ids:
            add('runtime', 'Preparing or finishing background runtime work')
    operations = [op for op in state.get('smartTools', {}).get('operations', []) if op.get('status') in {'queued', 'running'}]
    if operations or any(not task.done() for task in getattr(service, 'smart_tool_tasks', ())):
        add('smart-tools', 'A Smart Tool operation is still running')
    for key in ('requests', 'followups'):
        if any(row.get('status') in {'queued', 'sending'} for row in state.get('feedback', {}).get(key, [])):
            add('feedback', 'Feedback delivery is in progress')
    if state.get('voice', {}).get('status') not in {None, 'disconnected', 'idle', 'ended', 'error'}:
        add('voice', 'A voice call is active', state['voice'].get('sessionId'))
    for session in sessions.values():
        sid = session['id']
        if session.get('configurationBusy'):
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
