"""The update safety gate and its user-visible reasons share one source."""


def _quiescent_legacy_settings_hold(service, session):
    """Retain the chat fence without mistaking an old recovery hold for work.

    This is only an update-idleness observation. It never clears the fence,
    resolves unknown effects, replays settings, or permits a new chat turn.
    Modern operation-bound recovery and unobservable execution remain guarded.
    """
    pending = session.get('pendingConfiguration', {})
    if (session.get('status') not in {'idle', 'stopped', 'interrupted', 'error'}
            or pending.get('phase') != 'error' or pending.get('recoveryRequired') is not True
            or pending.get('operationId') or session.get('configurationOperationFence')
            or session.get('configurationPending')
            or session.get('configurationRefresh', {}).get('phase') == 'applying'
            or any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', []))
            or any(worker.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                   or worker.get('persistent') and worker.get('status') == 'idle'
                   for worker in session.get('workers', []))):
        return False
    management = getattr(service, 'management', None)
    tasks = getattr(management, 'pending_config_tasks', None)
    lock = getattr(management, 'lock', None)
    runtime_pending = getattr(getattr(service, 'runtime', None), 'has_pending_operations', None)
    if tasks is None or lock is None or lock.locked() or not callable(runtime_pending):
        return False
    task = tasks.get(session['id'])
    return not (task is not None and not task.done()) and not runtime_pending()


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
            unknown = any(row.get('phase') == 'unknown' for row in session.get('worktreeHandoffs', []))
            if unknown:
                add('checkout-handoff', 'Checkout handoff needs inspection; restarting will not resolve it', sid)
            elif not _quiescent_legacy_settings_hold(service, session):
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
