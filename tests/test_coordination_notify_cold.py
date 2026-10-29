"""Passive peer append resolves needed fields without waking a dormant owner."""
import asyncio
import copy
import json
import sqlite3

import pytest

from amplifier_web import cold_display
from amplifier_web.automatic_history import revision
from amplifier_web.service import AppError
from amplifier_web.session_files import project_slug
from amplifier_web.session_projection import view_path
from test_automatic_history import native_session
from test_collaborative_workspaces import app, send


def committed(app, identity):
    with sqlite3.connect(app.db.execute('PRAGMA database_list').fetchone()[2]) as db:
        row = db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


async def dormant(app, tmp_path, monkeypatch, *, native=False):
    source, target = app.state['sessions']
    await app.history.close()
    directory = None
    if native:
        directory = native_session(tmp_path, 'native-recipient')
        target.update(nativeIdentity='native-recipient', runtimeSessionId='native-recipient',
                      nativeProject=project_slug(tmp_path), historyManaged=False, historyLoaded=False)
        await app.history.ensure_loaded(target['id'])
    # Own the synthetic graph independently of temporary native-read results.
    target['messages'] = copy.deepcopy(target['messages']) + [
        {'id': f'web-{i}', 'role': 'assistant', 'text': 'retained '+str(i)+'x'*1000,
         'createdAt': i, 'via': 'chat'} for i in range(40)]
    target.update(draft='Keep recipient draft', configuration={'plan': {'notes': 'c'*20000}})
    target['execution'] = {'nodes': [{'id': 'web-tool', 'kind': 'tool', 'phase': 'completed',
                                     'output': 'exact saved output'+'e'*20000}], 'turns': []}
    app.state.setdefault('runtimeControl', {})[target['id']] = {
        'modelCatalogs': {'models': [{'id': 'saved-model', 'description': 'm'*20000}]},
        'task.get': {'task': {'id': 'saved-task', 'revision': 3, 'status': 'blocked'}}}
    app._publish()
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    assert target['id'] in app.cold_display.retire(force=True)
    refs = copy.deepcopy(dict.get(target, '_coldFields'))
    controls = copy.deepcopy(dict.get(app.state['runtimeControl'][target['id']], '_coldFields'))
    assert {'messages', 'execution', 'configuration'} <= refs.keys()
    assert 'modelCatalogs' in controls
    return source, target, refs, controls, directory


def record_loads(monkeypatch):
    calls = []
    original = cold_display.load
    def load(db, ref):
        calls.append(ref['$resource'])
        return original(db, ref)
    monkeypatch.setattr(cold_display, 'load', load)
    return calls


@pytest.mark.parametrize('native', [False, True])
async def test_notify_keeps_unrelated_cold_fields_and_commits_once(app, tmp_path, monkeypatch, native):
    source, target, refs, controls, directory = await dormant(app, tmp_path, monkeypatch, native=native)
    before = cold_display.materialize(target, app.db)['messages']
    native_before = (directory/'transcript.jsonl').read_bytes() if directory else None
    calls = record_loads(monkeypatch)
    result = await send(app, mode='notify')
    assert result['accepted'] and result['delivery'] == 'notified'
    assert calls == [refs['messages']['$resource']]
    assert dict.get(target, '_coldFields') == {k:v for k,v in refs.items() if k != 'messages'}
    assert dict.get(app.state['runtimeControl'][target['id']], '_coldFields') == controls
    assert target['messages'][:-1] == before
    assert target['messages'][-1]['inputId'] == 'request-1'
    assert target['messages'][-1]['peerEnvelope']['senderSessionId'] == source['id']
    assert committed(app, 'request-1')['messageId'] == target['messages'][-1]['id']
    saved = json.loads(view_path(app.data_dir, target).read_text())
    assert saved['_coldFields']['execution'] == refs['execution']
    assert cold_display.materialize(saved, app.db)['messages'] == target['messages']
    calls.clear()
    assert (await send(app, mode='notify'))['duplicate']
    assert calls == [] and len(target['messages']) == len(before)+1
    assert target['status'] == 'idle' and not app.runtime.inputs
    assert target['draft'] == 'Keep recipient draft'
    assert app.state['selectedSessionId'] == source['id']
    assert app.state['view']['draft'] == 'Private unsent draft'
    if directory:
        assert (directory/'transcript.jsonl').read_bytes() == native_before


async def test_changed_native_history_reconciles_before_notify(app, tmp_path, monkeypatch):
    _, target, refs, controls, directory = await dormant(app, tmp_path, monkeypatch, native=True)
    old_revision = target['nativeRevision']
    with (directory/'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'New canonical answer'})+'\n')
    canonical = (directory/'transcript.jsonl').read_bytes()
    await send(app, mode='notify')
    assert target['nativeRevision'] == revision(target) != old_revision
    texts = [row['text'] for row in target['messages']]
    assert texts.index('New canonical answer') < len(texts)-1
    assert texts[-1] == 'Check the interface dependency'
    assert all(f'web-{i}' in {m['id'] for m in target['messages']} for i in range(40))
    assert target['execution']['nodes'][0]['output'] == 'exact saved output'+'e'*20000
    assert dict.get(target, '_coldFields')['configuration'] == refs['configuration']
    assert dict.get(app.state['runtimeControl'][target['id']], '_coldFields') == controls
    assert (directory/'transcript.jsonl').read_bytes() == canonical
    assert target['draft'] == 'Keep recipient draft' and not app.runtime.inputs


@pytest.mark.parametrize('refusal', ['historyReadOnlyReason', 'workspaceAvailable', 'historyError'])
async def test_notify_retains_native_refusals_without_loading_unrelated_fields(app, tmp_path, monkeypatch, refusal):
    _, target, refs, controls, _ = await dormant(app, tmp_path, monkeypatch, native=True)
    target[refusal] = False if refusal == 'workspaceAvailable' else 'Synthetic unavailable history'
    calls = record_loads(monkeypatch)
    with pytest.raises(AppError):
        await send(app, mode='notify')
    assert calls == [] and committed(app, 'request-1') is None
    assert dict.get(target, '_coldFields') == refs and not app.runtime.inputs


async def test_notify_retains_managed_native_identity_validation(app, tmp_path, monkeypatch):
    _, target, _, _, directory = await dormant(app, tmp_path, monkeypatch, native=True)
    target['historyManaged'] = True
    metadata = json.loads((directory/'metadata.json').read_text())
    metadata['working_dir'] = str(tmp_path/'different-workspace')
    (directory/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(AppError, match='workspace metadata changed'):
        await send(app, mode='notify')
    assert committed(app, 'request-1') is None and not app.runtime.inputs


async def test_notify_rechecks_recipient_and_sender_after_history_await(app, tmp_path, monkeypatch):
    source, target, _, _, _ = await dormant(app, tmp_path, monkeypatch, native=True)
    original = app.history.ensure_loaded
    async def replaced(sid, **kwargs):
        await original(sid, **kwargs)
        target['sessionKind'] = 'internal'
    monkeypatch.setattr(app.history, 'ensure_loaded', replaced)
    with pytest.raises(AppError, match='root'):
        await send(app, mode='notify')
    assert committed(app, 'request-1') is None and not app.runtime.inputs
    target['sessionKind'] = 'root'
    async def changed(sid, **kwargs):
        await original(sid, **kwargs)
        source['collaborationGeneration']['id'] = 'replacement-generation'
    monkeypatch.setattr(app.history, 'ensure_loaded', changed)
    with pytest.raises(AppError, match='generation'):
        await send(app, mode='notify')
    assert committed(app, 'request-1') is None and not app.runtime.inputs


async def test_notify_lost_publication_ack_retries_receipt_without_loading_or_waking(app, tmp_path, monkeypatch):
    _, target, refs, _, _ = await dormant(app, tmp_path, monkeypatch)
    publish = app.collaboration.publish_receipt
    def no_schedule(*args, **kwargs):
        raise AssertionError('A passive committed notification must not schedule recipient work')
    monkeypatch.setattr(app.collaboration, 'expire_later', no_schedule)
    monkeypatch.setattr(app.collaboration, 'drain', no_schedule)
    def lost(receipt):
        publish(receipt)
        raise OSError('Synthetic lost acknowledgement')
    with monkeypatch.context() as scoped:
        scoped.setattr(app.collaboration, 'publish_receipt', lost)
        with pytest.raises(OSError, match='lost acknowledgement'):
            await send(app, mode='notify')
    assert committed(app, 'request-1')['delivery'] == 'notified'
    calls = record_loads(monkeypatch)
    assert (await send(app, mode='notify'))['duplicate']
    assert calls == [] and sum(m.get('inputId') == 'request-1' for m in target['messages']) == 1
    assert dict.get(target, '_coldFields')['execution'] == refs['execution'] and not app.runtime.inputs


@pytest.mark.parametrize('fault', ['message', 'insert', 'storage', 'cancelled_storage'])
async def test_notify_failed_save_does_not_duplicate_on_retry(app, tmp_path, monkeypatch, fault):
    source, target, refs, _, _ = await dormant(app, tmp_path, monkeypatch)
    target['navigationPostAdmissions'] = {'posts': [{'id': 'existing-admission'}], 'progress': False}
    target['navigationActivityPending'] = False
    fields = ('historyManaged', 'coordinationReference', 'recentActivityAt',
              'navigationActivityAt', 'navigationActivityPending', 'navigationPostAdmissions')
    before = {row['id']: {key:copy.deepcopy(row[key]) for key in fields if key in row}
              for row in (source, target)}
    app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)',
                   ('prior-operation', 'prior', json.dumps({'accepted': True})))
    owner, name = {'message': (app, '_message'), 'insert': (app.collaboration, 'insert'),
                   'storage': (app.directory, 'sync'), 'cancelled_storage': (app.directory, 'sync')}[fault]
    original = getattr(owner, name)
    error = asyncio.CancelledError if fault == 'cancelled_storage' else OSError
    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise error('Synthetic notification save failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(owner, name, failed)
        with pytest.raises(error, match='notification save'):
            await send(app, mode='notify')
    assert committed(app, 'request-1') is None
    assert committed(app, 'prior-operation') == {'accepted': True}
    assert not any(m.get('inputId') == 'request-1' for m in target['messages'])
    assert {row['id']: {key:copy.deepcopy(row[key]) for key in fields if key in row}
            for row in (source, target)} == before
    assert dict.get(target, '_coldFields')['execution'] == refs['execution']
    app._publish_changes(sessions={source['id'], target['id']})
    assert (await send(app, mode='notify'))['delivery'] == 'notified'
    assert sum(m.get('inputId') == 'request-1' for m in target['messages']) == 1 and not app.runtime.inputs


async def test_notify_requires_exact_message_resource_and_retry_is_single(app, tmp_path, monkeypatch):
    _, target, refs, _, _ = await dormant(app, tmp_path, monkeypatch)
    key = refs['messages']['$resource']
    saved = app.db.execute('SELECT value FROM state_resources WHERE id=?', (key,)).fetchone()[0]
    app.db.execute('DELETE FROM state_resources WHERE id=?', (key,))
    app.db.commit()
    try:
        with pytest.raises(AppError, match='State resource is unavailable'):
            await send(app, mode='notify')
        assert committed(app, 'request-1') is None
        assert dict.get(target, '_coldFields')['execution'] == refs['execution']
    finally:
        app.db.execute('INSERT INTO state_resources(id,value) VALUES(?,?)', (key, saved))
        app.db.commit()
    assert (await send(app, mode='notify'))['delivery'] == 'notified'
    assert sum(m.get('inputId') == 'request-1' for m in target['messages']) == 1 and not app.runtime.inputs


async def test_metadata_receipt_and_cancellation_projection_do_not_read_bodies(app, tmp_path, monkeypatch):
    _, target, refs, controls, _ = await dormant(app, tmp_path, monkeypatch)
    target['collaborationGeneration'] = {'id': 'active', 'inputIds': ['incoming-request']}
    receipt = {'commandAction': 'coordination.send', 'requestId': 'incoming-request',
               'senderSessionId': app.state['sessions'][0]['id'], 'target': {'sessionId': target['id']},
               'delivery': 'accepted', 'cancellation': {'status': 'requested'}}
    app.collaboration.insert('incoming-request', 'synthetic-fingerprint', receipt)
    calls = record_loads(monkeypatch)
    app.collaboration.refresh_reference(receipt)
    assert app.collaboration.current(target['id'])['requests'][0]['requestId'] == 'incoming-request'
    assert target['coordinationReference']['cancellations']['items'][0]['requestId'] == 'incoming-request'
    target['collaborationGeneration'] = {'id': 'replacement', 'inputIds': []}
    app.collaboration.refresh_reference(receipt)
    assert target['coordinationReference']['cancellations']['items'][0]['requestId'] == 'incoming-request'
    assert calls == [] and dict.get(target, '_coldFields') == refs
    assert dict.get(app.state['runtimeControl'][target['id']], '_coldFields') == controls
