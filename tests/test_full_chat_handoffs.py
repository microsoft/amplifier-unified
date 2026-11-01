"""Headless Full creation uses truthful lineage and canonical exactly-once effects."""
import asyncio
import copy
import json

import pytest

from amplifier_web.access_mode import read_mode
from amplifier_web.new_chat_handoffs import NewChatHandoffs
from amplifier_web.schedule_handoff import latest_human
from amplifier_web.service import AppError
from test_access_mode import save
from test_new_chat_handoffs import handoff, start, finish, private_view, propose


async def headless(handoff):
    app, runtime, source, peer, args = handoff
    await save(app, app._session(source))
    app.queues.clear(); app.queue_clients.clear(); app.queue_sessions.clear()
    app.clients.records.clear(); app.clients.dirty.clear()
    send = runtime.send
    async def guarded_send(session, text, input_id, emit, *, admission_check=None):
        if admission_check:
            admission_check()
        return await send(session, text, input_id, emit)
    runtime.send = guarded_send
    args = {key: value for key, value in args.items() if key != 'clientId'}
    return app, runtime, source, peer, args


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_headless_truthful_input_and_exact_retry(handoff, origin):
    app, runtime, source, peer, args = await headless(handoff)
    if origin == 'agent':
        row = await start(app, source, args)
    else:
        row = (await app.dispatch('session.handoff.start', args, origin='system', caller_session_id=source))['result']
    result = await finish(app, row)
    assert result['status'] == 'accepted', result
    assert result['mode'] == 'full'
    target = app._session(result['targetSessionId'])
    assert not target.get('collaboration') and not target.get('assignmentId')
    assert not target.get('parentSessionId')
    assert target['handoffOrigin'] == app.handoffs.lineage(result)
    assert app.access_mode.snapshot(target)['mode'] == 'full'
    assert app.access_mode.snapshot(target)['source'] == 'inherited'
    assert read_mode(app.data_dir, target['workspace'], target['id']) == 'full'
    message = next(m for m in target['messages'] if m.get('inputId') == result['inputId'])
    assert message['inputOrigin'] == origin
    assert message['inputActor']['origin'] == origin
    assert message['inputActor']['sessionId'] == source
    assert not message.get('navigationPost') and latest_human(target) is None
    assert not target['approvals'] and not target['questions']
    assert not app.clients.records
    assert len(runtime.sent) == 1
    authority = copy.deepcopy(result['authority'])
    app._session(source).setdefault('collaborationGeneration', {})['terminal'] = True
    await save(app, app._session(source), mode='ask')
    assert app.access_mode.snapshot(target)['mode'] == 'ask'
    duplicate = (await app.dispatch('session.handoff.start', args, origin='system', caller_session_id=source))['result']
    assert duplicate['status'] == 'accepted'
    assert app.handoffs.load(row['requestId'])['authority'] == authority
    assert len(runtime.sent) == 1 and len(app.state['sessions']) == 3
    with pytest.raises(AppError, match='different handoff'):
        await app.dispatch('session.handoff.start', {**args, 'text': 'Different'}, origin='system', caller_session_id=source)
    with pytest.raises(AppError, match='calling conversation'):
        await app.dispatch('session.handoff.read', {'requestId': row['requestId']}, origin='system', caller_session_id=peer)


async def test_full_preserves_client_drafts_and_cannot_upgrade_review(handoff):
    app, runtime, source, _, args = handoff
    reviewed = await propose(app, source, args)
    before = {client: private_view(app, client) for client in ('owner', 'other')}
    await save(app, app._session(source))
    with pytest.raises(AppError, match='different handoff'):
        await start(app, source, args)
    send = runtime.send
    async def guarded_send(session, text, input_id, emit, *, admission_check=None):
        admission_check()
        return await send(session, text, input_id, emit)
    runtime.send = guarded_send
    row = await start(app, source, {**args, 'requestId': 'autonomous'})
    assert (await finish(app, row))['status'] == 'accepted'
    assert app.handoffs.load(reviewed['requestId'])['status'] == 'pending'
    for client in before:
        assert private_view(app, client) == before[client]


@pytest.mark.parametrize('phase', ['session.create', 'conversation.send'])
@pytest.mark.parametrize('change', ['stop', 'revoke', 'off-on', 'owner', 'complete', 'placement'])
async def test_full_delayed_effect_guard(handoff, monkeypatch, phase, change):
    app, runtime, source, _, args = await headless(handoff)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.handoffs.perform
    async def delayed(row, action):
        if action == phase:
            entered.set()
            await release.wait()
        return await original(row, action)
    monkeypatch.setattr(app.handoffs, 'perform', delayed)
    row = await start(app, source, args)
    await asyncio.wait_for(entered.wait(), 5)
    source_row = app._session(source)
    if change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source})
    elif change in {'revoke', 'off-on'}:
        await save(app, source_row, mode='ask')
        if change == 'off-on':
            await save(app, source_row)
    elif change == 'owner':
        source_row['runtimeSessionId'] = 'replacement-root'
    elif change == 'placement':
        source_row.update(workingDirectory=source_row['workspace'] + '/another-checkout', executionRevision=1)
    else:
        source_row['collaborationGeneration']['terminal'] = True
    release.set()
    result = await finish(app, row)
    if change in {'complete', 'placement'}:
        assert result['status'] == 'accepted', result
        assert len(runtime.sent) == 1
    else:
        assert result['status'] == 'failed', result
        assert not runtime.sent
        assert len(app.state['sessions']) == (2 if phase == 'session.create' else 3)


async def test_full_target_stop_still_prevents_initial_delivery(handoff, monkeypatch):
    app, runtime, source, _, args = await headless(handoff)
    original = app.handoffs.perform
    async def stop_target(row, action):
        if action == 'conversation.send':
            await app.dispatch('conversation.stop', {'sessionId': row['targetSessionId']})
        return await original(row, action)
    monkeypatch.setattr(app.handoffs, 'perform', stop_target)
    result = await finish(app, await start(app, source, args))
    assert result['status'] == 'failed' and result['creation'] == 'created'
    assert not runtime.sent


async def test_full_native_stale_transport_cannot_restart_after_stop(handoff):
    app, runtime, source, _, args = await headless(handoff)
    app._session(source)['collaborationGeneration'] = {'id': 'human-turn', 'inputIds': ['user-request'], 'terminal': False}
    await app.dispatch('conversation.stop', {'sessionId': source})
    with pytest.raises(AppError):
        await start(app, source, args)
    assert not runtime.sent
    # A fresh system command is new authorized work, not stopped worker replay.
    row = (await app.dispatch('session.handoff.start', args, origin='system', caller_session_id=source))['result']
    assert (await finish(app, row))['status'] == 'accepted'


async def test_full_exact_lineage_restart_alias_and_destination_choice(handoff):
    app, runtime, source, _, args = await headless(handoff)
    row = await finish(app, await start(app, source, args))
    target = app._session(row['targetSessionId'])
    target['runtimeSessionId'] = 'native-target-alias'
    app._publish()
    app.handoffs = NewChatHandoffs(app)
    assert read_mode(app.data_dir, target['workspace'], 'native-target-alias', owner_session_id=target['id']) == 'full'
    assert read_mode(app.data_dir, target['workspace'], 'native-target-alias') == 'full'
    await save(app, target, mode='ask', scope='workspace')
    assert app.access_mode.snapshot(target)['mode'] == 'ask'
    await save(app, target, mode='inherit', scope='workspace')
    captured = app.access_mode.snapshot(target)['revision']
    await app.dispatch('conversation.stop', {'sessionId': source})
    assert app.access_mode.snapshot(target)['revision'] != captured
    assert app.access_mode.full(target)
    target['handoffOrigin']['requestId'] = 'invented'
    app._publish()
    assert app.access_mode.snapshot(target)['mode'] == 'ask'
    assert app.access_mode.snapshot(target)['source'] == 'unavailable'


@pytest.mark.parametrize('phase', ['session.create', 'conversation.send'])
async def test_full_lost_response_and_restart_do_not_replay(handoff, monkeypatch, phase):
    app, runtime, source, _, args = await headless(handoff)
    original = app.handoffs.perform
    async def lost(row, action):
        value = await original(row, action)
        if action == phase:
            raise RuntimeError('lost reply')
        return value
    monkeypatch.setattr(app.handoffs, 'perform', lost)
    result = await finish(app, await start(app, source, args))
    assert result['creation'] == 'created'
    assert result['status'] in {'unknown', 'accepted'}
    app.handoffs = NewChatHandoffs(app)
    count = len(runtime.sent)
    for _ in range(2):
        await app.dispatch('session.handoff.start', args, origin='system', caller_session_id=source)
        await app.dispatch('session.handoff.read', {'requestId': args['requestId']}, origin='system', caller_session_id=source)
    assert len(app.state['sessions']) == 3 and len(runtime.sent) == count


async def test_full_raw_create_and_peer_send_are_not_handoff_authority(handoff):
    app, runtime, source, _, args = await headless(handoff)
    with pytest.raises(AppError, match='raw agent session.create'):
        await app.dispatch('session.create', {'workspace': args['workspace']}, origin='system', caller_session_id=source)


@pytest.mark.parametrize('change', ['unchanged', 'complete', 'stop', 'revoke', 'off-on', 'target-stop', 'target-access', 'target-placement'])
async def test_full_final_native_write_rechecks_after_preparation(handoff, monkeypatch, change):
    from unittest.mock import AsyncMock
    from amplifier_web.runtime import RuntimeManager
    app, runtime, source, _, args = await headless(handoff)
    manager = RuntimeManager()
    entered, release = asyncio.Event(), asyncio.Event()
    prepared = []
    async def prepare(session, emit, **options):
        prepared.append(session['id'])
        entered.set()
        await release.wait()
        if options.get('guard'):
            options['guard']()
    monkeypatch.setattr(manager, 'start', prepare)
    admit = AsyncMock(return_value=('sid', 'request', 'waiter'))
    monkeypatch.setattr(manager, '_admit', admit)
    monkeypatch.setattr(manager, '_reply', AsyncMock(return_value={'accepted': True}))
    runtime.send = manager.send
    row = await start(app, source, args)
    await asyncio.wait_for(entered.wait(), 5)
    if change in {'stop', 'target-stop'}:
        await app.dispatch('conversation.stop', {'sessionId': source if change == 'stop' else row['targetSessionId']})
    elif change in {'revoke', 'off-on'}:
        await save(app, app._session(source), mode='ask')
        if change == 'off-on':
            await save(app, app._session(source))
    elif change == 'complete':
        app._session(source)['collaborationGeneration']['terminal'] = True
    elif change == 'target-access':
        await save(app, app._session(row['targetSessionId']), mode='ask')
    elif change == 'target-placement':
        app._session(row['targetSessionId']).update(workingDirectory=args['workspace'] + '/moved', executionRevision=1)
    release.set()
    for _ in range(300):
        retained = app.handoffs.load(row['requestId'])
        if retained['delivery'] in {'accepted', 'failed'}:
            break
        await asyncio.sleep(.01)
    if change in {'unchanged', 'complete'}:
        assert retained['delivery'] == 'accepted', retained
        admit.assert_awaited_once()
        assert admit.call_args.args == (row['targetSessionId'], 'send', {
            'text': args['text'], 'input_id': row['inputId'], 'reply_context': None,
            'context_binding': {'clientId': None, 'targets': []}, 'attachments': []})
    else:
        assert retained['delivery'] == 'failed', retained
        admit.assert_not_awaited()
    assert prepared == [row['targetSessionId']]


@pytest.mark.parametrize('warm', [False, True])
@pytest.mark.parametrize('revoke', [False, True])
async def test_full_handoff_new_and_existing_process_final_write(handoff, tmp_path, monkeypatch, warm, revoke):
    """Actual process transport; scripted worker, no provider or installed runtime claim."""
    import sys
    from amplifier_web.runtime import RuntimeManager
    app, _, source, _, args = await headless(handoff)
    log = tmp_path / 'native-input.jsonl'
    script = r'''
import json,sys
for line in sys.stdin:
    row=json.loads(line)
    with open(sys.argv[1], 'a') as stream: stream.write(line)
    if row['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{'session_id':row['session']['id']}}),flush=True)
    elif row['op']=='send':
        print(json.dumps({'type':'input.delivered','input_id':row['input_id']}),flush=True)
        print(json.dumps({'op':'reply','id':row['id'],'result':{'accepted':True}}),flush=True)
    elif row['op']=='stop': break
    elif 'id' in row:
        print(json.dumps({'op':'reply','id':row['id'],'result':{}}),flush=True)
'''
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', script, str(log)], startup_timeout=5)
    await app.replace_runtime(manager)
    entered, release = asyncio.Event(), asyncio.Event()
    request = manager._request
    async def delayed(sid, operation, **values):
        if operation == 'send':
            entered.set()
            await release.wait()
        return await request(sid, operation, **values)
    monkeypatch.setattr(manager, '_request', delayed)
    if warm:
        perform = app.handoffs.perform
        async def prewarm(row, action):
            receipt = await perform(row, action)
            if action == 'session.create':
                await manager.start(app._session(row['targetSessionId']), app.on_runtime_event)
            return receipt
        monkeypatch.setattr(app.handoffs, 'perform', prewarm)
    row = await start(app, source, args)
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if revoke:
            await save(app, app._session(source), mode='ask')
        release.set()
        for _ in range(400):
            retained = app.handoffs.load(row['requestId'])
            if retained['delivery'] in {'accepted', 'failed'}:
                break
            await asyncio.sleep(.01)
        assert retained['delivery'] == ('failed' if revoke else 'accepted'), retained
        writes = [json.loads(line) for line in log.read_text().splitlines()]
        sends = [value for value in writes if value['op'] == 'send']
        assert len(sends) == (0 if revoke else 1)
        if sends:
            assert sends[0]['input_id'] == row['inputId'] and sends[0]['text'] == args['text']
        assert len([value for value in writes if value['op'] == 'start']) == 1
    finally:
        release.set()


async def test_full_source_stop_while_waiting_for_owner_lock(handoff):
    app, runtime, source, _, args = await headless(handoff)
    async with app.lock:
        pending = asyncio.create_task(start(app, source, args))
        await asyncio.sleep(0)
        app._session(source)['interruptionRevision'] = 1
    with pytest.raises(AppError, match='stopped'):
        await pending
    assert app.handoffs.load(args['requestId']) is None and not runtime.sent


@pytest.mark.parametrize('changed_configuration', [False, True])
async def test_native_send_composes_handoff_and_configuration_guards(tmp_path, monkeypatch, changed_configuration):
    from unittest.mock import AsyncMock
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    manager = RuntimeManager()
    session = {'id': 'target', 'workspace': str(tmp_path), 'nextMessageRevision': 1}
    observed, current = [], True
    def configuration_guard():
        observed.append('configuration')
        if not current:
            raise ValueError('Configuration changed')
    def handoff_guard():
        observed.append('handoff')
    manager._input_configuration = AsyncMock(return_value=(session, configuration_guard))
    async def start(session, emit, **options):
        nonlocal current
        options['guard']()
        current = not changed_configuration
    monkeypatch.setattr(manager, 'start', start)
    admit = AsyncMock(return_value=('target', 'request', 'waiter'))
    monkeypatch.setattr(manager, '_admit', admit)
    monkeypatch.setattr(manager, '_reply', AsyncMock(return_value={'accepted': True}))
    emit = AsyncMock()
    if changed_configuration:
        with pytest.raises(RuntimeStartupError):
            await manager.send(session, 'brief', 'input', emit, admission_check=handoff_guard)
        admit.assert_not_awaited()
    else:
        await manager.send(session, 'brief', 'input', emit, admission_check=handoff_guard)
        admit.assert_awaited_once()
        emit.assert_awaited_once_with('runtime.nextMessageApplied', {'sessionId': 'target', 'revision': 1,
                                                                    'bundle': None, 'selection': None})
    assert observed[-2:] == ['handoff', 'configuration']


async def test_full_host_restart_retains_lineage_actor_and_exact_receipt(handoff):
    from amplifier_web.service import AppService
    from test_service import Runtime
    app, _, source, _, args = await headless(handoff)
    row = await finish(app, await start(app, source, args))
    authority = copy.deepcopy(row['authority'])
    target_id = row['targetSessionId']
    home, workspace = app.data_dir, app._session(source)['workspace']
    await app.close()
    runtime = Runtime()
    reopened = AppService(home, runtime, workspace=workspace)
    try:
        target = reopened._session(target_id, hydrate=False)
        assert reopened.access_mode.full(target)
        assert read_mode(home, args['workspace'], target_id) == 'full'
        assert reopened.handoffs.load(args['requestId'])['authority'] == authority
        result = await reopened.dispatch('session.handoff.start', args, origin='system', caller_session_id=source)
        assert result['duplicate'] and result['result']['status'] == 'accepted'
        assert result['result']['targetSessionId'] == target_id
        assert not runtime.sent
    finally:
        await reopened.close()


async def test_full_creation_respects_existing_destination_ask(handoff):
    app, runtime, source, _, args = await headless(handoff)
    made = await app.dispatch('session.create', {'title': 'Destination preference owner',
        'workspace': args['workspace'], 'select': False})
    destination = app._session(made['sessionId'])
    await save(app, destination, mode='ask', scope='workspace')
    result = await finish(app, await start(app, source, args))
    assert result['delivery'] == 'accepted'
    assert app.access_mode.snapshot(app._session(result['targetSessionId']))['mode'] == 'ask'
    assert len(runtime.sent) == 1


@pytest.mark.parametrize('change', ['complete', 'revoke', 'off-on', 'stop', 'target-ask', 'target-full'])
async def test_full_handoff_schedule_keeps_original_lineage_at_final_admission(handoff, monkeypatch, change):
    from test_full_schedules import preview, invoke
    from test_schedules import Runtime, attach

    app, handoff_runtime, source_id, _, args = await headless(handoff)
    handed = await finish(app, await start(app, source_id, args))
    assert handed['delivery'] == 'accepted'
    source, target = app._session(source_id), app._session(handed['targetSessionId'])
    assert app.access_mode.snapshot(target)['source'] == 'inherited'
    if change == 'target-full':
        await save(app, target)
    # Reuse the schedule owner's task/runtime fixture after the initial handoff.
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    runtime, now = Runtime(), [1800000000.0]
    attach(app, runtime, now)
    await app.dispatch('task.create', {'sessionId': target['id'], 'expectedRevision': 0,
        'objective': 'Continue the independently handed-off report'}, command_id='target-task')
    request = await preview(app, target, now[0])
    record = (await invoke(app, target, 'schedule.create', request, 'target-schedule', 'system'))['schedule']
    authority = record['authorization']['authority']
    assert authority['sessionId'] == target['id']
    assert authority['accessRevision'] == app.access_mode.snapshot(target)['revision']
    assert 'generationId' not in authority and 'messageId' not in record['authorization']
    source['collaborationGeneration']['terminal'] = True
    target.pop('collaborationGeneration', None)
    original = runtime.scheduled_input

    async def held(sid, values, guard):
        if change in {'revoke', 'off-on', 'target-full'}:
            await save(app, source, mode='ask')
            if change == 'off-on':
                await save(app, source)
        elif change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source_id})
        elif change == 'target-ask':
            await save(app, target, mode='ask')
        return await original(sid, values, guard)

    runtime.scheduled_input = held
    now[0] += 61
    await app.schedules.tick()
    run = app.schedules.store.runs(target['id'])[0]
    assert run['authorizationCommandId'] == record['authorization']['commandId'] == 'target-schedule'
    assert len(handoff_runtime.sent) == 1
    if change in {'complete', 'target-full'}:
        assert run['phase'] == 'accepted', run
        assert len(runtime.inputs) == 1
        assert app.access_mode.full(target)
    else:
        assert run['phase'] == 'skipped', run
        assert not runtime.inputs
        assert app.schedules.store.get(target['id'], record['id'])['status'] == 'needs_review'
    assert app.schedules.store.get(target['id'], record['id'])['authorization']['authority'] == authority
