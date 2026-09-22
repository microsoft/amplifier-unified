"""Destination imports retain exact evidence and refresh existing projections."""
import base64
import copy
import json
import sqlite3
from threading import RLock
from types import SimpleNamespace

import pytest

from amplifier_web import portability_data as data
from amplifier_web.outputs import Outputs
from amplifier_web.questions import Questions
from amplifier_web.resource_files import put, root
from amplifier_web.service import AppService
from amplifier_web.state_storage import resource
from amplifier_worktrees.git import digest


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def app(tmp_path):
    db = sqlite3.connect(tmp_path / 'state.db')
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    operations = sqlite3.connect(':memory:')
    operations.execute('CREATE TABLE operation_requests (session_id TEXT, id TEXT, signature TEXT, value TEXT, PRIMARY KEY(session_id,id))')
    app = SimpleNamespace(db=db, state={'sessions': [{'id': 'portable-task'}], 'canvasArtifacts': []},
        operations=SimpleNamespace(journal=SimpleNamespace(db=operations, lock=RLock())),
        state_resource=lambda identity: resource(db, identity))
    app.outputs = Outputs(app)
    app.questions = Questions(app)
    yield app
    operations.close()
    db.close()


@pytest.fixture
def payload():
    first = {'encoding': 'utf-8', 'data': 'An earlier imported resource'}
    last = {'encoding': 'utf-8', 'data': 'Exact saved evidence'}
    resources = {digest(encoded(value)): value for value in (first, last)}
    transcript = b'{"role":"user","content":"Saved task history"}\n'
    return {'session': {'id': 'portable-task', 'bundle': 'work', 'messages': []}, 'resources': resources,
        'nativeIdentity': 'portable-native',
        'native': {'transcript.jsonl': {'data': base64.b64encode(transcript).decode(),
                                      'bytes': len(transcript), 'sha256': digest(transcript)}},
        'intent': {'bundle': 'work', 'controls': {}},
        'outputs': [{'id': 'portable-output', 'sessionId': 'portable-task', 'createdAt': 1,
                     'body': {'$resource': digest(encoded(last))}, 'sha256': digest(last['data'].encode())}],
        'comments': [{'id': 'portable-comment', 'outputId': 'portable-output', 'body': 'Saved review'}],
        'outputReceipts': [{'id': 'portable-command', 'fingerprint': 'original-command',
                            'value': {'id': 'portable-output', 'sessionId': 'portable-task'}}],
        'canvas': [], 'observations': {'operationRequests': [], 'questions': [], 'liveJobs': []}}


def snapshot(app):
    directory = root(app.db)
    return {'tables': {table: app.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                      for table in ('state_resources', 'output_records', 'output_comments', 'output_receipts', 'questions')},
            'files': {path.name: path.read_bytes() for path in directory.glob('*.json')},
            'state': copy.deepcopy(app.state)}


@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('corruption', [b'{broken json', encoded({'encoding': 'utf-8', 'data': 'Changed evidence'})])
def test_conflicting_resource_file_rejects_all_writes_and_allows_deliberate_recovery(app, payload, indexed, corruption):
    key, value = list(payload['resources'].items())[-1]
    if indexed:
        put(app.db, value)
    directory = root(app.db)
    directory.mkdir(exist_ok=True)
    path = directory / (key + '.json')
    path.write_bytes(corruption)
    before = snapshot(app)

    for operation in (data.preflight_install, data.install_outputs):
        with pytest.raises(ValueError, match='resource'):
            operation(app, payload)
        app.db.commit()  # A later unrelated commit cannot publish partial imports.
        assert snapshot(app) == before

    # Recovery is explicit and retains the original; import never repairs over it.
    archived = path.with_suffix('.conflict')
    path.rename(archived)
    path.write_bytes(encoded(value))
    data.install_outputs(app, payload)
    app.db.commit()
    assert archived.read_bytes() == corruption
    assert app.outputs.content(app.outputs.store.read('portable-output')) == b'Exact saved evidence'
    complete = snapshot(app)
    data.install_outputs(app, payload)
    app.db.commit()
    assert snapshot(app) == complete


@pytest.mark.parametrize('index', [
    {'encoding': 'utf-8', 'data': 'Conflicting inline evidence'},
    {'$blob': '0' * 64},
    {'$blob': '0' * 64, 'unexpected': True},
    '{broken json',
])
def test_conflicting_resource_index_rejects_before_writing_even_with_valid_file(app, payload, index):
    key, value = list(payload['resources'].items())[-1]
    put(app.db, value)
    text = index if isinstance(index, str) else json.dumps(index)
    app.db.execute('UPDATE state_resources SET value=? WHERE id=?', (text, key))
    before = snapshot(app)
    with pytest.raises(ValueError, match='resource'):
        data.install_outputs(app, payload)
    app.db.commit()
    assert snapshot(app) == before


@pytest.mark.parametrize('inline', [False, True])
def test_valid_existing_resource_or_missing_file_can_be_imported(app, payload, inline):
    key, value = list(payload['resources'].items())[-1]
    put(app.db, value)
    path = root(app.db) / (key + '.json')
    path.unlink()
    if inline:
        app.db.execute('UPDATE state_resources SET value=? WHERE id=?', (json.dumps(value), key))
    data.preflight_install(app, payload)
    assert not path.exists()  # Preflight is read-only, even for recoverable absence.
    data.install_outputs(app, payload)
    assert path.read_bytes() == encoded(value)
    assert app.outputs.content(app.outputs.store.read('portable-output')) == b'Exact saved evidence'


def test_resource_symlink_is_rejected_without_following_or_replacing(app, payload, tmp_path):
    key, value = list(payload['resources'].items())[-1]
    directory = root(app.db)
    directory.mkdir()
    original = tmp_path / 'original.json'
    original.write_bytes(encoded(value))
    path = directory / (key + '.json')
    path.symlink_to(original)
    with pytest.raises(ValueError):
        data.install_outputs(app, payload)
    assert path.is_symlink() and original.read_bytes() == encoded(value)
    assert not app.db.execute('SELECT * FROM state_resources').fetchall()


@pytest.mark.parametrize('receipt', [
    {'id': 'foreign-output', 'sessionId': 'other-task'},
    {'id': 'foreign-comment', 'outputId': 'other-output'},
    {'id': 'unscoped-result'},
    {'sessionId': 'portable-task', 'outputId': 'other-output'},
    {'sessionId': 'other-task', 'outputId': 'portable-output'},
    {'id': 'missing-output', 'sessionId': 'portable-task'},
    {'sessionId': 'portable-task'},
    {'id': 'missing-comment', 'outputId': 'portable-output'},
    {'outputId': 'portable-output'},
    {'id': 'portable-output', 'outputId': 'portable-output'},
    None,
])
def test_unrelated_or_contradictory_portable_output_receipts_are_rejected(app, payload, receipt):
    payload['outputReceipts'][0]['value'] = receipt
    before = snapshot(app)
    for operation in (lambda app, value: data.validate(value), data.preflight_install, data.install_outputs):
        with pytest.raises(ValueError, match='receipt'):
            operation(app, payload)
        app.db.commit()
        assert snapshot(app) == before


@pytest.mark.parametrize('receipt', [
    {'id': 'foreign-output', 'sessionId': 'other-task'},
    {'id': 'foreign-comment', 'outputId': 'other-output'},
    {'id': 'unscoped-result'},
    {'id': 'different-output', 'sessionId': 'portable-task'},
    '{broken json',
])
def test_matching_fingerprint_cannot_overwrite_a_conflicting_existing_receipt(app, payload, receipt):
    incoming = payload['outputReceipts'][0]
    text = receipt if isinstance(receipt, str) else json.dumps(receipt)
    app.db.execute('INSERT INTO output_receipts VALUES (?,?,?)', (incoming['id'], incoming['fingerprint'], text))
    before = snapshot(app)
    with pytest.raises(ValueError, match='receipt'):
        data.install_outputs(app, payload)
    app.db.commit()
    assert snapshot(app) == before


@pytest.mark.parametrize('comment', [False, True])
def test_exact_output_and_comment_receipt_duplicates_remain_idempotent(app, payload, comment):
    incoming = payload['outputReceipts'][0]
    if comment:
        incoming['value'] = {'id': 'portable-comment', 'outputId': 'portable-output', 'body': 'Saved review'}
    data.validate(payload)
    data.install_outputs(app, payload)
    app.db.commit()
    before = snapshot(app)
    data.install_outputs(app, payload)
    app.db.commit()
    assert snapshot(app) == before


@pytest.mark.parametrize('conflicting', [False, True])
def test_duplicate_receipt_identities_inside_one_package_reject_before_writes(app, payload, conflicting):
    duplicate = copy.deepcopy(payload['outputReceipts'][0])
    if conflicting:
        duplicate['value']['title'] = 'A different saved result'
    payload['outputReceipts'].append(duplicate)
    before = snapshot(app)
    for operation in (lambda app, value: data.validate(value), data.preflight_install, data.install_outputs):
        with pytest.raises(ValueError, match='Duplicate output receipt'):
            operation(app, payload)
        app.db.commit()
        assert snapshot(app) == before


def test_comment_receipt_cannot_claim_a_comment_bound_to_another_output(app, payload):
    second = {**payload['outputs'][0], 'id': 'second-output'}
    payload['outputs'].append(second)
    payload['outputReceipts'][0]['value'] = {'id': 'portable-comment', 'outputId': 'second-output'}
    before = snapshot(app)
    for operation in (lambda app, value: data.validate(value), data.preflight_install, data.install_outputs):
        with pytest.raises(ValueError, match='receipt'):
            operation(app, payload)
        app.db.commit()
        assert snapshot(app) == before


class IdleRuntime:
    async def send(self, *args, **kwargs):
        pytest.fail('Importing saved questions must not send task input')

    async def close(self):
        pass


@pytest.mark.parametrize('existing_question', [False, True])
async def test_import_refreshes_cached_session_and_browser_question_projection(tmp_path, payload, existing_question):
    app = AppService(tmp_path / 'app', IdleRuntime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Existing destination task'})
        sid = app._session()['id']
        question = {'id': 'portable-question', 'sessionId': sid, 'createdAt': 1, 'updatedAt': 2,
                    'revision': 2, 'status': 'answered', 'prompt': 'Keep the original answer?',
                    'options': [], 'allowFreeText': True, 'required': True, 'dependency': 'Saved task work',
                    'answer': {'text': 'Preserved answer'}, 'delivery': {'status': 'accepted'}}
        if existing_question:
            app.questions.store.put({**question, 'revision': 1, 'status': 'pending', 'answer': None, 'delivery': None})
        app._save()
        cached = copy.deepcopy(app._session()['questions'])
        app.browser_state()  # Cache the pre-import browser projection as well.
        payload['session']['id'] = sid
        payload['observations']['questions'] = [question]

        data.install_receipts(app, payload)
        app._publish()  # The same publication boundary used by activation.
        assert app.questions.store.get(sid, question['id']) == question
        assert app._session()['questions'] == [question] != cached
        visible = next(row for row in app.browser_state()['sessions'] if row['id'] == sid)
        assert visible['questions'] == [question]
    finally:
        await app.close()
