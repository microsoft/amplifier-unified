"""Backfill names unnamed chats with one provider call each, never a worker."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from test_service import Runtime

from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import directory_for, read
from amplifier_web.naming_backfill import NamingBackfill
from amplifier_web.service import AppError, AppService


def reply(name='Incident summary', action='set'):
    return json.dumps({'action': action, 'name': name if action == 'set' else None,
                       'description': 'Summarizes the outage.' if action == 'set' else None})


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    service.calls = []

    async def complete(session, prompt):
        service.calls.append((session['id'], prompt))
        return service.replies.get(session['id'], reply())

    service.replies = {}
    service.naming_backfill = NamingBackfill(service, complete=complete)
    yield service
    await service.close()


async def chat(app, text='Summarize the incident report', **metadata):
    await app.dispatch('session.create', {})
    session = app._session()
    rows = [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': 'Done.'}] if text else [
        {'role': 'assistant', 'content': 'Hello'}]
    SessionStore.for_app(app.data_dir, app.data_dir).save(session['id'], rows, {})
    if metadata:
        from amplifier_foundation.session.metadata import SessionMetadataStore

        from amplifier_web.naming import refresh
        SessionMetadataStore(directory_for(app.data_dir, session)).set_name(metadata['name'], source=metadata['name_source'])
        refresh(app.data_dir, session)
    return session


async def backfill(app, args=None):
    receipt = await app.dispatch('session.naming.backfill', args or {})
    await asyncio.gather(*app.tasks)
    return receipt, app.state['namingBackfill']


async def test_backfill_names_fallback_chats_without_starting_a_worker(app):
    session = await chat(app)
    receipt, result = await backfill(app)
    assert receipt['result']['queued'] == 1
    assert result['status'] == 'done'
    assert result['named'] == [session['id']]
    assert session['title'] == 'Incident summary'
    assert session['naming'] == {'status': 'ready'}
    metadata = read(directory_for(app.data_dir, session))
    assert metadata['name'] == 'Incident summary' and metadata['name_source'] == 'generated'
    assert metadata['description'] == 'Summarizes the outage.'
    assert app.runtime.started == [] and app.runtime.sent == []
    [(_, prompt)] = app.calls
    assert 'Summarize the incident report' in prompt and '"action"' in prompt


async def test_defer_keeps_the_fallback_title(app):
    session = await chat(app)
    title = session['title']
    app.replies[session['id']] = reply(action='defer')
    _, result = await backfill(app)
    assert result['skipped'] == [{'id': session['id'], 'reason': 'The naming model deferred; the current title was kept.'}]
    assert session['title'] == title
    assert session['naming'] == {'status': 'deferred'}
    assert read(directory_for(app.data_dir, session)).get('name_source') == 'fallback'


async def test_manual_generated_auto_off_and_empty_chats_are_skipped(app):
    manual = await chat(app, name='Mine', name_source='manual')
    generated = await chat(app, name='Already named', name_source='generated')
    auto_off = await chat(app)
    await app.dispatch('session.naming', {'id': auto_off['id'], 'automatic': False})
    empty = await chat(app, text=None)
    eligible = await chat(app)
    _, result = await backfill(app)
    assert result['named'] == [eligible['id']]
    reasons = {row['id']: row['reason'] for row in result['skipped']}
    assert set(reasons) == {manual['id'], generated['id'], auto_off['id'], empty['id']}
    assert 'no user message' in reasons[empty['id']].lower()
    assert [call[0] for call in app.calls] == [eligible['id']]
    assert manual['title'] != 'Incident summary'


async def test_failures_are_reported_and_other_chats_continue(app):
    first, second = await chat(app), await chat(app)

    async def complete(session, prompt):
        if session['id'] == first['id']:
            raise ValueError('Provider rejected the model')
        return reply('Second chat')

    app.naming_backfill.complete = complete
    _, result = await backfill(app)
    assert result['named'] == [second['id']]
    assert result['failed'] == [{'id': first['id'], 'reason': 'Provider rejected the model'}]
    assert first['naming'] == {'status': 'error', 'error': 'Provider rejected the model'}


async def test_a_rename_during_the_call_wins(app):
    session = await chat(app)

    async def complete(target, prompt):
        await app.dispatch('session.rename', {'id': target['id'], 'title': 'User choice'})
        return reply('Late generated')

    app.naming_backfill.complete = complete
    _, result = await backfill(app)
    assert session['title'] == 'User choice'
    assert result['named'] == []
    assert result['skipped'][0]['id'] == session['id']


async def test_ids_limit_timeout_and_single_run(app):
    sessions = [await chat(app) for _ in range(3)]
    _, result = await backfill(app, {'ids': [sessions[0]['id'], sessions[2]['id']], 'limit': 1})
    assert len(result['named']) == 1 and result['named'][0] in {sessions[0]['id'], sessions[2]['id']}
    assert sessions[1]['title'] != 'Incident summary'

    release = asyncio.Event()

    async def slow(session, prompt):
        await release.wait()
        return reply()

    app.naming_backfill.complete = slow
    app.naming_backfill.timeout = 0.05
    await app.dispatch('session.naming.backfill', {'ids': [sessions[1]['id']]})
    with pytest.raises(AppError, match='already running'):
        await app.dispatch('session.naming.backfill', {})
    await asyncio.gather(*app.tasks)
    failed = app.state['namingBackfill']['failed']
    assert failed == [{'id': sessions[1]['id'], 'reason': 'Naming timed out; the current title was kept.'}]
    release.set()


async def test_backfill_action_is_a_chat_management_permission():
    from amplifier_web.service import ACTION_DEFINITIONS as ACTIONS
    from amplifier_web.shell_modules import COMMAND_CAPABILITIES
    assert COMMAND_CAPABILITIES['session.naming.backfill'] == 'chats.manage'
    assert set(ACTIONS['session.naming.backfill'][1]['properties']) == {'ids', 'limit'}


async def test_naming_completion_uses_one_bounded_non_streaming_call():
    from amplifier_web.provider_test import naming_completion
    seen = []

    class Provider:
        async def complete(self, request, **kwargs):
            seen.append((request, kwargs))
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='{"action": "defer"}')])

    assert await naming_completion(Provider(), {'default_model': 'm'}, None, 'prompt') == {'text': '{"action": "defer"}'}
    request, kwargs = seen[0]
    assert request.model == 'm' and request.max_output_tokens == 256
    assert request.metadata == {'stream': False} and kwargs == {'extended_thinking': False}

    class Failing:
        async def complete(self, request, **kwargs):
            raise RuntimeError('bad key sk-abcdefghijklmnopqrstuv')

    result = await naming_completion(Failing(), {}, 'm', 'prompt')
    assert 'sk-abc' not in result['error'] and '[REDACTED]' in result['error']
