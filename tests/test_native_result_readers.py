"""Actual mounted children inspect saved evidence using their own live provenance."""
from copy import deepcopy

import pytest

from test_access_mode import save
from amplifier_web import task_environments as environments
from amplifier_web.management import Management
from test_collaborative_workspaces import app as collaboration_app, agent_action, generation, settled
from test_full_child_configuration import mounted, dispatch
from test_coordination_generation import source_plan
from test_result_artifacts import setup, declare
from test_task_environments import backend, scope
from test_test_queue import queue, drive


@pytest.fixture
def reader_mode(request):
    return getattr(request, 'param', 'full')


@pytest.fixture
async def app(collaboration_app, reader_mode):
    collaboration_app.management = Management(collaboration_app)
    # Configure the root before admitting its synthetic native child, as the
    # mounted-child fixture does. A live settings handoff is a separate fence.
    collaboration_app.state['sessions'][0]['status'] = 'idle'
    collaboration_app.state['sessions'][0]['runtimeSessionId'] = 'native-root'
    source_plan(collaboration_app, collaboration_app.state['sessions'][0])
    await save(collaboration_app, mode=reader_mode)
    # The real settings owner retired the old mount. The mounted fixture below
    # represents its newly prepared replacement, with the saved configuration.
    collaboration_app.state['sessions'][0]['status'] = 'idle'
    return collaboration_app


@pytest.mark.parametrize('mode', ['finite', 'nested', 'resumed', 'persistent', 'observer'])
async def test_mounted_full_child_reads_saved_tests_results_and_artifacts(mounted, tmp_path, queue, backend, mode):
    from amplifier_web.host.children import _PERSISTENT
    h = mounted
    source, peer, _, args = await setup(h.app, tmp_path)
    if mode == 'observer':
        sender = h.app._new_session({'title': 'Other request owner'})
        h.app.state['sessions'].append(sender)
        await generation(h.app, sender, 'other-generation', ['other-input'])
        await agent_action(h.app, sender, 'coordination.send', {
            'sessionId': peer['id'], 'text': 'Review evidence', 'mode': 'auto'}, 'observer-request')
        await settled(h.app, 'observer-request', 'accepted')
        await generation(h.app, peer, 'other-result', ['observer-request'])
        args['requestId'] = 'observer-request'
    await declare(h.app, peer, args)
    producer = {**scope(h.app), 'sessionId': peer['id']}
    await h.app.dispatch('workspace.tests.submit', {**producer, 'requestId': 'check',
        'profileId': environments.PROFILE, 'command': 'python -V', 'notify': False})
    await drive(queue)
    assert not source.get('fileAccessPending'), source.get('configurationBusy')
    assert h.app.access_mode.full(source)
    before = deepcopy(backend), len(h.app.runtime.inputs), deepcopy(h.app.state['view'])
    visited = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        visited.append(coordinator.session_id)
        control = tools['app_control']
        queries = [
            ('workspace.tests.read', scope(h.app)),
            ('workspace.tests.read', producer),
            ('workspace.tests.result', {**producer, 'requestId': 'check'}),
            ('coordination.result', {'requestId': args['requestId']}),
            ('coordination.artifact', {'sessionId': source['id'], 'requestId': args['requestId'],
                                       'reference': args['references'][0]}),
        ]
        for action, payload in queries:
            result = await dispatch(control, action, payload, action + str(len(visited)))
            assert result.success, (action, result.error)
            assert result.output['accepted']
            if action == 'workspace.tests.result':
                assert result.output['result']['exitCode'] == 0
            if action == 'coordination.artifact':
                assert result.output['result']['text'] == 'Original exact bytes'
        # A passive review never lends the root's write or output-library identity.
        for action, payload in [
            ('workspace.tests.cancel', {**scope(h.app), 'requestId': 'check'}),
            ('workspace.tests.submit', {**scope(h.app), 'requestId': 'child-new',
                                       'profileId': environments.PROFILE, 'command': 'true'}),
            ('coordination.reply', args),
            ('outputs.list', {'sessionId': peer['id']}),
        ]:
            result = await dispatch(control, action, payload, action + str(len(visited)))
            assert not result.success, action
        if mode == 'nested' and coordinator.session_id == 'child':
            await h.registry.spawn('worker', 'review', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'reviewed'

    h.install_execute(execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await h.registry.spawn('worker', 'review', h.parent, sub_session_id='child')
        if mode == 'resumed':
            await h.registry.resume('child', 'review retained results', h.parent)
    finally:
        _PERSISTENT.reset(token)
    assert visited == (['child', 'grandchild'] if mode == 'nested' else ['child', 'child'] if mode == 'resumed' else ['child'])
    assert (backend, len(h.app.runtime.inputs), h.app.state['view']) == before
    h.approval_prompt.assert_not_awaited()
    assert h.envelopes[-1]['_runtimeSessionId'] in visited


@pytest.mark.parametrize('reader_mode', ['ask'], indirect=True)
async def test_ask_child_keeps_existing_retained_peer_image_access(mounted, tmp_path):
    from test_output_images import png
    h = mounted
    source, peer, file, args = await setup(h.app, tmp_path, png())
    file.rename(file.with_suffix('.png'))
    args['references'][0] = args['references'][0].replace('report.md@', 'report.png@')
    item = (await declare(h.app, peer, args))['result']['artifacts'][0]
    assert not h.app.access_mode.full(source)
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'outputs.image', {
            'sessionId': source['id'], 'id': item['outputId'], 'sha256': item['sha256']}, 'retained-image')
        assert result.success, result.error
        assert result.output['result']['width'] == 3 and result.output['result']['height'] == 2
        return 'reviewed image'
    h.install_execute(execute)
    await h.registry.spawn('worker', 'inspect image', h.parent, sub_session_id='child')


@pytest.mark.parametrize('invalidate', ['ask', 'stop', 'revoke', 'replace-worker', 'artifact-stop'])
async def test_child_evidence_reads_obey_current_execution_and_recheck_after_file_io(mounted, tmp_path, monkeypatch, invalidate):
    h = mounted
    source, peer, _, args = await setup(h.app, tmp_path)
    await declare(h.app, peer, args)
    if invalidate == 'ask':
        await save(h.app, mode='ask')

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        control = tools['app_control']
        if invalidate == 'revoke':
            await save(h.app, mode='ask')
        elif invalidate == 'stop':
            source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        elif invalidate == 'replace-worker':
            h.app.runtime.workers[source['id']] = {**h.app.runtime.workers[source['id']],
                                                  'runtimeOwnerId': 'new-worker'}
        elif invalidate == 'artifact-stop':
            content = h.app.outputs.content_async
            async def stop_during_read(record):
                value = await content(record)
                source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
                return value
            monkeypatch.setattr(h.app.outputs, 'content_async', stop_during_read)
        action = 'coordination.artifact' if invalidate == 'artifact-stop' else 'coordination.result'
        payload = {'requestId': args['requestId']}
        if action == 'coordination.artifact':
            payload.update(sessionId=source['id'], reference=args['references'][0])
        result = await dispatch(control, action, payload, 'invalidated-read')
        assert not result.success, result.output
        assert 'Original exact bytes' not in str(result.output)
        return 'refused'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'review', h.parent, sub_session_id='child')
    h.approval_prompt.assert_not_awaited()
