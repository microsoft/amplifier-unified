import copy
import json

import pytest

from amplifier_web.conversation_export import markdown
from amplifier_web.service import AppError
from amplifier_web.server import create_app
from amplifier_web.automatic_history import display_message
from amplifier_web.host.storage import SessionStore
from test_automatic_history import app_factory, native_session, native_rows, files_snapshot
from test_service import Runtime


def exported(app, receipt):
    return app.state_resource(receipt['result']['content']['$resource'])


async def test_native_unloaded_export_is_complete_read_only_and_excludes_private_context(tmp_path, app_factory):
    code = '```python\nprint("α")\n\n    # exact spacing  \n```\n'
    rows = [{'role': 'system', 'content': 'PRIVATE SYSTEM'}, {'role': 'developer', 'content': 'PRIVATE DEVELOPER'},
            {'role': 'user', 'content': 'PRIVATE EPHEMERAL', 'metadata': {'ephemeral': True}}]
    rows += [{'role': 'user' if n % 2 == 0 else 'assistant', 'content': 'message-' + str(n)} for n in range(240)]
    rows += [{'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'PRIVATE REASONING'}, {'type': 'text', 'text': code}]},
             {'role': 'tool', 'content': 'PRIVATE TOOL api_key=fixture'}]
    root = native_session(tmp_path / 'cli', 'native-export', rows)
    before = files_snapshot(root)
    app = app_factory()
    await app.history.refresh()
    source = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'native-export')
    assert source['messages'] == []
    result = await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown', 'destination': 'none'})
    value = exported(app, result)
    assert 'message-0\n' in value and 'message-239\n' in value and code in value
    assert value.count('## User\n') == 120 and value.count('## Assistant\n') == 121
    assert 'PRIVATE' not in value and 'fixture' not in value
    assert source['messages'] == [] and files_snapshot(root) == before
    assert not app.runtime.started and not app.runtime.sent
    assert 'conversationExports' not in app.browser_state()


async def test_native_paged_export_keeps_exact_code_voice_and_artifact_references(tmp_path, app_factory):
    rows = [{'role': 'user' if n % 2 == 0 else 'assistant', 'content': f'message-{n}'} for n in range(140)]
    root = native_session(tmp_path / 'cli', 'paged-export', rows)
    app = app_factory()
    await app.history.refresh()
    source = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'paged-export')
    await app.history.load(source['id'])
    assert source['sharedHistoryOffset'] == 40 and len(source['messages']) == 100
    source['messages'] += [{'id': 'voice-1', 'role': 'user', 'text': 'A spoken follow-up', 'via': 'call', 'voiceId': 'call-1'},
                           {'id': 'voice-2', 'role': 'assistant', 'text': 'A spoken reply', 'via': 'call', 'voiceId': 'call-1'}]
    source['messages'][-2]['attachments'] = [{'id': 'attachment-1', 'name': 'notes.md', 'api_key': 'PRIVATE'}]
    app.state['canvasArtifacts'].append({'id': 'artifact-1', 'title': 'Saved diagram', 'kind': 'markdown', 'sessionId': source['id'], 'messageId': 'voice-1', 'body': {'secret': 'PRIVATE'}})
    app.state['canvasArtifacts'].append({'id': 'unrelated', 'title': 'PRIVATE', 'sessionId': 'another-chat'})
    before = files_snapshot(root), copy.deepcopy(source['messages'])
    result = await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown'})
    value = exported(app, result)
    assert 'message-0\n' in value and value.count('message-139\n') == 1
    assert '## User (voice)\n\nA spoken follow-up' in value and '## Assistant (voice)\n\nA spoken reply' in value
    assert 'notes.md' in value and 'artifact-1' in value and 'Saved diagram' in value
    assert 'PRIVATE' not in value and 'unrelated' not in value
    assert before == (files_snapshot(root), source['messages'])


async def test_agent_and_ui_share_immutable_snapshot_and_json_compatibility(tmp_path, app_factory):
    app = app_factory(); await app.dispatch('session.create', {'title': 'Public export'})
    source = app._session(); source['messages'] = [{'id': 'first', 'role': 'user', 'text': 'A public question'}]
    first = await app.app_bridge('dispatch', {'action': 'session.export', 'args': {'format': 'markdown', 'destination': 'none'}, 'id': 'export-once'}, source['id'])
    value = exported(app, first)
    assert first['effects'] == []
    full = await app.app_bridge('state.get', {'path': first['result']['statePath'], 'limit': 16000}, source['id'])
    assert full['value'] == value
    source['messages'].append({'id': 'later', 'role': 'assistant', 'text': 'A later answer'})
    repeated = await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown', 'destination': 'none'}, origin='agent', command_id='export-once')
    assert repeated['duplicate'] and exported(app, repeated) == value and 'later answer' not in value
    second = await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown', 'destination': 'clipboard'})
    assert 'A later answer' in exported(app, second) and second['effects'][0]['destination'] == 'clipboard'
    effect = second['effects'][0]
    await app.dispatch('session.exportResult', {'requestId': 'stale', 'status': 'error'})
    assert app.state['view']['conversationExport']['status'] == 'pending'
    await app.dispatch('session.exportResult', {'requestId': effect['requestId'], 'status': 'ready', 'message': 'Copied conversation Markdown'})
    assert app.state['view']['conversationExport']['status'] == 'ready'
    legacy = await app.dispatch('session.export', {'id': source['id']})
    assert json.loads(legacy['effects'][0]['content'])['messages'] == source['messages']
    assert legacy['effects'][0]['filename'] == 'amplifier-export.json'
    with pytest.raises(AppError, match='Choose Markdown'):
        await app.dispatch('session.export', {'id': source['id'], 'destination': 'clipboard'})


async def test_export_rejects_missing_corrupt_or_rewritten_native_history(tmp_path, app_factory):
    root = native_session(tmp_path / 'cli', 'broken-export')
    app = app_factory(); await app.history.refresh()
    source = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'broken-export')
    await app.history.load(source['id'])
    (root / 'transcript.jsonl').write_text(json.dumps({'role': 'user', 'content': 'Rewritten question'}) + '\n')
    with pytest.raises(AppError, match='rewritten'):
        await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown'})
    (root / 'transcript.jsonl').unlink()
    with pytest.raises(AppError, match='unavailable'):
        await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown'})
    (root / 'transcript.jsonl').write_text('{broken')
    with pytest.raises(AppError):
        await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown'})
    assert not app.state.get('conversationExports')


def test_canonical_voice_delegation_is_readable_without_replaying_or_duplicate_ui_speech(tmp_path):
    voice = [{'role': 'user', 'text': 'Read that file'}, {'role': 'assistant', 'text': 'I will check'}]
    prompt = ('This is a user message arriving through the voice interface of this same Amplifier conversation. '
              'Private host instruction\n<voice_reference>\n' + json.dumps(voice) +
              '\n</voice_reference>\nCurrent spoken user request:\nRead that file')
    source = {'id': 'spoken', 'title': 'Voice', 'workspace': str(tmp_path), 'messages': [
        {'id': 'v1', **voice[0], 'via': 'call', 'voiceId': 'call'}, {'id': 'v2', **voice[1], 'via': 'call', 'voiceId': 'call'},
        {'id': 'response', 'role': 'assistant', 'text': 'The file says hello', 'via': 'call', 'source': 'amplifier'}]}
    SessionStore.for_app(tmp_path, tmp_path).save('spoken', [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': 'The file says hello'}], {})
    value = markdown(tmp_path, source, [])
    assert value.count('Read that file') == 1 and value.count('The file says hello') == 1
    assert 'Private host instruction' not in value and 'voice_reference' not in value
    native_only = markdown(tmp_path, {**source, 'messages': []}, [])
    assert 'Read that file' in native_only and '## User (voice)' in native_only


async def test_snapshot_endpoint_requires_authentication_and_survives_restart(tmp_path, authenticated_client):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app); service = app['service']
    await service.dispatch('session.create', {'title': 'HTTP export'})
    service._session()['messages'] = [{'id': 'one', 'role': 'assistant', 'text': '```text\nexact  \n```'}]
    result = await service.dispatch('session.export', {'id': service._session()['id'], 'format': 'markdown', 'destination': 'none'})
    response = await client.get(result['result']['url'])
    assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
    assert (await response.json())['content'] == exported(service, result)
    denied = await client.get(result['result']['url'], headers={'Authorization': ''})
    assert denied.status == 401
    assert (await client.get('/api/conversation/exports/missing')).status == 404
    from amplifier_web.resource_files import collect
    assert collect(service.db, service.state) == []
    service._save()
    from amplifier_web.service import AppService
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        assert exported(reopened, result) == exported(service, result)
        assert reopened.state['conversationExports'][result['result']['snapshotId']]['statePath'] == result['result']['statePath']
    finally:
        await reopened.close()


def test_recovered_references_keep_native_anchor_and_repeated_occurrences(tmp_path):
    reference = [{'role': 'user', 'text': 'yes', 'via': 'call'}, {'role': 'assistant', 'text': 'Continue?', 'via': 'call'},
                 {'role': 'user', 'text': 'yes', 'via': 'call'}]
    rows = [{'role': 'user', 'content': 'Before reference'},
            {'role': 'user', 'content': 'Host instructions\n' + json.dumps(reference), 'metadata': {'amplifier_visible_reference': True}},
            {'role': 'assistant', 'content': 'After reference'}]
    source = {'id': 'recovered', 'title': 'Recovered', 'workspace': str(tmp_path), 'messages': []}
    SessionStore.for_app(tmp_path, tmp_path).save(source['id'], rows, {})
    value = markdown(tmp_path, source, [])
    assert value.count('\n\nyes\n') == 2
    assert value.index('Before reference') < value.index('\n\nyes\n') < value.index('After reference')
    assert 'original conversation positions are unavailable' in value and 'Host instructions' not in value
    # The current UI carries both occurrences in their exact known position.
    source['messages'] = [display_message(rows[0], 0, source),
                          *[{'id': str(i), **row, 'voiceId': 'call'} for i, row in enumerate(reference)],
                          display_message(rows[2], 2, source)]
    value = markdown(tmp_path, source, [])
    assert value.count('\n\nyes\n') == 2
    assert 'recovered reference' not in value


def test_overlapping_native_voice_windows_preserve_repeated_yes_turns(tmp_path):
    first = [{'role': 'user', 'text': 'yes'}, {'role': 'assistant', 'text': 'Again?'}, {'role': 'user', 'text': 'yes'}]
    second = first[1:] + [{'role': 'assistant', 'text': 'Once more?'}, {'role': 'user', 'text': 'yes'}]
    def prompt(items):
        return ('This is a user message arriving through the voice interface of this same Amplifier conversation. '
                'Host instructions\n<voice_reference>\n' + json.dumps(items) + '\n</voice_reference>\nCurrent spoken user request:\nyes')
    rows = [{'role': 'user', 'content': prompt(first)}, {'role': 'assistant', 'content': 'First result'},
            {'role': 'user', 'content': prompt(second)}, {'role': 'assistant', 'content': 'Second result'}]
    source = {'id': 'repeated-voice', 'workspace': str(tmp_path), 'messages': []}
    SessionStore.for_app(tmp_path, tmp_path).save(source['id'], rows, {})
    value = markdown(tmp_path, source, [])
    assert value.count('\n\nyes\n') == 3
    assert value.index('Again?') < value.index('First result') < value.index('Once more?') < value.index('Second result')


def test_ui_owned_voice_or_imported_history_does_not_require_a_workspace(tmp_path):
    source = {'id': 'voice-only', 'messages': [{'id': 'voice', 'role': 'user', 'text': 'Keep this exchange', 'via': 'call', 'voiceId': 'call'}]}
    value = markdown(tmp_path, source, [])
    assert '## User (voice)\n\nKeep this exchange' in value
    assert list(tmp_path.iterdir()) == []


async def test_changed_during_read_is_rejected_without_publishing_snapshot(tmp_path, app_factory, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.conversation_export import SessionHistoryStore
    root = native_session(tmp_path / 'cli', 'changing-export')
    app = app_factory(); await app.history.refresh()
    source = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'changing-export')
    original = SessionHistoryStore.load
    def changed(self, **kwargs):
        result = original(self, **kwargs)
        result.diagnostics.append(SimpleNamespace(code='changed_during_read', source='transcript'))
        return result
    monkeypatch.setattr(SessionHistoryStore, 'load', changed)
    before = files_snapshot(root)
    with pytest.raises(AppError, match='changed during export'):
        await app.dispatch('session.export', {'id': source['id'], 'format': 'markdown'})
    assert files_snapshot(root) == before and not app.state.get('conversationExports')


async def test_slow_history_read_does_not_block_navigation(app_factory, monkeypatch):
    import asyncio
    import threading
    from amplifier_web import conversation_export
    app = app_factory(); await app.dispatch('session.create', {})
    entered, release = threading.Event(), threading.Event()
    original = conversation_export.markdown
    def paused(*args):
        entered.set()
        assert release.wait(3), 'Test did not release export reader'
        return original(*args)
    monkeypatch.setattr(conversation_export, 'markdown', paused)
    exporting = asyncio.create_task(app.dispatch('session.export', {'id': app._session()['id'], 'format': 'markdown', 'destination': 'none'}))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        result = await asyncio.wait_for(app.dispatch('view.update', {'patch': {'panel': 'settings'}}), timeout=.2)
        assert result['accepted'] and not exporting.done()
    finally:
        release.set()
        await exporting
