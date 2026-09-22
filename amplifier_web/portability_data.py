"""Explicit task data projection; executable configuration and secrets stay local."""
import base64
import copy
import json
from pathlib import Path
import stat

from amplifier_portability.capsule import validate_bytes, validate_public, _regular_bytes
from amplifier_worktrees.git import digest
from .host.storage import SessionStore
from .session_files import validate_id

MAX_BYTES = 32 * 1024 * 1024
NATIVE_FILES = {'transcript.jsonl', 'transcript.jsonl.backup', 'metadata.json', 'metadata.json.backup',
                'events.jsonl', 'context-intelligence/metadata.json', 'context-intelligence/events.jsonl'}
CONTROL_FIELDS = {'goal', 'task', 'taskReceipts', 'taskHistory', 'budget', 'capacity',
                  'capacityReceipts', 'capacityLastDenial', 'selection', 'mode', 'configurator'}
SESSION_FIELDS = {'id', 'title', 'titleSource', 'autoName', 'description', 'createdAt', 'updatedAt',
                  'bundle', 'messages', 'task', 'artifactRefs', 'draft', 'draftAttachments',
                  'selection', 'parentId', 'forkTranscript', 'execution', 'sharedHistoryOffset',
                  'sharedHistoryUserTurnOffset', 'sharedHistoryTotal', 'executionRevision'}


def file_data(path):
    before = path.stat()
    data, _ = _regular_bytes(path, MAX_BYTES)
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('Task history changed during export')
    validate_bytes(data)
    return {'data': base64.b64encode(data).decode(), 'sha256': digest(data), 'bytes': len(data)}


def decode_file(value):
    data = base64.b64decode(value['data'], validate=True)
    if len(data) != value['bytes'] or len(data) > MAX_BYTES or digest(data) != value['sha256']:
        raise ValueError('Task history integrity check failed')
    validate_bytes(data)
    # Native files are JSON/JSONL. Scan decoded structures too: base64 is a
    # transport encoding, never a way around credential-field rejection.
    def unique(pairs):
        result = {}
        for key, child in pairs:
            if key in result:
                raise ValueError('Duplicate key in saved task history')
            result[key] = child
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except json.JSONDecodeError:
        for line in data.splitlines():
            if line.strip(): validate_public(json.loads(line, object_pairs_hook=unique))
    else:
        validate_public(value)
    return data


def native_directory(app, session):
    identity = validate_id(session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])
    return SessionStore.for_app(app.data_dir, session['workspace']).directory(identity)


def capture(app, session):
    identity = validate_id(session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])
    native = native_directory(app, session)
    files = {}
    for name in sorted(NATIVE_FILES):
        path = native / name
        if path.exists():
            files[name] = file_data(path)
    if 'transcript.jsonl' not in files:
        raise ValueError('A canonical saved transcript is required before transferring a task')
    directory = app.data_dir / 'sessions' / identity
    controls_path = directory / 'control-state.json'
    controls = json.loads(decode_file(file_data(controls_path))) if controls_path.is_file() else {}
    controls = {key: value for key, value in controls.items() if key in CONTROL_FIELDS}
    selection = session.get('selection') or controls.get('selection')
    if not selection or not selection.get('model') or not (selection.get('instance') or selection.get('provider')):
        raise ValueError('Select an explicit provider and model before exporting configuration intent')
    intent = {'bundle': session['bundle'], 'selection': copy.deepcopy(selection), 'controls': controls}
    # Never transport configuration.json/effective-configuration.json, keys.env,
    # local source overrides, MCP/OAuth accounts, native-provider process state,
    # approval tokens, live jobs, scheduler admissions or module caches.
    observations = {'liveJobs': [], 'operations': [], 'questions': app.questions.store.all(session['id']),
                    'workers': copy.deepcopy(session.get('workers', [])), 'approvals': copy.deepcopy(session.get('approvals', []))}
    for path in sorted((native / 'live-jobs').glob('job-*.json')):
        observations['liveJobs'].append({'name': path.name, 'file': file_data(path)})
    observations['schedules'] = [json.loads(row[0]) for row in app.schedules.store.db.execute('SELECT value FROM schedules WHERE session_id=?', (session['id'],))]
    observations['scheduleRuns'] = [json.loads(row[0]) for row in app.schedules.store.db.execute('SELECT value FROM schedule_runs WHERE session_id=?', (session['id'],))]
    with app.operations.journal.lock:
        db = app.operations.journal.db
        observations['operationRequests'] = [{'id': identity, 'signature': signature, 'record': json.loads(value)}
            for identity, signature, value in db.execute('SELECT id,signature,value FROM operation_requests WHERE session_id=?', (session['id'],))]
        for (value,) in db.execute('SELECT value FROM operations WHERE session_id=?', (session['id'],)):
            row = json.loads(value)
            observations['operations'].append({'record': row,
                'events': [json.loads(x[0]) for x in db.execute('SELECT value FROM operation_events WHERE operation_id=? ORDER BY sequence', (row['id'],))],
                'output': [json.loads(x[0]) for x in db.execute('SELECT value FROM operation_output WHERE operation_id=? ORDER BY cursor', (row['id'],))]})
    previous_transfer = session.get('portabilityEvidence', {}).get('transferId')
    if previous_transfer:
        from amplifier_portability.capsule import read_capsule
        from amplifier_portability.protocol import encoded
        prior = app.portability.node.get(previous_transfer)
        body = read_capsule(Path(prior['destinationState']['package']))['body']
        if digest(encoded(body)) != prior['capsuleHash']:
            raise ValueError('Prior transfer evidence changed; restore it before moving this task again')
        previous = body['payload']['observations']
        def identity_of(row):
            return row.get('id') or row.get('name') or row.get('record', {}).get('id') or digest(row)
        for category, records in previous.items():
            current = {identity_of(row): row for row in observations.get(category, [])}
            merged = {identity_of(row): row for row in records}
            merged.update(current)
            observations[category] = list(merged.values())
    outputs = [json.loads(row[0]) for row in app.db.execute('SELECT value FROM output_records WHERE session_id=? ORDER BY created', (session['id'],))]
    output_ids = {row['id'] for row in outputs}
    output_receipts = []
    for receipt_id, fingerprint, receipt_value in app.db.execute('SELECT id,fingerprint,value FROM output_receipts'):
        saved = json.loads(receipt_value)
        if saved.get('sessionId') == session['id'] or saved.get('outputId') in output_ids:
            output_receipts.append({'id': receipt_id, 'fingerprint': fingerprint, 'value': saved})
    comments = [comment for output in outputs for comment in app.outputs.store.comments(output['id'])]
    canvas = [copy.deepcopy(row) for row in app.state.get('canvasArtifacts', []) if row.get('sessionId') == session['id']]
    value = {'session': {key: copy.deepcopy(value) for key, value in session.items() if key in SESSION_FIELDS},
             'nativeIdentity': identity, 'native': files, 'intent': intent, 'outputs': outputs, 'comments': comments, 'outputReceipts': output_receipts,
             'canvas': canvas, 'observations': observations,
             'origin': {'historyHome': session['workspace'], 'executionDirectory': session.get('workingDirectory') or session['workspace']}}
    checkpoint = directory / 'context-checkpoint.json'
    if checkpoint.exists(): value['contextCheckpoint'] = file_data(checkpoint)
    from .resource_files import references
    pending = list(references(value))
    resources = {}
    while pending:
        key = pending.pop()
        if key in resources:
            continue
        resource = app.state_resource(key)
        resources[key] = copy.deepcopy(resource)
        pending.extend(references(resource))
    value['resources'] = resources
    validate(value)
    return value


def validate(value):
    try:
        _validate(value)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('Malformed task transfer data') from exc


def _validate(value):
    validate_public(value)
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError('Task history and output evidence exceed the 32 MiB transfer limit')
    session = value['session']
    validate_id(session['id']); validate_id(value['nativeIdentity'])
    if type(session.get('executionRevision', 0)) is not int or session.get('executionRevision', 0) < 0:
        raise ValueError('Invalid task execution revision')
    if set(session) - SESSION_FIELDS or set(value['intent']['controls']) - CONTROL_FIELDS:
        raise ValueError('Unsupported executable state in task transfer')
    if session.get('bundle') != value['intent']['bundle']:
        raise ValueError('Task bundle differs from declared configuration intent')
    configurator = value['intent']['controls'].get('configurator', {})
    if not isinstance(configurator, dict) or set(configurator) - {'disabled'}:
        raise ValueError('Only disabled module intent may be transferred from the configurator')
    disabled = configurator.get('disabled', {})
    if not isinstance(disabled, dict) or set(disabled) - {'tools', 'providers', 'hooks', 'agents', 'context', 'behaviors'} or any(not isinstance(items, list) or any(not isinstance(item, str) for item in items) for items in disabled.values()):
        raise ValueError('Invalid disabled module intent')
    if not isinstance(session.get('messages'), list) or not isinstance(value['native'], dict):
        raise ValueError('Invalid task history')
    if set(value['native']) - NATIVE_FILES or 'transcript.jsonl' not in value['native']:
        raise ValueError('Unsupported native history file')
    for name, row in value['native'].items():
        raw = decode_file(row)
        if name in {'metadata.json', 'metadata.json.backup'}:
            metadata = json.loads(raw)
            if not isinstance(metadata, dict):
                raise ValueError('Native metadata must be an object')
            if metadata.get('session_id', value['nativeIdentity']) != value['nativeIdentity']:
                raise ValueError('Native metadata identity differs from the transferred task')
            for key in ('bundle', 'bundle_name'):
                if key in metadata and (not isinstance(metadata[key], str) or metadata[key].removeprefix('bundle:') != value['intent']['bundle']):
                    raise ValueError('Native metadata bundle differs from declared configuration intent')
    if value.get('contextCheckpoint'): decode_file(value['contextCheckpoint'])
    for row in value['observations']['liveJobs']: decode_file(row['file'])
    for key, resource in value['resources'].items():
        raw = json.dumps(resource, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if digest(raw) != key:
            raise ValueError('Saved output resource integrity check failed')
        if resource.get('encoding') == 'base64':
            validate_bytes(base64.b64decode(resource['data'], validate=True))
    output_ids = {row['id'] for row in value['outputs']}
    if len(output_ids) != len(value['outputs']):
        raise ValueError('Duplicate output identities')
    for row in value['outputs']:
        if row['sessionId'] != session['id'] or (row.get('parentId') and row['parentId'] not in output_ids) or any(key not in output_ids for key in row.get('evidenceIds', [])):
            raise ValueError('Output lineage must remain in the transferred task')
        if row.get('body'):
            resource = value['resources'][row['body']['$resource']]
            content = base64.b64decode(resource['data'], validate=True) if resource['encoding'] == 'base64' else resource['data'].encode()
            if digest(content) != row.get('sha256'):
                raise ValueError('Output content differs from its saved evidence hash')
    if any(row['outputId'] not in output_ids for row in value['comments']):
        raise ValueError('Output comment has no transferred lineage')
    if len({row['id'] for row in value['canvas']}) != len(value['canvas']) or any(row['sessionId'] != session['id'] for row in value['canvas']):
        raise ValueError('Canvas identities must belong to the transferred task')
    for request in value['observations']['operationRequests']:
        if request['record']['sessionId'] != session['id'] or request['record']['requestId'] != request['id']:
            raise ValueError('Operation receipt belongs to another task')
    from .resource_files import references
    if any(key not in value['resources'] for key in references(value)):
        raise ValueError('A referenced artifact is unavailable; restore it before transfer')


def preflight_install(app, value):
    """Reject destination identity conflicts before publishing any imported data."""
    sid = value['session']['id']
    for row in value['outputs']:
        existing = app.db.execute('SELECT session_id FROM output_records WHERE id=?', (row['id'],)).fetchone()
        if existing and existing[0] != sid: raise ValueError('An output identity belongs to another task')
    for row in value['comments']:
        existing = app.db.execute('SELECT output_id FROM output_comments WHERE id=?', (row['id'],)).fetchone()
        if existing and existing[0] != row['outputId']: raise ValueError('An output comment identity belongs to another output')
    for row in value['canvas']:
        if any(current['id'] == row['id'] and current.get('sessionId') != sid for current in app.state.get('canvasArtifacts', [])):
            raise ValueError('A canvas identity belongs to another task')
    for row in value['outputReceipts']:
        existing = app.db.execute('SELECT fingerprint FROM output_receipts WHERE id=?', (row['id'],)).fetchone()
        if existing and existing[0] != row['fingerprint']:
            raise ValueError('An output command identity already has different contents')
    for question in value['observations']['questions']:
        if question['sessionId'] != sid:
            raise ValueError('Question belongs to another task')
        existing = app.db.execute('SELECT session_id FROM questions WHERE id=?', (question['id'],)).fetchone()
        if existing and existing[0] != sid:
            raise ValueError('Question identity belongs to another task')
    with app.operations.journal.lock:
        for saved in value['observations']['operationRequests']:
            existing = app.operations.journal.db.execute('SELECT signature FROM operation_requests WHERE session_id=? AND id=?', (sid, saved['id'])).fetchone()
            if existing and existing[0] != saved['signature']:
                raise ValueError('A saved operation request has different contents on this host')


def install_outputs(app, value):
    from .resource_files import put
    preflight_install(app, value)
    for key, resource in value['resources'].items():
        if put(app.db, resource)['$resource'] != key:
            raise ValueError('Output resource identity changed during import')
    for row in value['outputs']:
        app.db.execute('INSERT OR REPLACE INTO output_records VALUES (?,?,?,?)',
                       (row['id'], row['sessionId'], row['createdAt'], json.dumps(row)))
    for row in value['comments']:
        app.db.execute('INSERT OR REPLACE INTO output_comments VALUES (?,?,?)', (row['id'], row['outputId'], json.dumps(row)))
    for row in value['outputReceipts']:
        app.db.execute('INSERT OR REPLACE INTO output_receipts VALUES (?,?,?)', (row['id'], row['fingerprint'], json.dumps(row['value'])))


def install_receipts(app, value):
    """Retain admission idempotence; old receipts never own destination work."""
    sid = value['session']['id']
    with app.operations.journal.lock, app.operations.journal.db:
        db = app.operations.journal.db
        for saved in value['observations']['operationRequests']:
            record = copy.deepcopy(saved['record'])
            if record['state'] == 'admitting':
                record.update(state='outcome_unknown', message='Transfer preserved this unconfirmed request without replay.')
            existing = db.execute('SELECT signature FROM operation_requests WHERE session_id=? AND id=?', (sid, saved['id'])).fetchone()
            if existing and existing[0] != saved['signature']:
                raise ValueError('A saved operation request has different contents on this host')
            db.execute('INSERT OR REPLACE INTO operation_requests VALUES (?,?,?,?)', (sid, saved['id'], saved['signature'], json.dumps(record)))
    for question in value['observations']['questions']:
        if question['sessionId'] != sid:
            raise ValueError('Question belongs to another task')
        existing = app.db.execute('SELECT session_id FROM questions WHERE id=?', (question['id'],)).fetchone()
        if existing and existing[0] != sid:
            raise ValueError('Question identity belongs to another task')
        app.db.execute('INSERT OR REPLACE INTO questions VALUES (?,?,?,?)', (question['id'], sid, question['createdAt'], json.dumps(question)))
