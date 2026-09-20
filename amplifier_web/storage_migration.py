"""One-time, rollback-preserving storage upgrade and bounded receipt retention."""
import json
import sqlite3
import time

from .resource_files import collect, put, remove_files, root
from .state_storage import resource

RESULT_LIMIT = 200
RESULT_BYTES = 32_000_000
RESULT_DAYS = 30


def trim_operations(db, state):
    """Keep small at-most-once receipts forever, recent full results within budget."""
    retained = size = 0
    expired = set()
    # Stream rows; an old installation can have gigabytes of full results.
    cursor = db.execute("SELECT id FROM smart_tool_operations WHERE json_extract(value,'$.resultExpired') IS NOT 1 ORDER BY json_extract(value,'$.updatedAt') DESC")
    for (identity,) in cursor:
        text = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (identity,)).fetchone()[0]
        operation = json.loads(text)
        if operation.get('status') in {'running', 'queued'}:
            continue
        fields = {key: operation[key] for key in ('result', 'arguments') if key in operation}
        if not fields:
            continue
        cost = sum(value.get('bytes', 0) if isinstance(value, dict) and '$resource' in value
                   else len(json.dumps(value).encode()) for value in fields.values())
        if (retained < RESULT_LIMIT and size + cost <= RESULT_BYTES
                and operation.get('updatedAt', time.time()) >= time.time() - RESULT_DAYS * 86400):
            retained += 1
            size += cost
            # Older receipts embedded full results. Externalize only what survives.
            for key, value in fields.items():
                if not (isinstance(value, dict) and '$resource' in value) and len(json.dumps(value)) > 16_000:
                    operation[key] = put(db, value)
        else:
            for key in fields:
                operation.pop(key, None)
            operation['resultExpired'] = True
            operation['detail'] = 'Full result retention expired. The execution receipt remains; work is never replayed.'
            expired.add(identity)
        encoded = json.dumps(operation)
        if encoded != text:
            db.execute('UPDATE smart_tool_operations SET value=? WHERE id=?', (encoded, identity))
    smart = state.get('smartTools', {})
    for item in [*smart.get('operations', []), smart.get('inspectedOperation', {})]:
        row = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (item.get('id'),)).fetchone()
        if row:
            item.clear()
            item.update(json.loads(row[0]))


def upgrade(service):
    """Runs before accepting connections; keep the complete old DB for rollback."""
    db, state = service.db, service.state
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_layout'").fetchone():
        return
    existing = db.execute('SELECT 1 FROM state WHERE id=1').fetchone()
    if existing:
        folder = service.data_dir / 'backups' / 'shared-storage-v1'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = folder / 'app.sqlite3'
        if not backup.exists():
            temporary = folder / 'app.sqlite3.pending'
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.chmod(0o600)
            temporary.replace(backup)
    from .session_projection import migrate
    migrate(service.data_dir, state)
    for item in state.get('canvasArtifacts', []):
        reference = item.get('body', {}).get('$resource')
        if not reference:
            continue
        body = resource(db, reference)
        if 'mcp' in body:
            item['mcpState'] = put(db, body['mcp'])
            item['body'] = put(db, {key: value for key, value in body.items() if key != 'mcp'})
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='smart_tool_operations'").fetchone():
        trim_operations(db, state)
    from .state_storage import normalize_state
    normalize_state(state, db)
    stale = collect(db, state)
    # Preserve existing IDs referenced by history, even when their original
    # serialization used different whitespace/key order.
    directory = root(db)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .host.storage import SessionStore
    for identity, text in db.execute('SELECT id,value FROM state_resources'):
        value = json.loads(text)
        if isinstance(value, dict) and set(value) == {'$blob'}:
            continue
        SessionStore._atomic(directory / (identity + '.json'), text)
        db.execute('UPDATE state_resources SET value=? WHERE id=?', (json.dumps({'$blob': identity}), identity))
    from .session_projection import persist
    saved = persist(service.data_dir, state, service._view_cache)
    db.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (json.dumps(saved),))
    db.execute('CREATE TABLE storage_layout(version INTEGER NOT NULL)')
    db.execute('INSERT INTO storage_layout VALUES(1)')
    db.commit()
    remove_files(db, stale)
    # Startup migration is offline. Do not shrink multi-GB databases while the
    # server is accepting requests; ordinary retention reuses freed pages.
    if existing:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def maintenance(service):
    """Occasional reachability sweep; called after committing the state snapshot."""
    now = time.monotonic()
    if getattr(service, 'backup_in_progress', False):
        return
    if now - getattr(service, '_last_storage_sweep', now - 61) < 60:
        return
    service._last_storage_sweep = now
    # A bound client is only a projection: it hides shared presentation fields
    # and all other clients. Retention must see every retained in-memory root.
    clients = getattr(service, 'clients', None)
    roots = [service._state, *clients.records.values()] if clients else service._state
    stale = collect(service.db, roots)
    service.db.commit()
    remove_files(service.db, stale)
