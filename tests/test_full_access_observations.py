"""Standing observation authority over real shared actions and MCP stdio reads.

The observation source is a synthetic installed fixture; terminal model admission
is a guarded adapter, not a live provider or independently verified source review.
"""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError
from test_coordination_generation import agent
from test_observations import fixture, record


async def mode(app, sid, value='full'):
    current = (await app.dispatch('access.get', {'sessionId': sid}))['result']
    await app.dispatch('access.set', {'sessionId': sid, 'scope': 'session',
        'mode': value, 'expectedRevision': current['revision']})


async def generation(app, sid, identity='system-work', input_id='trusted-system-input'):
    source = app._session(sid)
    app.collaboration.observe(source, 'runtime.generation', {'sessionId': sid,
        'rootSessionId': sid, 'event': 'generation.started', 'generation_id': identity,
        'interruptionRevision': source.get('interruptionRevision', 0)})
    app.collaboration.observe(source, 'runtime.status', {'sessionId': sid,
        'event': 'input.delivered', 'inputId': input_id})


async def full_fixture(tmp_path, monkeypatch):
    values = await fixture(tmp_path, monkeypatch)
    app, runtime, _, sid, *_ = values
    # No mounted provider needs retirement when the operator saves the preference.
    runtime.workers = {}
    runtime.stop = AsyncMock()
    await generation(app, sid)
    async def admit(owner, args, guard):
        reason = guard()
        if reason:
            return {'accepted': False, 'reason': reason}
        allowed = app.observations.authorize_admission(owner, args)
        return {'accepted': allowed['admitted'], 'inputId': args['inputId']}
    runtime.observation_input = AsyncMock(side_effect=admit)
    await mode(app, sid)
    return values


async def dispatch(app, sid, action, args, origin='agent'):
    if origin == 'agent':
        return await agent(app, app._session(sid), action, args,
            action + ':' + args.get('requestId', 'read'))
    # Origin is supplied by the trusted host entry, never a field in tool args.
    return await app.dispatch(action, args, origin=origin, caller_session_id=sid)


async def arm(app, sid, config, origin='agent', request='arm'):
    preview = (await dispatch(app, sid, 'observation.preview', config, origin))['result']
    payload = {**config, 'previewHash': preview['previewHash'], 'requestId': request}
    return (await dispatch(app, sid, 'observation.create', payload, origin))['result']['watch'], payload


async def resume(app, sid, config, watch, origin='agent', request='resume'):
    preview = (await dispatch(app, sid, 'observation.preview', config, origin))['result']
    return await dispatch(app, sid, 'observation.resume', {
        **config, 'id': watch['id'], 'expectedRevision': watch['revision'],
        'requestId': request, 'previewHash': preview['previewHash']}, origin)


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_watch_qualifies_runs_and_resumes_without_human_or_client(tmp_path, monkeypatch, origin):
    app, runtime, now, sid, config, data, _, supplied = await full_fixture(tmp_path, monkeypatch)
    try:
        source = app._session(sid)
        assert not any(row.get('role') == 'user' for row in source['messages'])
        assert not app.queues
        qualified = (await dispatch(app, sid, 'observation.qualify',
            {'sessionId': sid, 'descriptor': supplied}, origin))['result']['observer']
        expected = {'origin': origin, 'sessionId': sid,
            'accessRevision': app.access_mode.snapshot(source)['revision'], 'at': now[0]}
        if origin == 'agent':
            expected.update(runtimeSessionId=sid, generationId='system-work',
                inputIds=['trusted-system-input'], inputCoverage='complete')
        assert qualified['authorization'] == expected
        assert qualified['qualification'] == supplied['qualification']
        assert 'approvedBy' not in qualified and 'verified' not in qualified['qualification']
        config['observerId'] = qualified['id']
        watch, payload = await arm(app, sid, config, origin)
        assert watch['authorization'] == expected
        assert watch['presentationGrant'] is None
        before = copy.deepcopy(source['messages'])
        await app.observations.tick()
        runtime.observation_input.assert_not_awaited()
        paused = (await dispatch(app, sid, 'observation.pause', {
            'sessionId': sid, 'id': watch['id'], 'expectedRevision': watch['revision'], 'requestId': 'pause'}, origin))['result']['watch']
        resumed = (await resume(app, sid, config, paused, origin))['result']['watch']
        assert resumed['authorization']['origin'] == origin
        assert resumed['authorization']['accessRevision'] == expected['accessRevision']
        assert 'messageId' not in resumed['authorization']
        record(data / 'record.json', 'actionable', 'finished')
        now[0] += 16
        await app.observations.tick()
        now[0] += 16
        await app.observations.tick()
        assert runtime.observation_input.await_count == 1
        handoff = app.observations.store.rows('outbox')[0]
        assert handoff['phase'] == 'accepted'
        ended = app.observations.store.get('watch', watch['id'])
        with pytest.raises(AppError, match='nonterminal'):
            await resume(app, sid, config, ended, origin, request='cannot-replay')
        duplicate = (await dispatch(app, sid, 'observation.create', payload, origin))['result']
        assert duplicate['duplicate'] and duplicate['watch']['status'] == 'ended'
        assert len(app.observations.store.rows('outbox')) == 1
        assert source['messages'] == before and not source['approvals']
        runtime.stop.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('phase', ['before-read', 'read-return', 'handoff-admission'])
async def test_full_revocation_fences_delayed_reads_and_handoffs_even_after_reenable(tmp_path, monkeypatch, phase):
    app, runtime, _, sid, config, data, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        watch, _ = await arm(app, sid, config)
        record(data / 'record.json', 'actionable')
        async def revoke():
            await mode(app, sid, 'ask')
            await mode(app, sid, 'full')
            assert app.access_mode.full(app._session(sid))
        if phase == 'before-read':
            await revoke()
        elif phase == 'read-return':
            original = app.smart_tools.call_tool
            async def read(*args, **kwargs):
                result = await original(*args, **kwargs)
                await revoke()
                return result
            app.smart_tools.call_tool = read
        else:
            async def admit(owner, args, guard):
                await revoke()
                assert guard() == 'The saved Full access authority changed'
                assert not app.observations.authorize_admission(owner, args)['admitted']
                return {'accepted': False}
            runtime.observation_input.side_effect = admit
        await app.observations.tick()
        if phase == 'handoff-admission':
            assert app.observations.store.rows('outbox')[0]['phase'] == 'suppressed'
            assert runtime.observation_input.await_count == 1
        else:
            retained = app.observations.store.get('watch', watch['id'])
            assert retained['status'] == 'needs_review'
            assert retained['authorization'] == watch['authorization']
            runtime.observation_input.assert_not_awaited()
            runs = app.observations.store.rows('run')
            if phase == 'before-read':
                assert not runs
            else:
                assert runs[0]['phase'] == 'discarded'
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['stop', 'revocation'])
async def test_full_resume_cannot_renew_a_stopped_or_revoked_watch(tmp_path, monkeypatch, boundary):
    app, runtime, _, sid, config, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        watch, _ = await arm(app, sid, config)
        if boundary == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': sid})
        else:
            await mode(app, sid, 'ask')
            await mode(app, sid, 'full')
        await app.observations.tick()
        retained = app.observations.store.get('watch', watch['id'])
        assert retained['status'] == 'needs_review'
        assert app.access_mode.full(app._session(sid))
        with pytest.raises(AppError, match='Full access or Stop boundary|current.*generation|Stop'):
            await resume(app, sid, config, retained)
        assert app.observations.store.get('watch', watch['id']) == retained
        runtime.observation_input.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('fresh_origin', ['agent', 'system'])
async def test_stopped_transport_cannot_arm_or_qualify_fresh_work(tmp_path, monkeypatch, fresh_origin):
    app, _, _, sid, config, _, _, descriptor = await full_fixture(tmp_path, monkeypatch)
    try:
        source = app._session(sid)
        transport = {'_runtimeSessionId': sid,
            '_generationId': source['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': value} for value in source['collaborationGeneration']['inputIds']]}
        async def old_call(action, args, identity):
            return await app.app_bridge('dispatch', {
                'action': action, 'args': args, 'id': identity, **transport}, sid)
        watch, payload = await arm(app, sid, config)
        await app.dispatch('conversation.stop', {'sessionId': sid})
        # The worker has not reported a terminal generation. Even a later status
        # update must not let its old transport capture the new Stop revision.
        assert not source['collaborationGeneration']['terminal']
        original_generation = copy.deepcopy(source['collaborationGeneration'])
        await app.on_runtime_event('runtime.generation', {'sessionId': sid,
            'rootSessionId': sid, 'event': 'generation.started',
            'generation_id': transport['_generationId']})
        assert source['collaborationGeneration'] == original_generation
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
        await app.observations.tick()
        retained = app.observations.store.get('watch', watch['id'])
        assert retained['status'] == 'needs_review'
        before = copy.deepcopy(app.observations.store.rows('observer'))
        for action, args in [
            ('observation.preview', config),
            ('observation.create', {**payload, 'requestId': 'fresh-after-stop'}),
            ('observation.qualify', {'sessionId': sid, 'descriptor': descriptor}),
        ]:
            with pytest.raises(AppError, match='current.*generation|Stop'):
                await old_call(action, args, 'stale:' + action)
        assert app.observations.store.rows('observer') == before
        assert app.observations.store.rows('watch') == [retained]
        duplicate = (await old_call('observation.create', payload, 'original-receipt'))['result']
        assert duplicate['duplicate'] and duplicate['watch'] == retained

        if fresh_origin == 'agent':
            await generation(app, sid, 'new-system-work', 'new-trusted-system-input')
        qualified = (await dispatch(app, sid, 'observation.qualify',
            {'sessionId': sid, 'descriptor': descriptor}, fresh_origin))['result']['observer']
        fresh, _ = await arm(app, sid, config, fresh_origin, request='new-work')
        assert fresh['authorization']['origin'] == fresh_origin
        assert fresh['interruptionRevision'] == source['interruptionRevision']
        assert qualified['authorization']['origin'] == fresh_origin
        if fresh_origin == 'agent':
            assert fresh['authorization']['generationId'] == 'new-system-work'
            assert fresh['authorization']['inputIds'] == ['new-trusted-system-input']
        else:
            assert 'generationId' not in fresh['authorization']
        assert not any(row.get('role') == 'user' for row in source['messages'])
        assert not source['approvals']
    finally:
        await app.close()


async def test_full_preview_binds_the_saved_access_revision(tmp_path, monkeypatch):
    app, _, _, sid, config, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        preview = (await dispatch(app, sid, 'observation.preview', config))['result']
        await mode(app, sid, 'full')
        with pytest.raises(AppError, match='preview changed'):
            await dispatch(app, sid, 'observation.create', {
                **config, 'previewHash': preview['previewHash'], 'requestId': 'old-preview'})
        assert not app.observations.store.rows('watch')
        watch, _ = await arm(app, sid, config, request='current-preview')
        assert watch['authorization']['accessRevision'] == app.access_mode.snapshot(app._session(sid))['revision']
    finally:
        await app.close()


@pytest.mark.parametrize('transport', [{'_inputBindings': None}, {'_inputBindings': {}},
    {'_inputBindings': [{}]}, {'_inputBindings': ['malformed']},
    {'_inputBindings': [{'inputId': 'never-admitted'}]}, {'_generationId': 'obsolete'}])
async def test_full_cannot_borrow_current_owner_authority_without_current_work(tmp_path, monkeypatch, transport):
    app, _, _, sid, config, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        with pytest.raises(AppError, match='current.*generation|current admitted input'):
            await agent(app, app._session(sid), 'observation.preview', config, 'unbound', **transport)
        assert not app.observations.store.rows('watch')
    finally:
        await app.close()


async def test_full_unknown_handoff_is_retained_without_retry_or_resume(tmp_path, monkeypatch):
    app, runtime, now, sid, config, data, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        watch, payload = await arm(app, sid, config)
        record(data / 'record.json', 'actionable')
        runtime.observation_input.side_effect = RuntimeError('Admission reply lost')
        await app.observations.tick()
        assert app.observations.store.rows('outbox')[0]['phase'] == 'unknown'
        now[0] += 16
        await app.observations.tick()
        assert runtime.observation_input.await_count == 1
        ended = app.observations.store.get('watch', watch['id'])
        with pytest.raises(AppError, match='nonterminal'):
            await resume(app, sid, config, ended)
        duplicate = (await dispatch(app, sid, 'observation.create', payload))['result']
        assert duplicate['duplicate'] and duplicate['watch'] == ended
        assert app.observations.store.rows('outbox')[0]['phase'] == 'unknown'
    finally:
        await app.close()


@pytest.mark.parametrize('invalid', ['evidence', 'code', 'account'])
async def test_full_qualification_retains_real_source_and_evidence_checks(tmp_path, monkeypatch, invalid):
    app, _, _, sid, _, _, _, descriptor = await full_fixture(tmp_path, monkeypatch)
    try:
        before = copy.deepcopy(app.observations.store.rows('observer'))
        if invalid == 'evidence':
            descriptor['qualification']['evidence'] = []
        elif invalid == 'code':
            path = app.smart_tools.root / 'installs' / descriptor['installationId'] / 'observer.py'
            path.write_text('Changed after review')
        else:
            app.smart_tools._server('observer')['transport'] = 'streamable-http'
        with pytest.raises(AppError, match='evidence|implementation changed|verified connector account'):
            await dispatch(app, sid, 'observation.qualify', {'sessionId': sid, 'descriptor': descriptor})
        assert app.observations.store.rows('observer') == before
    finally:
        await app.close()


async def test_full_does_not_fabricate_a_required_factual_answer(tmp_path, monkeypatch):
    app, runtime, _, sid, config, _, control, _ = await full_fixture(tmp_path, monkeypatch)
    try:
        question = (await app.dispatch('question.create', {'sessionId': sid,
            'prompt': 'Which exact record contains the result?', 'required': True,
            'dependency': 'Choose the record to observe'}))['result']
        task = json.loads(control.read_text())
        task['task']['questionIds'] = [question['id']]
        control.write_text(json.dumps(task))
        watch, _ = await arm(app, sid, config)
        await app.observations.tick()
        assert app.questions.store.get(sid, question['id'])['status'] == 'pending'
        assert app.observations.store.get('watch', watch['id'])['status'] == 'needs_review'
        runtime.observation_input.assert_not_awaited()
    finally:
        await app.close()


async def test_ask_mode_still_requires_human_watch_authority_and_operator_qualification(tmp_path, monkeypatch):
    app, _, _, sid, config, _, _, descriptor = await full_fixture(tmp_path, monkeypatch)
    try:
        await mode(app, sid, 'ask')
        with pytest.raises(AppError, match='human request'):
            await arm(app, sid, config)
        with pytest.raises(AppError, match='real user request'):
            await arm(app, sid, config, origin='system')
        with pytest.raises(AppError, match='operator action or Full access'):
            await dispatch(app, sid, 'observation.qualify', {'sessionId': sid, 'descriptor': descriptor})
        app._session(sid)['messages'].append({'id': 'human', 'role': 'user',
            'inputOrigin': 'ui', 'text': 'Observe this record'})
        watch, _ = await arm(app, sid, {**config, 'sourceMessageId': 'human'})
        assert watch['authorization']['messageId'] == 'human'
        paused = (await dispatch(app, sid, 'observation.pause', {'sessionId': sid,
            'id': watch['id'], 'expectedRevision': watch['revision'], 'requestId': 'pause'}))['result']['watch']
        with pytest.raises(AppError, match='renewed human authority'):
            await resume(app, sid, {**config, 'sourceMessageId': 'human'}, paused)
    finally:
        await app.close()
