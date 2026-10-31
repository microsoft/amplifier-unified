"""Exact, passive Stop receipts: no replay, hydration or completion claim."""
import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.conversation_stop import stop_status
from amplifier_web.server import create_app
from amplifier_web.service import AppError, AppService, command_fingerprint
from test_service import Runtime


URL = '/api/conversation/stop-status'
CLIENT = 'stop-browser'


class WaitingStopRuntime(Runtime):
    """Keep real Stop admission distinct from eventual runtime completion."""
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Queue()
        self.release = asyncio.Event()

    async def stop(self, sid, *, reason='stopped'):
        self.stopped.append(sid)
        self.entered.put_nowait(sid)
        await self.release.wait()


async def prepare(service):
    await service.dispatch('session.create', {}, include_state=False)
    service.clients.attach(CLIENT)
    service.clients.attach('other-browser')
    service._save_changes()


@pytest.fixture
async def service(tmp_path):
    app = AppService(tmp_path / 'app', WaitingStopRuntime(), workspace=tmp_path)
    await prepare(app)
    try:
        yield app
    finally:
        app.runtime.release.set()
        await app.close()


@pytest.fixture
async def http(tmp_path, authenticated_client):
    app = await create_app(tmp_path / 'app', workspace=tmp_path,
                           runtime=WaitingStopRuntime(), voice=False,
                           background_updates=False, preload_providers=False,
                           background_work=False)
    service = app['service']
    await service.history.close()  # Keep unrelated discovery out of receipt/state assertions.
    await service.event_log_view.close()
    await prepare(service)
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = CLIENT
    try:
        yield service, client
    finally:
        service.runtime.release.set()


def lookup(service, command='stop-once', client=CLIENT):
    return {'sessionId': service._state['selectedSessionId'],
            'commandId': command, 'clientId': client}


async def read(service, args, client=CLIENT):
    with service.clients.bind(client):
        async with service.lock:
            return stop_status(service, args)


async def stop(service, args):
    with service.clients.bind(args['clientId']):
        receipt = await service.dispatch('conversation.stop',
            {'sessionId': args['sessionId']}, command_id=args['commandId'],
            include_state=False)
    assert receipt['accepted'] is True
    assert await asyncio.wait_for(service.runtime.entered.get(), 3) == args['sessionId']
    return receipt


async def test_exact_old_stop_receipt_survives_newer_stop_without_replay(service, monkeypatch):
    first = lookup(service)
    assert await read(service, first) == {**first, 'status': 'unknown'}
    await stop(service, first)
    newer = lookup(service, 'newer-stop')
    await stop(service, newer)
    session = service._session(first['sessionId'])
    assert session['lastInterruption']['commandId'] == newer['commandId']
    assert session['interruptionRevision'] == 2
    assert session['status'] == 'stopping' and not service.runtime.release.is_set()

    before = copy.deepcopy(service._state)
    commands = service.db.execute('SELECT * FROM commands ORDER BY id').fetchall()
    writes = service.db.total_changes
    # These guards run after real admission and runtime Stop entry. A passive
    # lookup must neither dispatch again nor start/load/stop anything else.
    with monkeypatch.context() as guard:
        guard.setattr(service, 'dispatch', AsyncMock(side_effect=AssertionError('No replay')))
        guard.setattr(service, '_stop', AsyncMock(side_effect=AssertionError('No Stop')))
        guard.setattr(service, '_task', Mock(side_effect=AssertionError('No work admission')))
        guard.setattr(service.cold_display, 'hydrate', Mock(side_effect=AssertionError('No hydration')))
        guard.setattr(service.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('No history load')))
        for args in (first, newer, first):
            assert await read(service, args) == {**args, 'status': 'accepted'}
        service.dispatch.assert_not_called()
        service._stop.assert_not_called()
        service.history.ensure_loaded.assert_not_called()
    assert service._state == before
    assert service.db.total_changes == writes
    assert service.db.execute('SELECT * FROM commands ORDER BY id').fetchall() == commands
    assert service.runtime.stopped == [first['sessionId'], first['sessionId']]
    assert service.runtime.started == service.runtime.sent == []


@pytest.mark.parametrize('mismatch', ['action', 'session', 'client', 'origin', 'hash'])
async def test_receipt_fingerprint_must_match_every_stop_identity(service, mismatch):
    args = lookup(service)
    await stop(service, args)
    action, payload, origin, client = 'conversation.stop', {'sessionId': args['sessionId']}, 'ui', CLIENT
    if mismatch == 'action':
        action = 'conversation.send'
    elif mismatch == 'session':
        payload = {'sessionId': 'another-session'}
    elif mismatch == 'client':
        client = 'other-browser'
    elif mismatch == 'origin':
        origin = 'agent'
    fingerprint = command_fingerprint(action, payload, origin, client) if mismatch != 'hash' else 'wrong-hash'
    service.db.execute('UPDATE commands SET fingerprint=? WHERE id=?', (fingerprint, args['commandId']))
    service.db.commit()
    before = copy.deepcopy(service._state)
    with pytest.raises(AppError, match='does not belong') as error:
        await read(service, args)
    assert error.value.status == 409
    assert service._state == before
    assert service.runtime.stopped == [args['sessionId']]


@pytest.mark.parametrize('receipt', [{}, {'accepted': False}, {'accepted': None},
                                    {'accepted': 1}, {'accepted': 'true'}, []])
async def test_unaccepted_receipts_stay_unknown(service, receipt):
    args = lookup(service)
    fingerprint = command_fingerprint('conversation.stop', {'sessionId': args['sessionId']}, 'ui', CLIENT)
    service.db.execute('INSERT INTO commands VALUES (?,?,?)',
                       (args['commandId'], fingerprint, json.dumps(receipt)))
    service.db.commit()
    before = copy.deepcopy(service._state)
    assert await read(service, args) == {**args, 'status': 'unknown'}
    assert service._state == before
    assert service.runtime.stopped == service.runtime.started == service.runtime.sent == []


@pytest.mark.parametrize('args', [None, [], 'stop', {},
    {'sessionId': 's', 'commandId': 'c'},
    {'sessionId': 's', 'commandId': 'c', 'clientId': CLIENT, 'action': 'conversation.stop'}])
def test_malformed_envelope_is_rejected_before_service_access(args):
    with pytest.raises(AppError) as error:
        stop_status(SimpleNamespace(), args)
    assert error.value.status == 400


@pytest.mark.parametrize('field,limit', [('sessionId', 200), ('commandId', 200), ('clientId', 100)])
@pytest.mark.parametrize('invalid', ['', None, 1, False, [], 'too-long'])
def test_identifiers_are_nonempty_bounded_strings(field, limit, invalid):
    args = {'sessionId': 's', 'commandId': 'c', 'clientId': CLIENT}
    args[field] = 'x' * (limit + 1) if invalid == 'too-long' else invalid
    with pytest.raises(AppError) as error:
        stop_status(SimpleNamespace(), args)
    assert error.value.status == 400


async def test_maximum_identifier_lengths_and_metadata_only_lookup(service, monkeypatch):
    args = {'sessionId': 's' * 200, 'commandId': 'c' * 200, 'clientId': 'b' * 100}
    service.clients.attach(args['clientId'])
    session = Mock(return_value={'id': args['sessionId']})
    with monkeypatch.context() as guard:
        guard.setattr(service, '_session', session)
        assert await read(service, args, client=args['clientId']) == {**args, 'status': 'unknown'}
    session.assert_called_once_with(args['sessionId'], hydrate=False)
    assert service.runtime.stopped == service.runtime.started == service.runtime.sent == []


async def test_lookup_requires_an_existing_session(service):
    args = {**lookup(service), 'sessionId': 'not-a-session'}
    with pytest.raises(AppError) as error:
        await read(service, args)
    assert error.value.status == 404
    assert service.runtime.stopped == service.runtime.started == service.runtime.sent == []


async def test_http_exact_lookup_uses_lock_and_echoes_only_identity_and_status(http, monkeypatch):
    from amplifier_web import conversation_stop
    service, client = http
    args = lookup(service)
    missing = await client.post(URL, json=args)
    assert missing.status == 200
    assert await missing.json() == {**args, 'status': 'unknown'}
    response = await client.post('/api/actions', json={'action': 'conversation.stop',
        'args': {'sessionId': args['sessionId']}, 'id': args['commandId']})
    assert response.status == 200 and (await response.json())['accepted'] is True
    assert await asyncio.wait_for(service.runtime.entered.get(), 3) == args['sessionId']
    original = conversation_stop.stop_status

    def locked_read(app, payload):
        assert app.lock.locked(), 'Receipt reads must share the admission lock'
        return original(app, payload)

    monkeypatch.setattr(conversation_stop, 'stop_status', locked_read)
    before = copy.deepcopy(service._state)
    for _ in range(2):
        response = await client.post(URL, json=args)
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert await response.json() == {**args, 'status': 'accepted'}
    assert service._state == before
    assert service._session(args['sessionId'])['status'] == 'stopping'
    assert service.runtime.stopped == [args['sessionId']]
    assert service.runtime.started == service.runtime.sent == []


async def test_http_rejects_unattached_or_different_client_and_malformed_bodies(http):
    service, client = http
    args = lookup(service)
    await stop(service, args)
    for headers, body, status in [
        ({'X-Amplifier-Client': ''}, args, 400),
        ({'X-Amplifier-Client': 'unattached'}, args, 409),
        ({'X-Amplifier-Client': 'other-browser'}, args, 409),
        ({}, {**args, 'clientId': 'other-browser'}, 409),
        ({'X-Amplifier-Client': 'other-browser'}, {**args, 'clientId': 'other-browser'}, 409),
        ({}, {**args, 'origin': 'ui'}, 400),
        ({}, {**args, 'commandId': ''}, 400),
    ]:
        response = await client.post(URL, headers=headers, json=body)
        assert response.status == status
        assert (await response.json()).get('status') != 'accepted'
    malformed = await client.post(URL, data='{broken', headers={'Content-Type': 'application/json'})
    assert malformed.status == 400
    assert service.runtime.stopped == [args['sessionId']]


async def test_http_authentication_and_cross_site_boundaries_are_unchanged(http):
    service, client = http
    args = lookup(service)
    for headers, status in [
        ({'Authorization': ''}, 401),
        ({'Origin': 'https://untrusted.example'}, 403),
        ({'Origin': 'null'}, 403),
        ({'Sec-Fetch-Site': 'cross-site'}, 403),
    ]:
        response = await client.post(URL, headers=headers, json=args)
        assert response.status == status
        assert (await response.json()).get('status') != 'accepted'
    assert (await client.get(URL)).status == 405
    assert service.runtime.stopped == service.runtime.started == service.runtime.sent == []


async def test_http_failed_commit_cannot_report_acceptance_or_replay_stop(http, monkeypatch):
    service, client = http
    args = lookup(service)
    original_save = service._save
    commit = service._commit_pending_progress
    observer = sqlite3.connect(service.data_dir / 'app.sqlite3')

    def fail_save():
        raise OSError('Synthetic storage failure')

    def locked_commit():
        assert service.lock.locked(), 'Commit retry and receipt read must share the lock'
        return commit()

    monkeypatch.setattr(service, '_save', fail_save)
    monkeypatch.setattr(service, '_commit_pending_progress', locked_commit)
    try:
        failed = await client.post('/api/actions', json={'action': 'conversation.stop',
            'args': {'sessionId': args['sessionId']}, 'id': args['commandId']})
        assert failed.status == 500
        assert service._progress_dirty
        assert service.db.execute('SELECT receipt FROM commands WHERE id=?', (args['commandId'],)).fetchone()
        assert observer.execute('SELECT receipt FROM commands WHERE id=?', (args['commandId'],)).fetchone() is None
        failed_read = await client.post(URL, json=args)
        assert failed_read.status == 500
        assert service._progress_dirty
        assert observer.execute('SELECT receipt FROM commands WHERE id=?', (args['commandId'],)).fetchone() is None
        assert service.runtime.stopped == []

        monkeypatch.setattr(service, '_save', original_save)
        recovered = await client.post(URL, json=args)
        assert recovered.status == 200
        assert await recovered.json() == {**args, 'status': 'accepted'}
        assert not service._progress_dirty
        saved = observer.execute('SELECT receipt FROM commands WHERE id=?', (args['commandId'],)).fetchone()
        assert json.loads(saved[0])['accepted'] is True
        # Recovery committed the original admission only; it did not schedule
        # the Stop effect skipped when the original save failed.
        assert service.runtime.stopped == service.runtime.started == service.runtime.sent == []
        assert service._session(args['sessionId'])['status'] == 'stopping'
    finally:
        monkeypatch.setattr(service, '_save', original_save)
        monkeypatch.setattr(service, '_commit_pending_progress', commit)
        observer.close()