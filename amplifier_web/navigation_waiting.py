"""Passive navigation facts from exact saved waits, never execution authority."""
from .task_binding import continuation_epoch, dependency_blocked

LIMIT = 16


def dependency(task):
    # Legacy recovery may walk task changes. Navigation requires the current
    # typed proof and never invokes that unbounded history reconstruction.
    return bool(task.get('blockProvenance') is not None and dependency_blocked(task))


# A rebuildable presentation index over commands, including returned results.
# Notification eligibility is deliberately NOT dependency ownership.
ELIGIBLE = """json_extract(receipt,'$.commandAction')='coordination.send'
 AND json_extract(receipt,'$.protocol')=2
 AND json_extract(receipt,'$.subscription.notificationVersion')=2
 AND json_extract(receipt,'$.subscription.supported')=1"""
KEYS = """json_extract(receipt,'$.senderSessionId'),
 json_extract(receipt,'$.subscription.taskId'),
 json_extract(receipt,'$.subscription.interruptionRevision'),
 json_extract(receipt,'$.subscription.taskContinuationEpoch'),
 json_extract(receipt,'$.sourceGenerationId'),
 json_extract(receipt,'$.sourceInputIds[#-1]')"""
TARGET = "json_extract(receipt,'$.target.sessionId')"
TITLE = "substr(json_extract(receipt,'$.target.title'),1,200)"
FACTS = {
    'workspace': '$.workspace', 'delivery': '$.delivery',
    'wait_status': '$.subscription.status', 'wait_open': '$.subscription.open',
    'response_status': '$.response.status', 'response_kind': '$.response.kind',
    'response_outcome': '$.response.outcome', 'response_qualified': '$.response.qualified',
    'response_revision': '$.response.outcomeRevision',
    'terminal_status': '$.terminalOutcome.status', 'terminal_outcome': '$.terminalOutcome.outcome',
    'terminal_revision': '$.terminalOutcome.outcomeRevision',
    'cancellation_status': '$.cancellation.status',
}
COLUMNS = ('id', 'sender', 'task_id', 'interruption', 'epoch', 'generation',
           'input_id', 'target', 'title', *FACTS)
OPEN_WAIT = """wait_open=1 AND wait_status IN ('waiting','backpressured')
 AND response_status IS NULL AND terminal_status IS NULL
 AND cancellation_status IS NULL
 AND delivery IN ('queued','accepted','pending_steer','started','applied')"""


def initialize(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone():
        return
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_dependency_requests'").fetchone():
        return
    db.execute('SAVEPOINT navigation_wait_init')
    try:
        _initialize(db)
        db.execute('RELEASE navigation_wait_init')
    except BaseException:
        db.execute('ROLLBACK TO navigation_wait_init')
        db.execute('RELEASE navigation_wait_init')
        raise


def _initialize(db):
    # Replace only the old derived index, atomically. Canonical receipts and
    # admission/return subscriptions are never rewritten.
    for name in ('insert', 'update', 'delete'):
        db.execute(f'DROP TRIGGER IF EXISTS navigation_wait_{name}')
    db.execute('DROP TABLE IF EXISTS navigation_pending_requests')
    db.execute(f'''CREATE TABLE navigation_dependency_requests(
        id TEXT PRIMARY KEY,sender TEXT,task_id TEXT,interruption INTEGER,epoch INTEGER,
        generation TEXT,input_id TEXT,target TEXT,title TEXT,
        {','.join(FACTS)})''')
    db.execute('CREATE INDEX navigation_dependency_scope ON navigation_dependency_requests(sender,task_id,interruption,epoch,generation,input_id)')
    db.execute(f'CREATE INDEX navigation_dependency_target ON navigation_dependency_requests(target,sender) WHERE {OPEN_WAIT}')
    values = f'{KEYS},{TARGET},{TITLE},' + ','.join(
        f"json_extract(receipt,'{path}')" for path in FACTS.values())
    insert = f"INSERT OR REPLACE INTO navigation_dependency_requests SELECT new.id,{values.replace('receipt,', 'new.receipt,')} WHERE {ELIGIBLE.replace('receipt,', 'new.receipt,')};"
    for event in ('INSERT', 'UPDATE OF receipt'):
        name = 'insert' if event == 'INSERT' else 'update'
        db.execute(f'''CREATE TRIGGER navigation_wait_{name} AFTER {event} ON commands BEGIN
            DELETE FROM navigation_dependency_requests WHERE id=new.id;
            {insert} END''')
    db.execute('''CREATE TRIGGER navigation_wait_delete AFTER DELETE ON commands BEGIN
        DELETE FROM navigation_dependency_requests WHERE id=old.id; END''')
    # One migration/backfill, not a navigation read. Canonical bytes stay intact.
    db.execute(f'INSERT INTO navigation_dependency_requests SELECT id,{values} FROM commands WHERE {ELIGIBLE}')


def affected_sources(db, target):
    """Only open pending subscriptions need recipient lifecycle publication."""
    return {row[0] for row in db.execute(
        f"SELECT DISTINCT sender FROM navigation_dependency_requests WHERE target=? AND {OPEN_WAIT}",
        (target,)) if row[0]}


def wait_epoch(task):
    """Bounded native projection of the last active boundary preceding a block.

    task.block fences automatic continuation. Display may still describe a wait
    saved immediately before that block, but never one from before pause/resume
    or objective replacement. Missing typed history fails closed.
    """
    if not dependency(task):
        return None
    changes = task.get('changes')
    if not isinstance(changes, list):
        return None
    revision = task['revision']
    # Correction-only updates legitimately have no changed-fields row. Their
    # exact typed revisions fill those gaps; unknown gaps remain unavailable.
    corrections = task.get('corrections', [])
    corrections = corrections[-64:] if isinstance(corrections, list) else []
    correction_revisions = {row.get('revision') for row in corrections
                            if isinstance(row, dict) and type(row.get('revision')) is int}
    for row in reversed(changes[-64:]):
        if not isinstance(row, dict):
            return None
        while revision in correction_revisions and row.get('revision', 0) < revision:
            revision -= 1
        if row.get('revision') != revision:
            return None
        fields = row.get('fields') or {}
        status = fields.get('status')
        if status == 'active':
            return revision
        if status not in {None, 'blocked'}:
            return None
        if status == 'blocked' and row.get('origin') != 'agent':
            return None
        if 'objective' in fields:
            return revision if status is None else None
        revision -= 1
    return None


def task_for(state, row):
    task = state.get('runtimeControl', {}).get(row['id'], {}).get('task.get', {}).get('task') or row.get('task') or {}
    display = row.get('_navigationTask') or {}
    if task and all(display.get(key) == task.get(key) for key in ('id', 'revision', 'status')):
        return display
    return task


def saved_task(db, sid):
    """Read fixed scalar current facts, not the cold runtime/history body."""
    import json
    row = db.execute('SELECT value FROM history_navigation_tasks WHERE id=?', (sid,)).fetchone()
    if row:
        return json.loads(row[0])
    # Pre-upgrade directory rows may know only blocked/paused, not exact task
    # provenance. Keep that honest generic status without parsing old runtime.
    legacy = db.execute('SELECT base_activity_label FROM history_local_rows WHERE id=?', (sid,)).fetchone()
    return {'status': 'blocked'} if legacy and legacy[0] in {
        'Work blocked', 'Blocked — details unavailable'} else (
        {'status': 'paused'} if legacy and legacy[0] == 'Paused' else {})


def save_task(db, sid, task):
    """Derived scalar metadata owned by the existing directory transaction."""
    import json
    keys = ('id', 'status', 'revision', 'continuationEpoch', 'blockProvenance', 'dependencyWaitEpoch')
    value = json.dumps({**{key: task.get(key) for key in keys},
                        **operation_refs(task)}, separators=(',', ':'))
    db.execute('''INSERT INTO history_navigation_tasks VALUES(?,?)
        ON CONFLICT(id) DO UPDATE SET value=excluded.value WHERE value!=excluded.value''', (sid, value))


def refresh_directory(db):
    """One atomic presentation migration; only bounded directory/task metadata.

    First upgrade visits stale derived rows in 128-row pages. No task runtime,
    transcript or command body is hydrated; restart checks only the marker.
    """
    import json
    db.execute('CREATE TABLE IF NOT EXISTS navigation_fact_versions(version INTEGER PRIMARY KEY)')
    if db.execute('SELECT 1 FROM navigation_fact_versions WHERE version=2').fetchone():
        return
    db.execute('SAVEPOINT navigation_fact_refresh')
    try:
        after = ''
        changed = False
        while True:
            rows = db.execute("""SELECT id,status,workspace,
                json_extract(summary,'$._navigationInput'),
                json_extract(summary,'$._navigationEndedGeneration'),
                json_extract(summary,'$.interruptionRevision'),
                base_activity_kind,base_activity_label
                FROM history_local_rows WHERE id>? AND
                base_activity_label IN ('Work blocked','Waiting for another chat')
                ORDER BY id LIMIT 128""", (after,)).fetchall()
            if not rows:
                break
            for sid, status, workspace, boundary, ended, interruption, kind, label in rows:
                session = {'id':sid, 'status':status, 'workspace':workspace,
                           '_navigationInput':json.loads(boundary) if boundary else None,
                           '_navigationEndedGeneration':ended,
                           'interruptionRevision':interruption or 0}
                from .navigation_summary import activity
                facts = activity(session, task=saved_task(db,sid), blocked=label=='Work blocked', db=db)
                db.execute("""UPDATE history_local_rows SET base_activity_kind=?,base_activity_label=?,
                    activity_kind=?,activity_label=?,
                    summary=json_set(summary,'$._navigationWaiting',json(?)) WHERE id=?""",
                    (facts['kind'],facts['label'],facts['kind'],facts['label'],
                     json.dumps(facts.get('waiting'),separators=(',',':')),sid))
                db.execute('DELETE FROM history_observation_rows WHERE id=?',(sid,))
                changed = True
            after = rows[-1][0]
        if changed:
            db.execute('UPDATE history_directory_layout SET revision=revision+1 WHERE version=1')
            db.execute("""UPDATE history_observation_revisions SET revision=revision+1
                WHERE collection IN ('navigation','root_navigation','workspace')""")
        db.execute('INSERT INTO navigation_fact_versions VALUES(2)')
        db.execute('RELEASE navigation_fact_refresh')
    except BaseException:
        db.execute('ROLLBACK TO navigation_fact_refresh')
        db.execute('RELEASE navigation_fact_refresh')
        raise


def operation_refs(task):
    """Existing owner-authored references, bounded before cold persistence."""
    refs = task.get('operationIds')
    if not isinstance(refs, list):
        return {}
    return {'operationIds': [ref for ref in refs[:LIMIT + 1]
                             if isinstance(ref, str) and 0 < len(ref) <= 200],
            'operationIdsCount': task.get('operationIdsCount', len(refs))}


def disposition(row):
    """Fixed labels over saved scalar facts; no report/reason text inference."""
    delivery = row['delivery']
    if delivery == 'unknown':
        return 'unknown', 'Dependency outcome unknown', 'attention'
    cancellation = row['cancellation_status']
    if cancellation and delivery in {'not_sent', 'suppressed', 'expired', 'held', 'cancelled'}:
        return None  # Confirmed withdrawal, not an unknown/admitted effect.
    if delivery in {'rejected', 'not_sent', 'expired', 'failed'}:
        return 'failed', 'Dependency request failed', 'attention'
    status, outcome, qualified = row['response_status'], row['response_outcome'], row['response_qualified']
    if row['response_kind'] == 'ack':
        status = outcome = qualified = None
    if row['terminal_status'] == 'observed' and (
            status is None or (row['terminal_revision'] or 0) >= (row['response_revision'] or 0)):
        status, outcome, qualified = 'observed', row['terminal_outcome'], False
    from .request_cancellation import terminal_report
    final_result = terminal_report({'status': status, 'kind': row['response_kind'], 'outcome': outcome})
    final_admission = row['terminal_status'] == 'observed' and outcome in {'failed', 'cancelled', 'declined'}
    if cancellation and not (final_result or final_admission):
        return 'cancellation_requested', 'Dependency cancellation unconfirmed', 'attention'
    if status == 'unknown' or outcome == 'unknown':
        return 'unknown', 'Dependency outcome unknown', 'attention'
    if outcome in {'declined', 'cancelled'}:
        return outcome, 'Dependency ' + outcome, 'attention'
    if status == 'rejected' or outcome == 'failed':
        return 'failed', 'Dependency failed', 'attention'
    if outcome in {'question', 'conflict'}:
        return 'decision', 'Dependency decision requested', 'attention'
    if status in {'staged', 'sealed', 'observed'} and outcome == 'success':
        # A sealed success is a report, not independent acceptance or task completion.
        return ('returned', 'Dependency result ready', 'waiting') if qualified else (
            'reported', 'Dependency result reported', 'waiting')
    if outcome == 'deferred':
        return 'deferred', 'Dependency deferred', 'waiting'
    if (row['wait_open'] == 1 and row['wait_status'] in {'waiting', 'backpressured', 'claimed'}
            and delivery in {'queued', 'accepted', 'pending_steer', 'started', 'applied', 'held'}):
        return ('backpressured', 'Waiting for dependency delivery', 'waiting') if row['wait_status'] == 'backpressured' else (
            'pending', 'Waiting for another chat', 'waiting')
    return None


def input_boundary(session):
    """Content-free, exact host observations; not a new admission fence."""
    generation = session.get('collaborationGeneration') or {}
    if not generation:
        boundary = session.get('_navigationInput')
    else:
        inputs = generation.get('inputIds') or []
        boundary = [generation['id'], inputs[-1]] if generation.get('id') and inputs else None
    if (not isinstance(boundary, list) or len(boundary) != 2
            or not all(isinstance(item, str) and item for item in boundary)
            or session.get('_navigationEndedGeneration') == boundary[0]):
        return None
    return boundary


def project(db, session, task):
    if (db is None or not dependency(task)
            or session.get('status') in {'stopped', 'cancelled', 'interrupted', 'error', 'failed', 'paused'}):
        return None
    epochs = [continuation_epoch(task)]
    prior = task.get('dependencyWaitEpoch')
    if type(prior) is int and prior not in epochs:
        epochs.append(prior)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_dependency_requests'").fetchone():
        return None
    boundary = input_boundary(session)
    rows = []
    refs = operation_refs(task).get('operationIds', [])
    # Explicit owner references may outlive the model callback, but retain the
    # exact sender/task/pre-block epoch, interruption and workspace boundaries.
    if refs:
        refs = [ref for ref in refs if isinstance(ref, str) and 0 < len(ref) <= 200]
        rows.extend(db.execute(f"""SELECT {','.join(COLUMNS)} FROM navigation_dependency_requests
            WHERE id IN ({','.join('?' for _ in refs)}) AND sender=? AND task_id=?
            AND interruption=? AND epoch IN ({','.join('?' for _ in epochs)})
            LIMIT ?""", (*refs, session['id'], task['id'],
                         session.get('interruptionRevision', 0), *epochs, LIMIT + 1)).fetchall())
    # Implicit association still needs exact current input/generation. Reading
    # navigation never promotes an old callback into execution authority.
    for value in epochs if boundary else []:
        if len(rows) > LIMIT:
            break
        excluded = [row[0] for row in rows]
        rows.extend(db.execute(f"""SELECT {','.join(COLUMNS)} FROM navigation_dependency_requests
            WHERE sender=? AND task_id=? AND interruption=?
            AND epoch=? AND generation=? AND input_id=?
            AND id NOT IN ({','.join('?' for _ in excluded)})
            LIMIT ?""", (session['id'], task['id'], session.get('interruptionRevision', 0),
                         value, *boundary, *excluded, LIMIT + 1 - len(rows))).fetchall())
    targets = {}
    phases = []
    for raw in rows[:LIMIT]:
        row = dict(zip(COLUMNS, raw))
        if row['workspace'] != session.get('workspace'):
            continue
        state = disposition(row)
        if state is None:
            continue
        phase, label, kind = state
        sid, title = row['target'], row['title']
        if isinstance(sid, str) and sid and sid != session['id']:
            phases.append(state)
            target = targets.setdefault(sid, {'sessionId': sid, 'title': title or 'Conversation',
                                             'phase': phase, 'label': label})
            if kind == 'attention':
                target.update(phase=phase, label=label)
    if not targets:
        return None
    phase, label, kind = next((item for item in phases if item[2] == 'attention'), phases[0])
    if kind == 'waiting' and len({item[0] for item in phases}) > 1:
        phase, label = 'mixed', 'Waiting on chat dependencies'
    return {'targets': list(targets.values()),
            'hasMore': len(rows) > LIMIT or task.get('operationIdsCount', 0) > LIMIT,
            'basis': 'saved-request-subscription', 'phase': phase, 'label': label, 'kind': kind}


def target_details(db, waiting):
    """One bounded keyed metadata read for the selected chat only."""
    targets = waiting['targets']
    ids = [row['sessionId'] for row in targets]
    rows = db.execute(f"SELECT id,title,status,available FROM history_local_rows WHERE id IN ({','.join('?' for _ in ids)})",
                      ids).fetchall()
    known = {sid: {'title': title[:200], 'status': status, 'available': bool(available)}
             for sid, title, status, available in rows}
    return {**waiting, 'targets': [{**row, **known.get(row['sessionId'], {'available': False})} for row in targets]}
