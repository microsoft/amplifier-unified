"""Latest app-state records, committed together; not a replay log.

Full saves reconcile every record, while scoped saves visit only declared keys.
Both leave unchanged values untouched. The base row contains only session order;
load() is the authority for reads, retention and deletion, including older full
snapshots with overlays. Canonical events and command receipts are separate.
"""
import json


UPSERT = '''INSERT INTO state_records VALUES(?,?,?)
    ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value
    WHERE state_records.value != excluded.value'''


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS state_records (
        kind TEXT NOT NULL CHECK(kind IN ('session','runtime','revision','global')),
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
        elif kind == 'global':
            if identity in {'sessions', 'runtimeControl', 'revision'} or not isinstance(value, dict) or set(value) != {'present', 'value'} or type(value['present']) is not bool:
                raise ValueError('Invalid saved global record; original records were preserved.')
            if value['present']:
                state[identity] = value['value']
            else:
                state.pop(identity, None)
    state['sessions'] = list(sessions.values())
    return state


def checkpoint(db, state):
    """Reconcile a complete snapshot without rewriting unchanged records.

    The caller commits this alongside view pointers and private client state. No
    in-memory acknowledgement/cache can get ahead of a failed SQLite transaction.
    Replacing a legacy full base and all of its overlays is likewise atomic. Older
    code that reads only the base row cannot read this layout; use load().
    """
    base = {'revision': 0, 'sessions': [{'id': row['id']} for row in state.get('sessions', [])],
            'runtimeControl': {}}
    db.execute('''INSERT INTO state VALUES(1,?)
        ON CONFLICT(id) DO UPDATE SET value=excluded.value
        WHERE state.value != excluded.value''', (json.dumps(base),))
    remaining = set(db.execute('SELECT kind,id FROM state_records'))

    def records():
        yield 'revision', '', state['revision']
        for row in state.get('sessions', []):
            yield 'session', row['id'], row
        for identity, value in state.get('runtimeControl', {}).items():
            yield 'runtime', identity, value
        for key, value in state.items():
            if key not in {'sessions', 'runtimeControl', 'revision'}:
                yield 'global', key, {'present': True, 'value': value}

    def encoded():
        for kind, identity, value in records():
            remaining.discard((kind, identity))
            yield kind, identity, json.dumps(value)

    db.executemany(UPSERT, encoded())
    db.executemany('DELETE FROM state_records WHERE kind=? AND id=?', remaining)


def save(db, state, references, session_ids, global_keys=()):
    """Replace only the latest changed references and runtime records.

    The owning service commits these with its revision and dirty private client
    records. Full saves reconcile unknown scopes; all readers use load(), even
    after a clean shutdown. Unchanged runtime/configuration records stay put.
    """
    from .cold_display import saved
    values = [('revision', '', state['revision'])]
    controls = state.get('runtimeControl', {})
    for identity in session_ids:
        values.append(('session', identity, references.get(identity)))
        values.append(('runtime', identity, saved(controls[identity]) if identity in controls else None))
    for key in global_keys:
        if key in {'sessions', 'runtimeControl', 'revision'}:
            raise ValueError('Global records cannot replace scoped session authority.')
        values.append(('global', key, {'present': key in state, 'value': state.get(key)}))
    db.executemany(UPSERT,
                   ((kind, identity, json.dumps(value)) for kind, identity, value in values))
