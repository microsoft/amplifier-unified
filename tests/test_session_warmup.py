import asyncio
import copy
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_web.service import AppService
from amplifier_web.execution import ensure_turn
from amplifier_web.session_health import failure_details


class Runtime:
    def __init__(self):
        self.started = []
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.error = None
        self.send_started = asyncio.Event()
        self.emit = None

    async def prewarm(self, session, emit):
        self.emit = emit
        self.started.append(session['id'])
        self.ready.set()
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'starting'})
        await self.release.wait()
        if self.error:
            await emit('runtime.error', {'sessionId': session['id'], 'error': self.error})
            raise RuntimeError(self.error)
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready', 'report': {'bundle': 'test'}})
        await emit('runtime.warmth', {'sessionId': session['id'], 'status': 'warm'})

    async def send(self, session, text, input_id, emit):
        self.send_started.set()
        await self.release.wait()
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working',
                                     'event': 'input.delivered', 'inputId': input_id})
        return {'accepted': True}

    async def close(self): pass


@pytest.fixture
async def service(tmp_path):
    result = AppService(tmp_path/'app', runtime=Runtime(), workspace=tmp_path)
    await result.dispatch('session.create', {'title': 'One'})
    yield result
    result.runtime.release.set()
    await result.close()


async def test_selection_returns_before_preparation_finishes_and_preserves_draft(service):
    sid = service.state['selectedSessionId']
    await service.dispatch('view.update', {'patch': {'draft': 'unsent'}, 'sessionId': sid})
    response = await asyncio.wait_for(service.dispatch('session.select', {'id': sid}), 1)
    await asyncio.wait_for(service.runtime.ready.wait(), 1)
    assert response['accepted'] and not service.runtime.release.is_set()
    assert service.state['view']['draft'] == 'unsent'
    assert service.state['sessions'][0]['status'] == 'idle'
    # Repeated selection shares the same preparation.
    await service.dispatch('session.select', {'id': sid})
    await asyncio.sleep(.02)
    assert service.runtime.started == [sid]
    service.runtime.release.set()
    await asyncio.gather(*list(service.warmup.pending.values()))
    assert service.state['sessions'][0]['preparation']['status'] == 'warm'
    assert service.state['sessions'][0]['messages'] == []


async def test_failed_background_preparation_does_not_create_attention_error(service):
    service.runtime.error = 'Fixture missing provider'
    service.runtime.release.set()
    sid = service.state['selectedSessionId']
    await service.warmup.schedule(sid)
    await asyncio.gather(*service.warmup.pending.values())
    session = service._session(sid)
    assert 'error' not in session
    assert session['preparation']['status'] == 'unavailable'


@pytest.mark.parametrize('raw_error', [True, False])
async def test_passive_preparation_preserves_failed_turn_evidence_and_history(service, raw_error):
    session = service._session()
    service._message(session, 'user', 'Synthetic attachment question', inputId='failed-input')
    ensure_turn(session, 'failed-input')
    await service.on_runtime_event('execution.event', {
        'id': 'failed-call', 'revision': 2, 'turnId': 'failed-input',
        'sessionId': session['id'], 'rootSessionId': session['id'],
        'kind': 'llm', 'phase': 'error', 'endedAt': 1790182800,
        'failure': failure_details('Invalid image base64 data', 'ValueError'),
    })
    if raw_error:
        await service.on_runtime_event('runtime.error', {
            'sessionId': session['id'], 'error': 'Manager turn failed; no automatic replay',
        })
    else:
        session['status'] = 'stopped'
    before = copy.deepcopy(session)
    service.runtime.release.set()
    await service.warmup.run(session['id'])
    assert service.runtime.started == [session['id']]
    assert session['preparation']['status'] == 'warm'
    assert session['runtimeReport'] == {'bundle': 'test'}
    for field in ('status', 'error', 'errorAt', 'failure', 'execution', 'messages'):
        assert session.get(field) == before.get(field), field
    receipt = await service.dispatch('session.inspect', {'id': session['id']}, origin='agent')
    assert receipt['result']['failure']['category'] == 'invalid_image'
    assert receipt['result']['failure']['inputId'] == 'failed-input'
    assert receipt['result']['status'] == before['status']
    # Only a verified new generation retires the old current-turn failure.
    await service.on_runtime_event('runtime.generation', {
        'sessionId': session['id'], 'event': 'generation.started',
        'generation_id': 'new-generation', 'input_ids': ['new-input'],
    })
    assert 'failure' not in session and 'error' not in session
    assert session['execution'] == before['execution']
    assert session['messages'] == before['messages']


@pytest.mark.parametrize('patch_value', [{'ownership': {'status': 'blocked'}}, {'configurationBusy': True},
    {'workspaceAvailable': False}, {'historyReadOnlyReason': 'Historical child'}, {'status': 'working'}])
async def test_background_preparation_respects_owner_and_active_work(service, patch_value):
    session = service.state['sessions'][0]
    session.update(patch_value)
    await service.warmup.schedule(session['id'])
    await asyncio.gather(*service.warmup.pending.values())
    assert not service.runtime.started


async def test_unchanged_history_refresh_skips_loading_and_publication(service):
    session = service.state['sessions'][0]
    session.update(nativeProject='fixture', nativeRevision=[1, 2], historyLoaded=True)
    revision = service.state['revision']
    with patch('amplifier_web.automatic_history.revision', return_value=[1, 2]), patch('amplifier_web.automatic_history.read_transcript') as read:
        await service.history.refresh_session(session['id'])
    read.assert_not_called()
    assert service.state['revision'] == revision


@pytest.mark.parametrize('fork', [False, True])
async def test_preparation_detaches_startup_inputs_without_copying_display_history(service, fork):
    class DisplayOnly(list):
        def __deepcopy__(self, memo):
            raise AssertionError('Preparing a worker traversed retained display history')

    session = service._session()
    previous = dict(session)
    session.update(nativeProject='fixture', nativeIdentity='native-chat',
                   runtimeSessionId='runtime-chat', nativeRevision=[1, 2],
                   historyManaged=True, historyLoaded=True,
                   workingDirectory=session['workspace'], executionRevision=3,
                   selection={'instance': 'synthetic', 'model': 'model', 'effort': 'high'},
                   replaceSavedSelection=True, bundleReplacement={'root': 'synthetic'},
                   execution=DisplayOnly(), workers=DisplayOnly(),
                   messages=[{'role': 'user', 'text': 'Explicit fork context'}] if fork else DisplayOnly())
    if fork:
        session['forkContext'] = {'sessionId': 'parent', 'before': 2}
    runtime = AsyncMock()
    service.runtime.prewarm = runtime
    try:
        with patch.object(service.history.index, 'validate_resume') as validate, \
                patch('amplifier_web.automatic_history.revision', return_value=[1, 2]) as stamp, \
                patch('amplifier_web.automatic_history.read_transcript') as read:
            await service.warmup.run(session['id'])
        validate.assert_called_once_with({'id': session['id'], 'nativeProject': 'fixture',
                                         'nativeIdentity': 'native-chat', 'workspace': session['workspace']})
        stamp.assert_called_once_with({'id': session['id'], 'nativeProject': 'fixture',
                                      'nativeIdentity': 'native-chat', 'runtimeSessionId': 'runtime-chat'})
        read.assert_not_called()
        runtime.assert_awaited_once()
        source = runtime.await_args.args[0]
        assert source['workingDirectory'] == session['workspace'] and source['executionRevision'] == 3
        assert source['nativeIdentity'] == 'native-chat' and source['runtimeSessionId'] == 'runtime-chat'
        assert source['replaceSavedSelection'] is True
        assert source['bundleReplacement'] == {'root': 'synthetic'}
        assert 'execution' not in source and 'workers' not in source
        assert source['selection'] == session['selection'] and source['selection'] is not session['selection']
        source['selection']['effort'] = 'low'
        assert session['selection']['effort'] == 'high'
        if fork:
            assert source['messages'] == session['messages'] and source['messages'] is not session['messages']
            source['messages'][0]['text'] = 'Detached'
            source['forkContext']['before'] = 1
            assert session['messages'][0]['text'] == 'Explicit fork context'
            assert session['forkContext']['before'] == 2
        else:
            assert 'messages' not in source
    finally:
        session.clear()
        session.update(previous)


async def test_small_resume_snapshot_still_requires_fresh_canonical_admission(service):
    session = service._session()
    session.update(nativeProject='fixture', nativeIdentity='native-chat',
                   historyManaged=True, historyLoaded=True)
    with patch.object(service.history.index, 'validate_resume',
                      side_effect=ValueError('Native workspace metadata changed')) as validate, \
            patch('amplifier_web.automatic_history.revision') as stamp:
        with pytest.raises(ValueError, match='Native workspace metadata changed'):
            await service.history.ensure_loaded(session['id'])
    validate.assert_called_once()
    stamp.assert_not_called()
    assert not service.runtime.started


async def test_exception_before_worker_events_settles_only_preparation(service):
    from amplifier_foundation.exceptions import BundleNotFoundError
    from unittest.mock import AsyncMock
    session = service._session()
    before = copy.deepcopy(session)
    service.runtime.prewarm = AsyncMock(side_effect=BundleNotFoundError('private detail'))
    await service.warmup.run(session['id'])
    assert session['preparation']['status'] == 'unavailable'
    assert 'bundle ID' in session['preparation']['detail']
    assert 'private detail' not in session['preparation']['detail']
    for field in ('status', 'error', 'failure', 'messages'):
        assert session.get(field) == before.get(field)


async def test_send_waiting_on_warmup_shows_progress_without_replaying_or_old_tools(service):
    session = service._session()
    session['activity'] = {'phase': 'idle', 'lastEvent': {'tool': 'yesterday'}, 'activeTools': ['old']}
    await service.warmup.schedule(session['id'])
    await asyncio.wait_for(service.runtime.ready.wait(), 1)
    send = asyncio.create_task(service.dispatch('conversation.send',
        {'sessionId': session['id'], 'text': 'One input'}, command_id='pending-input'))
    try:
        await asyncio.wait_for(service.runtime.send_started.wait(), 1)
        assert session['activity']['phase'] == 'runtime-setup'
        assert session['activity']['lastEvent'] is None
        assert session['activity']['activeTools'] == []
        assert session['messages'][-1]['delivery']['status'] == 'sending'
        await service.runtime.emit('runtime.status', {'sessionId': session['id'], 'status': 'starting',
            'detail': 'Verifying this chat’s setup…', 'elapsedSeconds': 90})
        assert session['status'] == 'starting'
        assert session['activity']['label'] == 'Verifying this chat’s setup…'
        assert session['progress']['elapsedSeconds'] == 90
        assert service.runtime.started == [session['id']]
        assert len(session['messages']) == 1
    finally:
        service.runtime.release.set()
        await asyncio.wait_for(send, 2)
        await asyncio.gather(*list(service.warmup.pending.values()))
    assert session['messages'][0]['delivery']['status'] == 'accepted'
    assert len(session['messages']) == 1


async def test_passive_warmup_progress_does_not_revive_a_stopped_send(service):
    session = service._session()
    await service.warmup.schedule(session['id'])
    await asyncio.wait_for(service.runtime.ready.wait(), 1)
    session['messages'].append({'role': 'user', 'text': 'Saved', 'delivery': {'status': 'sending'}})
    session['status'] = 'stopped'
    before = copy.deepcopy(session.get('activity'))
    await service.runtime.emit('runtime.status', {'sessionId': session['id'], 'status': 'starting',
        'detail': 'Checking this chat’s tools…'})
    assert session['status'] == 'stopped'
    assert session.get('activity') == before
