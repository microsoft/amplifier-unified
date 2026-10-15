"""Reply repair before terminal, using the real bridge and scripted runtime."""
import copy

import pytest

from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action, finish, generation, send, settled


async def ready(app):
    source, target = app.state['sessions']
    request = await send(app, 'message-link-request')
    await settled(app, request['requestId'], 'accepted')
    await generation(app, target, 'recipient-message-link-request', [request['requestId']])
    args = {'requestId': request['requestId'], 'kind': 'result', 'outcome': 'success',
            'text': 'Bounded result', 'references': ['urn:fixture:evidence']}
    return source, target, request, args


@pytest.mark.parametrize('invalid', ['incoming-peer', 'user', 'foreign', 'prior-generation', 'unknown'])
async def test_invalid_output_link_can_be_repaired_before_terminal_without_consuming_revision(app, monkeypatch, invalid):
    from amplifier_web import result_artifacts
    source, target, request, args = await ready(app)
    if invalid == 'incoming-peer':
        identity = request['messageId']
    elif invalid == 'user':
        identity = app._message(target, 'user', 'Current user input')['id']
    elif invalid == 'foreign':
        identity = app._message(source, 'assistant', 'Foreign output',
                                generationId=target['collaborationGeneration']['id'])['id']
    elif invalid == 'prior-generation':
        identity = app._message(target, 'assistant', 'Earlier output', generationId='prior')['id']
    else:
        identity = 'unavailable-output'
    args['messageIds'] = [identity]
    before = copy.deepcopy(app.collaboration.receipt(request['requestId']))
    with monkeypatch.context() as patch:
        def forbidden(*_):
            pytest.fail('Invalid output IDs must fail before reading or retaining artifacts')
        patch.setattr(result_artifacts, 'prepare_files', forbidden)
        with pytest.raises(AppError, match='Omit messageIds') as error:
            await agent_action(app, target, 'coordination.reply', args, 'repairable-declaration')
    assert error.value.status == 400
    assert app.collaboration.receipt(request['requestId']) == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='repairable-declaration'").fetchone()
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    args.pop('messageIds')
    declaration = await agent_action(app, target, 'coordination.reply', args, 'repairable-declaration')
    assert declaration['result']['responseRevision'] == 1
    await finish(app, target, request['requestId'])
    retained = app.collaboration.receipt(request['requestId'])['response']
    assert retained['qualified'] and retained['status'] == 'sealed'
    # Successful retry identity stays exact even after the current turn ends.
    duplicate = await agent_action(app, target, 'coordination.reply', args, 'repairable-declaration')
    assert duplicate['duplicate'] and duplicate['result'] == retained


async def test_message_link_rechecked_after_off_lock_artifact_preparation(app, monkeypatch):
    from amplifier_web import result_artifacts
    _, target, request, args = await ready(app)
    message = app._message(target, 'assistant', 'Current output',
                           generationId=target['collaborationGeneration']['id'])
    args['messageIds'] = [message['id']]
    original = result_artifacts.prepare
    async def prepare_then_change(*values):
        prepared = await original(*values)
        message['role'] = 'user'
        return prepared
    monkeypatch.setattr(result_artifacts, 'prepare', prepare_then_change)
    with pytest.raises(AppError, match='Invalid messageIds'):
        await agent_action(app, target, 'coordination.reply', args, 'changed-link')
    assert 'response' not in app.collaboration.receipt(request['requestId'])
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='changed-link'").fetchone()
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0


async def test_current_checkpoint_link_does_not_load_transcript_during_declaration(app, monkeypatch):
    from amplifier_web import automatic_history
    _, target, _, args = await ready(app)
    target['nativeProject'] = 'bounded-checkpoint-fixture'
    target['collaborationMessageAnchors'] = [{'messageId': 'checkpoint-output', 'nativeIndex': 10,
                                            'generationId': target['collaborationGeneration']['id']}]
    args['messageIds'] = ['checkpoint-output']
    def forbidden(*_, **__):
        pytest.fail('Declaration must not load history outside the retained current anchors')
    monkeypatch.setattr(automatic_history, 'read_transcript', forbidden)
    result = await agent_action(app, target, 'coordination.reply', args, 'checkpoint-reply')
    assert result['result']['status'] == 'staged' and not result['result']['qualified']


async def test_discovered_schema_and_skill_explain_incoming_id_repair(app):
    from amplifier_web.builtin_behaviors import resource_root
    source = app.state['sessions'][0]
    page = await app.app_bridge('list_actions', {'prefix': 'coordination.reply'}, source['id'])
    action, = page['items']
    description = action['inputSchema']['properties']['messageIds']['description']
    assert 'current generation' in description and "incoming request's messageId" in description
    assert 'Omit messageIds' in description
    skill = (resource_root()/'skills/coordinate-work/SKILL.md').read_text()
    assert "incoming peer request's `messageId`" in skill
    assert 'Omit `messageIds`' in skill
