import copy
import json
import asyncio
import threading

import pytest

from test_work_windows import fixture


def recorded_call(session='s', call='llm:host', request='attempt', *, linked=True):
    common = {'session_id': session, 'provider': 'test', 'model': 'fixture', 'request_id': request}
    if linked:
        common['unified_call_id'] = call
    return [
        {'event': 'llm:request', 'timestamp': 1, 'data': {**common, 'raw': {'input': 'PRIVATE REQUEST'}}},
        {'event': 'llm:response', 'timestamp': 2, 'data': {**common, 'raw': {'output': 'PRIVATE RESPONSE'}}},
    ]


def live_call(session, call='llm:host', sid='s'):
    session.setdefault('execution', {}).update(nodes=[{'id': call, 'sessionId': sid, 'rootSessionId': session['id'],
        'kind': 'llm', 'provider': 'test', 'model': 'fixture', 'turnId': 't',
        'phase': 'completed', 'liveObservation': True, 'startedAt': 1, 'endedAt': 2,
        'usage': {'totalTokens': 2603}}], turns=[{'id': 't', 'anchorMessageId': 'user',
        'phase': 'completed', 'startedAt': 1, 'endedAt': 2}])


@pytest.mark.asyncio
async def test_exact_call_recordings_attach_to_paged_live_work_without_body_or_accounting_changes(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_call(session, sid=session['id'])
    original = copy.deepcopy(session)
    first = owner.live_pages.read(session, 't@user')
    before_revision = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert 'requestDetail' not in first['items'][0]
    path.write_text(''.join(json.dumps(row) + '\n' for row in recorded_call()))
    page = await owner.read('s')
    after_revision = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert after_revision != before_revision, 'The browser must be notified without a new execution event'
    known = {row['id']: row['detailVersion'] for row in first['items']}
    rows = owner.live_pages.sync(session, 't@user', known)['items']
    assert len(rows) == 1, 'An arriving recording must update an already open live call'
    row = rows[0]
    for field in ('request', 'response'):
        ref = row[field + 'Detail']
        assert ref['windowKey'].startswith('open-work:')
        assert ref['id'] != row['id'], 'The exact canonical observation remains the field owner'
        assert field not in row and 'PRIVATE' not in json.dumps(row)
        result = await owner.read_text('s', ref)
        assert 'PRIVATE ' + field.upper() in result['value']
    assert row['usage']['totalTokens'] == 2603 and session == original
    from amplifier_web.work_windows import MAX_PAGES
    for index in range(MAX_PAGES + 1):
        with path.open('a') as stream:
            stream.write(json.dumps({'event': 'tool:post', 'timestamp': 3 + index,
                'data': {'tool_call_id': 'other-' + str(index), 'result': 'unrelated'}}) + '\n')
        await owner.read('s')
        revision = owner.project(session)['execution']['segments'][0]['detailRevision']
        assert revision != after_revision, 'Recent-window activity still reaches the browser'
        known = {row['id']: row['detailVersion']}
        updates = owner.live_pages.sync(session, 't@user', known)['items']
        if updates:
            updated = updates[0]
            assert updated['requestDetail'] == row['requestDetail']
            row = updated
        assert row['requestDetail']['windowKey'].startswith('open-work:')
        assert 'PRIVATE REQUEST' in (await owner.read_text('s', row['requestDetail']))['value']
        after_revision = revision
    path.write_text('{}\n')
    with pytest.raises(ValueError):
        await owner.read_text('s', row['requestDetail'])
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['legacy', 'different-call', 'child', 'retry', 'duplicate'])
async def test_window_does_not_guess_or_mix_call_recordings(tmp_path, monkeypatch, case):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_call(session, sid=session['id'])
    records = recorded_call(linked=case != 'legacy', call='llm:other' if case == 'different-call' else 'llm:host',
                            session='child' if case == 'child' else 's')
    if case == 'retry':
        records += recorded_call(request='retry')
    if case == 'duplicate':
        records += records[:1]
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert 'requestDetail' not in row and 'responseDetail' not in row
    assert row['recordingsGroup'].startswith('event-window:')
    observations = owner.nodes('s', row['recordingsGroup'])['items']
    assert any(value.get('requestDetail') for value in observations)
    assert all('_hostCallId' not in value for value in observations)
    await owner.close()


@pytest.mark.asyncio
async def test_cut_prefix_uses_exact_call_receipt_without_timing_uniqueness(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_call(session)
    # Earlier concurrent/retry records are outside the accepted page. The
    # selected record belongs to another exact call despite identical timing.
    records = recorded_call() + [{'event': 'tool:post', 'timestamp': 1,
        'data': {'tool_call_id': str(i), 'result': 'unrelated'}} for i in range(110)] + recorded_call(call='llm:other')
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    page = await owner.read('s')
    assert page['hasEarlier']
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert 'requestDetail' not in row and 'responseDetail' not in row
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('record_owner', ['event', 'resident', 'resident-reference', 'resident-attribution', 'resident-position'])
async def test_large_recording_download_uses_accepted_event_window_and_never_hydrates_tree(authenticated_client, tmp_path, monkeypatch, record_owner):
    from amplifier_web.server import create_app
    from amplifier_web.provider_payloads import save
    from test_service import Runtime
    from test_work_windows import FakeProjector
    _, source, path, _ = fixture(tmp_path, monkeypatch, count=0)
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
        voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    # This test owns explicit windows without a browser stream subscription;
    # the ordinary interest loop would correctly retire their unobserved lease.
    for reader in (service.event_log_view, service.history):
        if reader.task:
            reader.task.cancel()
            await asyncio.gather(reader.task, return_exceptions=True)
            reader.task = None
    await service.dispatch('session.create', {})
    session = service._session()
    session.update(nativeProject='p', nativeIdentity='s', messages=source['messages'])
    # runtime.py maps only root sessionId/runtime_id to the public owner; the
    # canonical source below deliberately keeps its different native ID 's'.
    live_call(session, sid=session['id'])
    body = {'input': 'LARGE RECORD ' * 1000, 'end': 'FULL-END'}
    saved = save(path.parent, body)
    records = recorded_call()
    records[0]['data'].update(raw={'preview': True}, provider_payloads={'raw': saved},
                             request_capture={'redacted': True, 'truncated': True})
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    windows = service.event_log_view.windows
    windows.projector = FakeProjector(service.data_dir)
    await windows.read(session['id'])
    if record_owner.startswith('resident'):
        # Complete projection may already have enriched this exact host node
        # before bundled web starts using independent bounded windows.
        observed = windows.pages[windows.current[session['id']]]['items'][0]
        node = session['execution']['nodes'][0]
        node.update(canonicalHistory=True, _eventFields=copy.deepcopy(observed['_eventFields']),
            requestDetail={**observed['requestDetail'], 'id': node['id'], 'sessionId': session['id']},
            requestRecording=observed['requestRecording'], requestCapture=observed['requestCapture'])
        windows.pages.clear()
        windows.current.clear()
    service.clients.attach('recording-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'recording-browser'
    original = service._session
    def bounded(identity=None, *, hydrate=True):
        assert hydrate is False, 'A window recording must never hydrate historical execution'
        return original(identity, hydrate=False)
    monkeypatch.setattr(service, '_session', bounded)
    page = await (await client.get('/api/conversation/detail', params={
        'sessionId': session['id'], 'part': 'nodes', 'group': 't@user'})).json()
    node = page['items'][0]
    ref = node['requestDetail']
    assert node['requestRecording'] == saved and node['requestCapture']['truncated']
    args = {**ref, 'digest': saved['sha256']}
    response = await client.get('/api/conversation/provider-recording', params=args)
    assert response.status == 200, await response.text()
    result = await response.json()
    assert result['payload'] == body and result['callId'] == ref['id']
    assert result['rootSessionId'] == session['id']
    assert result['sessionId'] == ('s' if record_owner == 'event' else session['id'])
    assert not result['capture']['truncated']
    assert (await client.get('/api/conversation/provider-recording', params={**args, 'id': 'unowned'})).status == 400
    from amplifier_web import provider_payloads
    opened, release, streams = threading.Event(), threading.Event(), []
    real_open = provider_payloads.open_record
    def delayed_open(*args):
        stream, info = real_open(*args)
        streams.append(stream)
        opened.set()
        assert release.wait(5)
        return stream, info
    monkeypatch.setattr(provider_payloads, 'open_record', delayed_open)
    pending = asyncio.create_task(client.get('/api/conversation/provider-recording', params=args))
    assert await asyncio.to_thread(opened.wait, 5)
    if record_owner == 'resident-reference':
        session['execution']['nodes'][0]['_eventFields']['request']['sha256'] = '0' * 64
    elif record_owner == 'resident-attribution':
        session['execution']['nodes'][0]['sessionId'] = 'unrelated-child'
    elif record_owner == 'resident-position':
        session['execution']['nodes'].insert(0, {'id': 'moved', 'kind': 'tool'})
    else:
        windows.pages.clear()
        windows.live_pages.groups.clear()
    release.set()
    assert (await pending).status == 400
    assert streams[0].closed
    windows.pages.clear()
    windows.live_pages.groups.clear()
    assert (await client.get('/api/conversation/provider-recording', params=args)).status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 3])
async def test_no_recording_shortcut_for_empty_or_tool_only_window(tmp_path, monkeypatch, count):
    owner, session, _, _ = fixture(tmp_path, monkeypatch, count=count)
    live_call(session)
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert 'recordingsGroup' not in row and 'requestDetail' not in row
    await owner.close()


@pytest.mark.asyncio
async def test_exact_partial_attempt_replaces_all_prior_resident_recording_metadata(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_call(session)
    node = session['execution']['nodes'][0]
    node.update(canonicalHistory=True, requestInfo={'message_count': 999},
        _eventFields={'request': {'old': True}, 'response': {'old': True}},
        requestDetail={'part': 'nodes', 'id': node['id'], 'field': 'request', 'digest': 'old'},
        responseDetail={'part': 'nodes', 'id': node['id'], 'field': 'response', 'digest': 'old'},
        requestRecording={'sha256': 'old'}, responseRecording={'sha256': 'old'},
        requestCapture={'truncated': True}, responseCapture={'truncated': True})
    path.write_text(json.dumps(recorded_call(request='new-small-attempt')[0]) + '\n')
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert row['requestDetail']['windowKey'].startswith('open-work:')
    assert all(key not in row for key in ('requestInfo', 'requestRecording', 'responseRecording',
        'requestCapture', 'responseCapture', 'responseDetail'))
    assert node['responseDetail']['digest'] == 'old', 'Only presentation is replaced'
    await owner.close()
