"""Mounted native roots/children keep actual actor and assignment on handoff."""
import pytest

from test_full_child_configuration import app, mounted, dispatch
from test_access_mode import save
from test_new_chat_handoffs import finish
from test_service import Runtime


@pytest.mark.parametrize('kind', ['root', 'finite', 'resumed', 'persistent', 'nested'])
async def test_mounted_full_headless_handoff(mounted, tmp_path, monkeypatch, kind):
    from amplifier_web.host.children import _PERSISTENT
    h = mounted
    await save(h.app, h.source)
    monkeypatch.setattr(h.app.runtime, 'sent', [], raising=False)
    async def send(session, text, input_id, emit, *, admission_check=None):
        admission_check()
        return await Runtime.send(h.app.runtime, session, text, input_id, emit)
    monkeypatch.setattr(h.app.runtime, 'send', send, raising=False)
    target = tmp_path / 'destination'; target.mkdir()
    actors = []

    async def create(control, actor):
        actors.append(actor)
        args = {'requestId': 'headless-child-' + str(len(actors)), 'sessionId': h.source['id'],
                'workspace': str(target), 'title': 'Independent delegated work', 'text': 'Inspect then report.',
                'bundle': 'anchors'}
        response = await dispatch(control, 'session.handoff.start', args, args['requestId'])
        assert response.success, response.error
        result = await finish(h.app, response.output['result'])
        assert result['status'] == 'accepted', result
        authority = result['authority']
        assert authority['runtimeSessionId'] == actor
        assert authority['sessionId'] == h.source['id']
        assert authority['generationId'] == 'generation-1'
        assert authority['inputIds'] == ['assigned-input']
        assert authority['inputCoverage'] == 'complete'
        destination = h.app._session(result['targetSessionId'])
        assert h.app.access_mode.full(destination)
        assert not destination.get('collaboration') and not destination.get('assignmentId')
        message = next(row for row in destination['messages'] if row.get('inputId') == result['inputId'])
        assert message['inputOrigin'] == 'agent'
        assert message['inputActor']['runtimeSessionId'] == actor
        assert not message.get('navigationPost')

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        actor = coordinator.session_id
        await create(tools['app_control'], actor)
        if kind == 'nested' and actor == 'child':
            h.worker.runtime.generation['input_ids'].append('later-root-input')
            h.source['collaborationGeneration']['inputIds'].append('later-root-input')
            h.worker.context_inputs = ['later-root-input']
            h.worker.context_bindings = {'later-root-input': {'clientId': None, 'targets': []}}
            await h.registry.spawn('worker', 'nested work', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'started'

    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        if kind == 'root':
            await create(h.parent.coordinator.get('tools')['app_control'], 'native-root')
        else:
            await h.registry.spawn('worker', 'start independent work', h.parent, sub_session_id='child')
            if kind == 'resumed':
                await h.registry.resume('child', 'start separate work', h.parent)
        assert actors == (['native-root'] if kind == 'root' else ['child', 'grandchild'] if kind == 'nested'
                          else ['child', 'child'] if kind == 'resumed' else ['child'])
        assert len(h.app.runtime.sent) == len(actors)
        assert not h.app.clients.records and not h.source['approvals']
        h.approval_prompt.assert_not_awaited()
    finally:
        _PERSISTENT.reset(token)
