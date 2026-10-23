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
    management = getattr(service, 'management', None)
    configuration = getattr(management, 'maintenance_blockers', None)
    if configuration is not None:
        result.extend(configuration())
    result.extend(test_work_blockers(service))
    return result


def test_work_blockers(service):
    """Project bounded owner evidence; never probe or cancel a test machine."""
    from contextlib import closing
    from pathlib import Path
    import sqlite3
    from . import test_environment_setup as setup
    result = []
    queue = getattr(service, 'test_queue', None)
    if queue is not None and any(not task.done() for task in queue.tasks.values()):
        result.append({'kind': 'test-environments', 'label': 'An isolated check is preparing, running or settling'})
    home = getattr(service, 'data_dir', None)
    if home is None:
        return result
    ledger = Path(home) / 'workspace-resources' / 'reservations.sqlite3'
    if ledger.exists():
        try:
            if ledger.is_symlink() or ledger.parent.is_symlink():
                raise ValueError('The test owner ledger changed.')
            with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
                active = db.execute("""SELECT 1 FROM allocations WHERE status!='retired'
                    AND json_extract(body,'$.kind')='test-environment' AND (
                    json_extract(body,'$.preparationPending')=1
                    OR json_extract(body,'$.pendingRequest') IS NOT NULL
                    OR (json_extract(body,'$.backendAdmitted')=1
                        AND COALESCE(json_extract(body,'$.environmentState'),'unknown') NOT IN ('stopped','removed','not-started'))) LIMIT 1""").fetchone()
            if active and not result:
                result.append({'kind': 'test-environments', 'label': 'An isolated test machine still owns active or unconfirmed work'})
        except (OSError, ValueError, sqlite3.Error):
            result.append({'kind': 'test-environments', 'label': 'Isolated test ownership could not be confirmed'})
    setup_busy = any(not task.done() for task in getattr(service, '_test_setup_tasks', {}).values())
    try:
        folder = setup.root(service, create=False)
        latest = setup.read_json(folder / 'latest.json') if folder.exists() else None
        record = setup.read_json(folder / (latest['id'] + '.json')) if latest else None
        setup_busy = setup_busy or bool(record and record.get('state') in setup.ACTIVE | {'outcome_unknown'}
            and record.get('cleanup', {}).get('state') != 'completed')
    except (OSError, ValueError, KeyError, AttributeError):
        setup_busy = True
    if setup_busy:
        result.append({'kind': 'test-setup', 'label': 'Test runner setup or cleanup still needs confirmation'})
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
            elif not _quiescent_legacy_settings_hold(service, session):
                pending = session.get('pendingConfiguration') or {}
                legacy = (not pending.get('operationId') and not pending.get('requestHash')
                          and (pending.get('recoveryRequired') or pending.get('error')))
                add('configuration', 'Earlier conversation settings need inspection; no change is being replayed'
                    if legacy else 'Conversation settings are being applied', sid)
        if session.get('status') in {'working', 'starting', 'stopping'} or (session.get('status') == 'ready' and
                any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', []))):
            add('conversation', 'Conversation work is running or finishing', sid)
        for worker in session.get('workers', []):
            if worker.get('status') in {'starting', 'running', 'stopping', 'queued'}:
                add('worker', 'Background worker work is running or queued', sid)
            elif worker.get('persistent') and worker.get('status') == 'idle':
                add('worker', 'A persistent background worker is still open', sid)
    return result
