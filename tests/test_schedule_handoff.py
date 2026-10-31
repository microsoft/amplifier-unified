"""Real dispatcher/receipt provenance; synthetic guarded runtime, no paid work."""
from types import MethodType
import pytest

from test_schedules import fixture, schedule
from test_collaborative_workspaces import Runtime as PeerRuntime, generation, agent_action, settled
from amplifier_web.service import AppError


async def setup(tmp_path, monkeypatch, *, new_task=False):
    if new_task:
        from test_schedule_new_tasks import fixture as make, create
        app, runtime, now, sid = await make(tmp_path, monkeypatch)
    else:
        app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    runtime.app, runtime.fail = app, False
    runtime.unavailable_boundaries = set()
    runtime.collaboration_input = MethodType(PeerRuntime.collaboration_input, runtime)
    record = (await create(app, sid, now[0]))[0] if new_task else await schedule(app, sid, now[0])
    app.schedules.store.review(sid, record['id'], 'Review configuration', now[0])
    record = app.schedules.store.get(sid, record['id'])
    await app.dispatch('session.create', {'title': 'Infrastructure helper'})
    source, target = app._session(), app._session(sid)
    now[0] += 1
    human = app._message(source, 'user', 'Repair this existing schedule.', 'chat',
                         inputId='human-repair', inputOrigin='ui')
    human['createdAt'] = now[0]
    await generation(app, source, 'human-generation', ['human-repair'])
    value = {'sessionId':sid,'text':'Re-review and resume the named schedule unchanged.',
             'scheduleRepair':{'sourceMessageId':human['id'],
                 'schedules':[{'id':record['id'],'expectedRevision':record['revision']}]}}
    return app, runtime, now, source, target, human, record, value


async def handoff(app, source, target, value):
    receipt = await agent_action(app, source, 'coordination.send', value, 'repair-handoff')
    receipt = await settled(app, 'repair-handoff', 'accepted')
    await generation(app, target, 'owner-repair-generation', ['repair-handoff'])
    return receipt


async def resume(app, target, record, human):
    args = {'sessionId':target['id'], **app.schedules.configuration(record)}
    preview = (await app.dispatch('schedule.preview', args))['result']
    return await agent_action(app, target, 'schedule.resume', {
        'sessionId':target['id'],'id':record['id'],'expectedRevision':record['revision'],
        'previewHash':preview['previewHash'],'sourceMessageId':human['id'],
        'handoffRequestId':'repair-handoff'}, 'repair-resume')


@pytest.mark.parametrize('new_task', [False, True])
async def test_human_handoff_repairs_exact_schedule_and_next_due_run_is_admitted(tmp_path, monkeypatch, new_task):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch, new_task=new_task)
    try:
        receipt = await handoff(app, source, target, value)
        assert receipt['scheduleRepair']['sourceMessageId'] == human['id']
        result = (await resume(app, target, record, human))['result']['schedule']
        assert result['status'] == 'active'
        assert app.schedules.configuration(result) == app.schedules.configuration(record)
        authority = result['authorization']
        assert authority['messageId'] == human['id']
        assert authority['handoff']['requestId'] == 'repair-handoff'
        assert authority['handoff']['senderSessionId'] == source['id']
        # Separate actual due admission through the guarded scheduler/runtime boundary.
        await app.on_runtime_event('runtime.status', {'sessionId':target['id'],'status':'idle'})
        runtime.inputs.clear();now[0] += 61
        await app.schedules.tick()
        assert len(runtime.inputs) == 1
        run = app.schedules.store.runs(target['id'])[0]
        assert run['phase'] == 'accepted'
        if new_task:
            assert run['destinationSessionId'] == runtime.inputs[0][0] != target['id']
            assert run['creationConfirmed']
        else:
            assert runtime.inputs[0][0] == target['id']
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['agent', 'question', 'scheduled', 'unadmitted', 'old-human', 'revision'])
async def test_forged_or_stale_handoff_is_rejected_before_receipt(tmp_path, monkeypatch, change):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch)
    try:
        if change == 'agent': human['inputOrigin'] = 'peer'
        elif change == 'question': human['questionId'] = 'answered-question'
        elif change == 'scheduled': human['scheduledRunId'] = 'scheduled'
        elif change == 'unadmitted': human['inputId'] = 'not-delivered'
        elif change == 'old-human': app._message(source, 'user', 'Wait.', 'chat', inputOrigin='ui')
        elif change == 'revision': value['scheduleRepair']['schedules'][0]['expectedRevision'] -= 1
        with pytest.raises(AppError):
            await agent_action(app, source, 'coordination.send', value, 'invalid-repair')
        assert app.schedules.store.get(target['id'],record['id'])['status'] == 'needs_review'
        assert not runtime.inputs
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['source-stop','target-stop','edit','new-human','target-human','workspace','unrelated','revision','not-admitted'])
async def test_handoff_cannot_outlive_scope_or_human_correction(tmp_path, monkeypatch, change):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch)
    try:
        await handoff(app, source, target, value)
        if change == 'source-stop': source['interruptionRevision'] = 1
        elif change == 'target-stop': target['interruptionRevision'] = 1
        elif change == 'edit': human['text'] = 'Do not repair it.'
        elif change == 'new-human': app._message(source, 'user', 'Wait.', 'chat', inputOrigin='ui')
        elif change == 'target-human': app._message(target, 'user', 'Wait.', 'chat', inputOrigin='ui')
        elif change == 'workspace': source['workspace'] += '-different'
        elif change == 'unrelated':
            from test_schedules import config
            args = {'sessionId':target['id'], **config(now[0]), 'prompt':'Another schedule'}
            preview = (await app.dispatch('schedule.preview',args))['result']
            record = (await app.dispatch('schedule.create',{**args,'expectedRevision':0,
                'previewHash':preview['previewHash']},command_id='unrelated-create'))['result']['schedule']
        elif change == 'revision':
            await app.dispatch('schedule.pause', {'sessionId':target['id'], 'id':record['id'],
                'expectedRevision':record['revision']}, command_id='intervening-pause')
            record = app.schedules.store.get(target['id'],record['id'])
        elif change == 'not-admitted': await generation(app, target, 'different-generation', ['other-input'])
        with pytest.raises(AppError):
            await resume(app, target, record, human)
    finally:
        await app.close()


@pytest.mark.parametrize('new_task', [False, True])
@pytest.mark.parametrize('change', ['stop', 'edit', 'missing', 'ui', 'user', 'voice'])
async def test_later_correction_wins_actual_schedule_admission(tmp_path, monkeypatch, new_task, change):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch, new_task=new_task)
    try:
        await handoff(app, source, target, value)
        await resume(app, target, record, human)
        await app.on_runtime_event('runtime.status', {'sessionId':target['id'],'status':'idle'})
        runtime.inputs.clear()
        def correct():
            if change == 'stop': source['interruptionRevision'] = 1
            elif change == 'edit': human['text'] = 'Do not run this.'
            elif change in {'ui', 'user', 'voice'}:
                app._message(source, 'user', 'Wait; do not run this.', 'chat', inputOrigin=change)
            else: source['messages'].remove(human)
        runtime.before_admission = correct
        now[0] += 61
        await app.schedules.tick()
        assert not runtime.inputs
        assert app.schedules.store.runs(target['id'])[0]['phase'] == 'skipped'
        assert app.schedules.store.get(target['id'],record['id'])['status'] == 'needs_review'
    finally:
        await app.close()


@pytest.mark.parametrize('new_task', [False, True])
@pytest.mark.parametrize('origin', ['ui', 'user', 'voice', 'peer'])
async def test_due_recheck_distinguishes_new_human_input_from_peer_updates(tmp_path, monkeypatch, new_task, origin):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch, new_task=new_task)
    try:
        await handoff(app, source, target, value)
        await resume(app, target, record, human)
        revision = source.get('interruptionRevision', 0)
        app._message(source, 'user', 'Later input', 'chat', inputOrigin=origin)
        assert source.get('interruptionRevision', 0) == revision
        assert human in source['messages'] and human['text'] == 'Repair this existing schedule.'
        await app.on_runtime_event('runtime.status', {'sessionId':target['id'],'status':'idle'})
        runtime.inputs.clear(); now[0] += 61
        await app.schedules.tick()
        accepted = origin == 'peer'
        assert len(runtime.inputs) == int(accepted)
        runs = app.schedules.store.runs(target['id'])
        assert [row['phase'] for row in runs] == (['accepted'] if accepted else [])
        if not accepted:
            assert app.schedules.store.get(target['id'], record['id'])['status'] == 'needs_review'
    finally:
        await app.close()


async def test_changed_new_task_configuration_is_rebound_without_changing_schedule(tmp_path, monkeypatch):
    app, runtime, now, source, target, human, record, value = await setup(tmp_path, monkeypatch, new_task=True)
    try:
        original = record['newTaskConfiguration']
        target['selection']['effort'] = 'low'
        await handoff(app, source, target, value)
        result = (await resume(app, target, record, human))['result']['schedule']
        assert result['newTaskConfiguration'] != original
        assert app.schedules.configuration(result) == app.schedules.configuration(record)
        await app.on_runtime_event('runtime.status', {'sessionId':target['id'],'status':'idle'})
        runtime.inputs.clear(); now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(target['id'])[0]
        assert run['phase'] == 'accepted' and len(runtime.inputs) == 1
        assert app._session(run['destinationSessionId'])['selection']['effort'] == 'low'
    finally:
        await app.close()
