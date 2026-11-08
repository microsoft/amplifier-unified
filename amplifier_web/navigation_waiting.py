"""Passive navigation facts from exact saved waits, never execution authority."""
from .task_binding import continuation_epoch, dependency_blocked

LIMIT = 16


def dependency(task):
    # Legacy recovery may walk task changes. Navigation requires the current
    # typed proof and never invokes that unbounded history reconstruction.
    return bool(task.get('blockProvenance') is not None and dependency_blocked(task))


# This partial, covering index excludes settled/unknown/cancelled requests before
# pagination. Routine reads neither decode receipt bodies nor scan old commands.
PENDING = """json_extract(receipt,'$.commandAction')='coordination.send'
 AND json_extract(receipt,'$.protocol')=2
 AND json_extract(receipt,'$.subscription.notificationVersion')=2
 AND json_extract(receipt,'$.subscription.supported')=1
 AND json_extract(receipt,'$.subscription.status')='waiting'
 AND json_extract(receipt,'$.subscription.open')=1
 AND json_extract(receipt,'$.response') IS NULL
 AND json_extract(receipt,'$.terminalOutcome') IS NULL
 AND json_extract(receipt,'$.delivery') IN ('queued','accepted','pending_steer','started','applied')
 AND json_extract(receipt,'$.cancellation') IS NULL"""
KEYS = """json_extract(receipt,'$.senderSessionId'),
 json_extract(receipt,'$.subscription.taskId'),
 json_extract(receipt,'$.subscription.interruptionRevision'),
 json_extract(receipt,'$.subscription.taskContinuationEpoch'),
 json_extract(receipt,'$.sourceGenerationId'),
 json_extract(receipt,'$.sourceInputIds[#-1]')"""
TARGET = "json_extract(receipt,'$.target.sessionId')"
TITLE = "substr(json_extract(receipt,'$.target.title'),1,200)"


def initialize(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone():
        db.execute(f"CREATE INDEX IF NOT EXISTS navigation_pending_waits ON commands({KEYS},{TARGET},{TITLE}) WHERE {PENDING}")
        db.execute(f"CREATE INDEX IF NOT EXISTS navigation_waiting_target ON commands({TARGET},json_extract(receipt,'$.senderSessionId')) WHERE {PENDING}")


def affected_sources(db, target):
    """Lifecycle publication visits actual pending dependants, never history."""
    return {row[0] for row in db.execute(
        f"SELECT DISTINCT json_extract(receipt,'$.senderSessionId') FROM commands INDEXED BY navigation_waiting_target WHERE {PENDING} AND {TARGET}=?",
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
    return state.get('runtimeControl', {}).get(row['id'], {}).get('task.get', {}).get('task') or row.get('task') or {}


def saved_task(db, sid):
    """Read fixed scalar current facts, not the cold runtime/history body."""
    import json
    row = db.execute('SELECT value FROM history_navigation_tasks WHERE id=?', (sid,)).fetchone()
    if row:
        return json.loads(row[0])
    # Pre-upgrade directory rows may know only blocked/paused, not exact task
    # provenance. Keep that honest generic status without parsing old runtime.
    legacy = db.execute('SELECT base_activity_label FROM history_local_rows WHERE id=?', (sid,)).fetchone()
    return {'status': 'blocked'} if legacy and legacy[0] == 'Work blocked' else (
        {'status': 'paused'} if legacy and legacy[0] == 'Paused' else {})


def save_task(db, sid, task):
    """Derived scalar metadata owned by the existing directory transaction."""
    import json
    keys = ('id', 'status', 'revision', 'continuationEpoch', 'blockProvenance', 'dependencyWaitEpoch')
    value = json.dumps({key: task.get(key) for key in keys}, separators=(',', ':'))
    db.execute('''INSERT INTO history_navigation_tasks VALUES(?,?)
        ON CONFLICT(id) DO UPDATE SET value=excluded.value WHERE value!=excluded.value''', (sid, value))


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
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_pending_waits'").fetchone():
        return None
    boundary = input_boundary(session)
    if boundary is None:
        return None
    sender, identity, interruption, epoch, generation, last_input = KEYS.split(',\n ')
    rows = []
    # SQLite 3.46 loses covering expression reads with an IN seek here. Two
    # exact epoch seeks retain the total row bound without reparsing big bodies.
    for value in epochs:
        rows.extend(db.execute(f"""SELECT {TARGET},{TITLE} FROM commands INDEXED BY navigation_pending_waits
            WHERE {PENDING} AND {sender}=? AND {identity}=? AND {interruption}=?
            AND {epoch}=? AND {generation}=? AND {last_input}=?
            LIMIT ?""", (session['id'], task['id'], session.get('interruptionRevision', 0),
                         value, *boundary, LIMIT + 1 - len(rows))).fetchall())
        if len(rows) > LIMIT:
            break
    targets = {}
    for sid, title in rows[:LIMIT]:
        if isinstance(sid, str) and sid and sid != session['id']:
            targets.setdefault(sid, {'sessionId': sid, 'title': title or 'Conversation'})
    if not targets:
        return None
    return {'targets': list(targets.values()), 'hasMore': len(rows) > LIMIT,
            'basis': 'saved-request-subscription'}


def target_details(db, waiting):
    """One bounded keyed metadata read for the selected chat only."""
    targets = waiting['targets']
    ids = [row['sessionId'] for row in targets]
    rows = db.execute(f"SELECT id,title,status,available FROM history_local_rows WHERE id IN ({','.join('?' for _ in ids)})",
                      ids).fetchall()
    known = {sid: {'title': title[:200], 'status': status, 'available': bool(available)}
             for sid, title, status, available in rows}
    return {**waiting, 'targets': [{**row, **known.get(row['sessionId'], {'available': False})} for row in targets]}
