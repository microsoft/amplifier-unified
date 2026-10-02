"""Latest scoped records over a compatible full checkpoint, not a replay log.

Only an admitted detail-only writer uses this path. Unknown/global mutations
checkpoint the complete state and fold these records in the same transaction.
Reads, retention and deletion share this exact committed-state reader. Canonical
transcripts/events and command receipts remain in their existing stores.
"""
import json


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS state_records (
        kind TEXT NOT NULL CHECK(kind IN ('session','runtime','revision')),
        id TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,id))''')


def load(db):
    row = db.execute('SELECT value FROM state WHERE id=1').fetchone()
    if row is None:
        return None
    state = json.loads(row[0])
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='state_records'").fetchone():
        return state
    sessions = {row['id']: row for row in state.get('sessions', [])}
    controls = state.setdefault('runtimeControl', {})
    for kind, identity, text in db.execute('SELECT kind,id,value FROM state_records'):
        value = json.loads(text)
        if kind == 'revision':
            if identity != '' or type(value) is not int or value < state['revision']:
                raise ValueError('Invalid saved state revision; original records were preserved.')
            state['revision'] = value
        elif kind == 'session':
            if value is not None and (not isinstance(value, dict) or value.get('id') != identity):
                raise ValueError('Invalid saved session record; original records were preserved.')
            if value is None:
                sessions.pop(identity, None)
            else:
                sessions[identity] = value
        elif kind == 'runtime':
            if value is not None and not isinstance(value, dict):
                raise ValueError('Invalid saved runtime record; original records were preserved.')
            if value is None:
                controls.pop(identity, None)
            else:
                controls[identity] = value
    state['sessions'] = list(sessions.values())
    return state


def checkpoint(db, state):
    """Existing complete storage shape, including downgrade/backup readers."""
    db.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (json.dumps(state),))
    db.execute('DELETE FROM state_records')


def save(db, state, references, session_ids):
    """Replace only the latest changed references and runtime records.

    The owning service commits these with its revision and dirty private client
    records. A full checkpoint on close/unknown scope folds them for old readers;
    an unclean restart must use load(), not the older checkpoint alone.
    """
    from .cold_display import saved
    values = [('revision', '', state['revision'])]
    controls = state.get('runtimeControl', {})
    for identity in session_ids:
        values.append(('session', identity, references.get(identity)))
        values.append(('runtime', identity, saved(controls[identity]) if identity in controls else None))
    db.executemany('INSERT OR REPLACE INTO state_records VALUES(?,?,?)',
                   ((kind, identity, json.dumps(value)) for kind, identity, value in values))
