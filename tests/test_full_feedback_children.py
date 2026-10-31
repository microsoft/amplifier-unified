"""Real mounted child bridges publish exact feedback; GitHub is intercepted."""
import base64
import copy

import pytest

from test_full_child_configuration import app, mounted, dispatch
from test_full_feedback import github, mode, saved, settle, staged
from test_feedback_attachments import submission
from test_feedback_followup import seed
from test_full_feedback_continuation import remote, request, record, settled


@pytest.mark.parametrize('kind', ['finite', 'resumed', 'persistent', 'nested'])
@pytest.mark.parametrize('input_count', [1, 17])
async def test_mounted_full_child_publishes_files_and_excerpt_with_original_actor(mounted, github, kind, input_count):
    from amplifier_web.host.children import _PERSISTENT
    h = mounted
    await mode(h.app, h.source)
    assigned = ['assigned-' + str(i) for i in range(input_count)]
    h.worker.runtime.generation['input_ids'] = list(assigned)
    h.source['collaborationGeneration']['inputIds'] = list(assigned)
    h.worker.context_inputs = list(assigned)
    h.worker.context_bindings = {value: {'clientId': None, 'targets': []} for value in assigned}
    h.source['messages'] = [{'id': 'excerpt-one', 'role': 'user', 'text': 'Exact delegated evidence'}]
    h.app._publish_changes(sessions={h.source['id']})
    before = copy.deepcopy(h.app.state['view'])
    actors = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        actor = coordinator.session_id
        actors.append(actor)
        suffix = str(len(actors))
        control = tools['app_control']

        async def action(name, args):
            result = await dispatch(control, name, args, name + '-' + suffix)
            assert result.success, result.error
            return result.output

        row = (await action('feedback.attachment.add', staged(identity='child-file-' + suffix)))['result']
        exported = await action('session.export', {'id': h.source['id'], 'format': 'markdown',
            'destination': 'none', 'scope': 'range', 'minimal': True,
            'fromMessageId': 'excerpt-one', 'throughMessageId': 'excerpt-one'})
        review = (await action('feedback.excerpt.review', {'id': h.source['id'],
            'snapshotId': exported['result']['snapshotId']}))['result']
        excerpt = (await action('feedback.excerpt.stage', {'id': h.source['id'],
            'reviewId': review['reviewId'], 'requestId': 'child-excerpt-' + suffix}))['result']
        identity = 'child-feedback-' + suffix
        await action('feedback.submit', submission(requestId=identity, attachmentIds=[row['id'], excerpt['id']], includeDiagnostics=False))
        await settle(h.app)
        _, payload, receipt = saved(h.app, identity)
        assert receipt['status'] == 'submitted'
        authority = receipt['authority']
        assert authority == payload['_authority']
        assert authority['origin'] == 'agent' and authority['sessionId'] == h.source['id']
        assert authority['runtimeSessionId'] == actor != 'native-root'
        assert authority['generationId'] == 'generation-1' and authority['interruptionRevision'] == 0
        assert authority['inputIds'] == (assigned if input_count <= 16 else [])
        assert authority['inputCoverage'] == ('complete' if input_count <= 16 else 'unavailable')
        assert {item['id'] for item in payload['_attachments']} == {row['id'], excerpt['id']}
        if kind == 'nested' and actor == 'child':
            h.worker.runtime.generation['input_ids'].append('later-root-input')
            h.source['collaborationGeneration']['inputIds'].append('later-root-input')
            h.worker.context_inputs = ['later-root-input']
            h.worker.context_bindings = {'later-root-input': {'clientId': None, 'targets': []}}
            await h.registry.spawn('worker', 'publish retained assignment', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'published'

    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        await h.registry.spawn('worker', 'publish exact feedback', h.parent, sub_session_id='child')
        if kind == 'resumed':
            await h.registry.resume('child', 'publish a separate report', h.parent)
        assert actors == (['child', 'grandchild'] if kind == 'nested' else ['child', 'child'] if kind == 'resumed' else ['child'])
        blobs = [base64.b64decode(value['content']) for path, value in github[0] if path.endswith('/git/blobs')]
        assert len(blobs) == 2 * len(actors)
        assert b'exact reproduction bytes' in blobs
        assert any(b'Exact delegated evidence' in blob for blob in blobs)
        assert h.app.state['view'] == before and not h.app.clients.records
        assert not h.source['approvals']
        h.approval_prompt.assert_not_awaited()
    finally:
        _PERSISTENT.reset(token)


async def test_mounted_full_child_followup_keeps_local_actor_separate_from_remote_account(mounted, remote):
    h = mounted
    await seed(h.app)
    await mode(h.app, h.source)
    completed = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        payload = request('feedback.update', remote[0], 'child-correction')
        result = await dispatch(tools['app_control'], 'feedback.update', payload, 'child-correction-action')
        assert result.success, result.error
        await settled(h.app)
        _, frozen, receipt = record(h.app, payload['requestId'])
        assert receipt['status'] == 'submitted' and receipt['author']['id'] == 7
        assert receipt['sourceSessionId'] == h.source['id']
        assert receipt['runtimeSessionId'] == coordinator.session_id == 'child'
        assert receipt['authority'] == frozen['_authority']
        assert receipt['authority']['inputIds'] == ['assigned-input']
        assert receipt['authority']['generationId'] == 'generation-1'
        assert not h.app.clients.records and not h.source['approvals']
        h.approval_prompt.assert_not_awaited()
        completed.append(True)
        return 'published'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'correct the report', h.parent, sub_session_id='child')
    assert completed == [True]
