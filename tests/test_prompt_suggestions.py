"""Offline utility-request regressions; runnable with unittest or pytest."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from amplifier_web.prompt_suggestions import PromptSuggestions, BROWSER
from amplifier_web.suggestion_provider import complete, parse
from amplifier_web.suggestion_settings import describe, configure
from amplifier_web.suggestion_source import public_rows, select, Unavailable, witness
from amplifier_web.transcript_windows import TranscriptWindows, _native_page


class Clients:
    def __init__(self):
        from contextvars import ContextVar
        self.current = ContextVar('test-suggestion-client', default='one')
        self.records = {key: {'kind': 'web', 'deviceOwner': 'owner', 'selectedSessionId': 'chat'}
                        for key in ('one', 'two')}

    def record(self):
        return self.records.get(self.current.get())


class Service:
    def __init__(self, root):
        self.data_dir = root
        self.db = sqlite3.connect(':memory:')
        self.lock = asyncio.Lock()
        self.closed = False
        self.clients = Clients()
        self.session = {'id': 'chat', 'status': 'idle', 'messages': [
            {'id': 'human', 'role': 'user', 'text': 'Plan the next step.'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'The plan is ready.'}]}
        self.history = SimpleNamespace(windows=TranscriptWindows(self))
        self.projections = SimpleNamespace(resolver=None)

    def _session(self, sid, hydrate=False):
        if sid != self.session['id'] or self.session.get('_deleting'):
            raise ValueError('missing')
        return self.session


class Requests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Service(Path(self.tmp.name))
        self.host = PromptSuggestions(self.app)
        self.browser_token = BROWSER.set({'principal': 'cookie-one', 'valid': lambda: True})
        self.config = {'enabled': True, 'provider': 'explicit-account', 'model': 'cheap-model',
                       'transcriptMode': 'last_n', 'humanTurns': 6}
        self.revision = 'config-one'
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.calls = []

        async def respond(record, config, rows):
            self.calls.append(copy.deepcopy(rows))
            return {'status': 'ready', 'suggestion': 'Please carry out the next step.',
                    'usage': None, 'cost': None}
        self.host.complete = respond

    async def asyncTearDown(self):
        await self.host.close()
        await self.app.history.windows.close()
        self.app.db.close()
        BROWSER.reset(self.browser_token)
        self.tmp.cleanup()

    async def request(self, request_id='request', **values):
        return await self.host.dispatch('session.suggestion.request', {
            'sessionId': 'chat', 'requestId': request_id,
            'expectedLastMessageId': 'assistant', **values}, 'ui')

    async def status(self, request_id='request'):
        return await self.host.dispatch('session.suggestion.status',
                                       {'sessionId': 'chat', 'requestId': request_id}, 'ui')

    async def settle(self):
        await asyncio.gather(*tuple(self.host.jobs))

    async def test_request_is_private_and_never_changes_history(self):
        original = copy.deepcopy(self.app.session)
        pending = await self.request()
        self.assertEqual(pending['status'], 'pending')
        self.assertTrue(pending['sourceKey'])
        self.assertEqual(pending['configRevision'], self.revision)
        await self.settle()
        ready = await self.status()
        self.assertEqual(ready['status'], 'ready')
        self.assertIsNone(ready['usage'])
        self.assertIsNone(ready['cost'])
        self.assertEqual(self.app.session, original)
        self.app.clients.current.set('two')
        other = await self.status()
        self.assertEqual(other['status'], 'expired')
        self.assertIsNone(other['suggestion'])
        self.assertNotIn('Please carry', repr(list(self.app.db.execute('SELECT * FROM suggestion_requests'))))

    async def test_same_request_and_new_request_same_source_do_not_replay(self):
        await self.request()
        await self.request()
        await self.settle()
        await self.request()
        self.assertEqual((await self.request('other'))['status'], 'duplicate_source')
        self.assertEqual(len(self.calls), 1)
        await self.host.close()
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.assertEqual((await self.request())['status'], 'expired')
        self.assertEqual((await self.request('third'))['status'], 'duplicate_source')

    async def test_new_browser_document_and_disposable_fence_do_not_repeat_paid_source(self):
        await self.request()
        await self.settle()
        self.app.history.windows.invalidate({'chat'})
        self.app.clients.current.set('two')
        self.assertEqual((await self.request('new-document'))['status'], 'duplicate_source')
        self.assertEqual(len(self.calls), 1)

    async def test_conflicting_request_id_is_rejected(self):
        await self.request()
        with self.assertRaisesRegex(Exception, 'different inputs'):
            await self.request(expectedLastMessageId='other')

    async def test_status_does_not_read_or_hydrate_transcript(self):
        await self.request()
        await self.settle()
        self.app.history.windows.read = AsyncMock(side_effect=AssertionError('read'))
        for _ in range(8):
            self.assertEqual((await self.status())['status'], 'ready')
        self.app.history.windows.read.assert_not_called()

    async def test_same_id_edit_and_append_fence_ready_candidate(self):
        await self.request()
        await self.settle()
        self.app.session['messages'][-1]['text'] = 'Edited with same ID.'
        self.assertEqual((await self.status())['status'], 'stale')
        self.assertIsNone((await self.status())['suggestion'])
        self.app.session['messages'].append({'id': 'new', 'role': 'assistant', 'text': 'New reply'})
        self.assertEqual((await self.request('new', expectedLastMessageId='new'))['status'], 'pending')
        await self.settle()
        self.assertEqual(len(self.calls), 2)

    async def test_change_while_provider_running_discards_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(*args):
            entered.set()
            await release.wait()
            return {'status': 'ready', 'suggestion': 'Late response', 'usage': None, 'cost': None}
        self.host.complete = respond
        await self.request()
        await entered.wait()
        self.app.session['messages'][-1]['text'] = 'Changed'
        release.set()
        await self.settle()
        self.assertEqual((await self.status())['status'], 'stale')

    async def test_cancel_retains_occupancy_until_physical_settlement(self):
        entered, settling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def respond(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                settling.set()
                await release.wait()
                raise
        self.host.complete = respond
        await self.request()
        await entered.wait()
        await self.host.dispatch('session.suggestion.cancel',
                                 {'sessionId': 'chat', 'requestId': 'request'}, 'ui')
        await settling.wait()
        self.app.session['messages'][-1]['text'] = 'Different'
        self.assertEqual((await self.request('second'))['status'], 'busy')
        release.set()
        await self.settle()
        self.assertEqual((await self.status())['status'], 'cancelled')

    async def test_archive_delete_config_logout_and_navigation_fence(self):
        for change in ('archive', 'delete', 'config', 'logout', 'navigation', 'principal'):
            with self.subTest(change=change):
                self.app.session.pop('archived', None)
                self.app.session.pop('_deleting', None)
                self.app.session['messages'][-1]['text'] = change
                self.app.clients.records['one'].update(deviceOwner='owner', selectedSessionId='chat')
                self.revision = change
                await self.request(change)
                await self.settle()
                if change == 'archive':
                    self.app.session['archived'] = True
                elif change == 'delete':
                    self.app.session['_deleting'] = True
                elif change == 'config':
                    self.revision = 'new-config'
                elif change == 'logout':
                    self.host._epoch = lambda: 'changed'
                elif change == 'navigation':
                    self.app.clients.records['one']['selectedSessionId'] = 'elsewhere'
                else:
                    self.app.clients.records['one']['deviceOwner'] = 'other-owner'
                if change in {'navigation', 'principal'}:
                    if change == 'navigation':
                        self.assertFalse(self.host.current(next(reversed(self.host.records.values()))))
                    else:
                        self.assertEqual((await self.status(change))['status'], 'expired')
                else:
                    self.assertEqual((await self.status(change))['status'], 'stale')

    async def test_disabled_busy_and_missing_selection_no_calls(self):
        self.config['enabled'] = False
        self.assertEqual((await self.request('off'))['status'], 'disabled')
        self.config['enabled'] = True
        self.app.session['status'] = 'working'
        self.assertEqual((await self.request('busy'))['status'], 'busy')
        self.app.session['status'] = 'idle'
        self.host.settings = lambda: (self.config, None, self.revision)
        self.assertEqual((await self.request('missing'))['status'], 'missing_selection')
        self.assertEqual(self.calls, [])

    async def test_internal_worker_scheduled_and_update_pause_are_unavailable(self):
        for kind in ('worker', 'internal'):
            self.app.session['sessionKind'] = kind
            self.assertEqual((await self.request(kind))['status'], 'unsupported')
        self.app.session['sessionKind'] = 'root'
        self.app.session['scheduledRunId'] = 'run'
        self.assertEqual((await self.request('scheduled'))['status'], 'unsupported')
        self.app.session.pop('scheduledRunId')
        self.app._state = {'updates': {'phase': 'activating'}}
        self.assertEqual((await self.request('paused'))['status'], 'unsupported')
        self.assertEqual(self.calls, [])

    async def test_ttl_and_failures_never_retry(self):
        async def fail(*args):
            self.calls.append('attempt')
            raise ValueError('secret transcript or credential')
        self.host.complete = fail
        await self.request()
        await self.settle()
        self.assertEqual((await self.status())['status'], 'failed')
        self.assertNotIn('secret', repr(await self.status()))
        record = next(iter(self.host.records.values()))
        record['created'] -= 61
        self.assertEqual((await self.status())['status'], 'expired')
        self.assertEqual(len(self.calls), 1)


class Source(unittest.TestCase):
    def test_sanitizes_closed_and_unclosed_host_envelopes_payloads_and_nonhuman_inputs(self):
        rows = public_rows([
            {'id': '1', 'role': 'user', 'text': '<system-reminders>PRIVATE</system-reminders> Human'},
            {'id': '2', 'role': 'user', 'text': 'Visible <context_file path="x">FILE'},
            {'id': '3', 'role': 'user', 'text': 'data:image/png;base64,PRIVATE', 'inputOrigin': 'approval'},
            {'id': '4', 'role': 'user', 'text': 'Scheduled', 'scheduledRunId': 'run'},
        ])
        self.assertEqual(rows[0]['text'], 'Human')
        self.assertEqual(rows[1]['text'], 'Visible')
        self.assertNotIn('PRIVATE', repr(rows))
        self.assertTrue(rows[2]['agentId'])
        self.assertTrue(rows[3]['agentId'])

    def test_private_roles_delivery_recovery_and_labels(self):
        rows = [
            {'id': 'human', 'role': 'user', 'text': 'Public human'},
            {'id': 'agent', 'role': 'assistant', 'text': 'Public agent', 'agentId': 'other', 'agentLabel': 'Agent B'},
            {'id': 'system', 'role': 'system', 'text': 'SYSTEM'},
            {'id': 'private', 'role': 'assistant', 'text': 'PRIVATE', 'visibility': 'internal'},
            {'id': 'recovery', 'role': 'user', 'text': 'RECOVERY', 'observation': {'id': 'service'}},
            {'id': 'pending', 'role': 'user', 'text': 'PENDING', 'delivery': {'status': 'sending'}},
            {'id': 'failed', 'role': 'user', 'text': 'FAILED', 'inputStatus': {'phase': 'failed'}},
        ]
        result = public_rows(rows)
        self.assertEqual([r['id'] for r in result], ['human', 'agent'])
        self.assertEqual(result[-1]['agentLabel'], 'Agent B')

    def test_human_turn_window_counts_humans_not_agent_user_roles(self):
        rows = [{'id': str(i), 'role': 'user', 'text': str(i)} for i in range(9)]
        rows.insert(8, {'id': 'agent', 'role': 'user', 'agentId': 'bot', 'text': 'agent input'})
        selected = select(rows, {'transcriptMode': 'last_n', 'humanTurns': 2}, True)
        self.assertEqual([r['id'] for r in selected], ['7', 'agent', '8'])
        self.assertEqual(select(rows, {'transcriptMode': 'last_n', 'humanTurns': 20}, False), rows)
        with self.assertRaises(Unavailable):
            select(rows, {'transcriptMode': 'full'}, True)
        with self.assertRaises(Unavailable):
            select(rows, {'transcriptMode': 'last_n', 'humanTurns': 20}, True)

    def test_independent_byte_and_row_limits(self):
        with self.assertRaises(Unavailable):
            public_rows([{'id': 'x', 'role': 'assistant', 'text': 'x' * 25000}])
        with self.assertRaises(Unavailable):
            public_rows([{'id': str(i), 'role': 'user', 'text': 'x'} for i in range(101)])
        with self.assertRaises(Unavailable):
            public_rows([{'id': 'x', 'role': 'assistant', 'text': '😀' * 7000}])

    def test_native_canonical_private_block_filter_preserves_display_id(self):
        with tempfile.TemporaryDirectory() as root:
            from amplifier_web.transcript_window import TranscriptWindowReader
            path = Path(root)
            # Patch only directory lookup: the real maintained window reader
            # scans real bytes and the real canonical block filter projects.
            (path / 'transcript.jsonl').write_text(json.dumps({
                'role': 'assistant', 'content': [
                    {'type': 'text', 'text': 'INTERNAL', 'visibility': 'internal'},
                    {'type': 'text', 'text': 'DEVELOPER', 'visibility': 'developer'},
                    {'type': 'thinking', 'thinking': 'REASONING'},
                    {'type': 'tool_result', 'content': 'TOOL'},
                    {'type': 'text', 'text': 'Public reply'}]}) + '\n')
            source = {'id': 'chat', 'nativeProject': root, 'nativeIdentity': 'chat'}
            with patch('amplifier_web.automatic_history.directory', return_value=path):
                ordinary = _native_page(TranscriptWindowReader(), source, None, 20)
                safe = _native_page(TranscriptWindowReader(), source, None, 20, public_only=True)
            self.assertEqual(safe['items'][0]['text'], 'Public reply')
            self.assertEqual(safe['items'][0]['id'], ordinary['items'][0]['id'])

    def test_resident_witness_visits_bounded_suffix_not_growing_prefix(self):
        class Tail(list):
            def __iter__(self):
                raise AssertionError('whole-history iteration')
            def __getitem__(self, key):
                if isinstance(key, slice):
                    assert key.start >= len(self) - 100
                return super().__getitem__(key)
        with tempfile.TemporaryDirectory() as root:
            app = Service(Path(root))
            app.session['messages'] = Tail(
                [{'id': str(i), 'role': 'user', 'text': 'x'} for i in range(36000)])
            first = witness(app, 'chat')
            app.session['messages'][-1]['text'] = 'changed same id'
            self.assertNotEqual(first, witness(app, 'chat'))


class Provider(unittest.IsolatedAsyncioTestCase):
    async def test_one_tool_free_explicit_bounded_completion_and_reported_usage(self):
        provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(type='text', text='{"suggestion":"Please continue."}')],
            usage={'input_tokens': 10, 'output_tokens': 6})))
        result = await complete(provider, {}, 'explicit', [{'role': 'user', 'text': 'test'}], {})
        self.assertEqual(result['suggestion'], 'Please continue.')
        self.assertEqual(result['usage']['input_tokens'], 10)
        self.assertIsNone(result['cost'])
        provider.complete.assert_awaited_once()
        request = provider.complete.call_args.args[0]
        self.assertEqual(request.model, 'explicit')
        self.assertEqual(request.max_output_tokens, 512)
        self.assertFalse(request.tools)
        self.assertEqual(request.timeout, 15)
        self.assertFalse(provider.complete.call_args.kwargs['extended_thinking'])

    async def test_plan_route_refuses_without_call(self):
        provider = SimpleNamespace(complete=AsyncMock())
        self.assertEqual((await complete(provider, {'auth_mode': 'chatgpt_plan'}, 'x', [], {}))['status'], 'unsupported')
        provider.complete.assert_not_called()

    async def test_tool_response_and_malformed_never_repair(self):
        for block in (SimpleNamespace(type='tool_call'), SimpleNamespace(type='text', text='not JSON')):
            provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=[block])))
            with self.assertRaises(ValueError):
                await complete(provider, {}, 'explicit', [], {})
            provider.complete.assert_awaited_once()

    async def test_no_per_request_preparation_even_for_missing_module(self):
        from amplifier_web.provider_probe import query
        with patch('amplifier_web.provider_probe.provider_class', side_effect=ImportError), \
                patch('amplifier_foundation.Bundle.prepare', new_callable=AsyncMock) as prepare:
            result = await query({'action': 'suggestion.complete', 'module': 'missing',
                                  'source': None, 'fallbackSource': 'git+https://example.invalid/missing'})
            self.assertEqual(result['suggestion']['status'], 'unsupported')
            prepare.assert_not_called()


class Parsing(unittest.TestCase):
    def test_valid_null_and_unicode_boundary(self):
        self.assertIsNone(parse('{"suggestion":null}'))
        self.assertEqual(parse(json.dumps({'suggestion': '😀' * 240})), '😀' * 240)

    def test_strict_schema_and_single_line(self):
        for value in ('{}', '[]', '{"suggestion":1}', '{"suggestion":" hi"}',
                      '{"suggestion":"hi\\nthere"}', '{"suggestion":"\\u2028"}',
                      '{"suggestion":"ok","other":true}', '{"suggestion":"a","suggestion":"b"}',
                      '```json\n{"suggestion":"hi"}\n```',
                      json.dumps({'suggestion': 'x' * 241})):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                parse(value)


class Integration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AMPLIFIER_HOME': str(self.root / 'shared'),
                                          'AMPLIFIER_WEB_HOME': str(self.root / 'app')})
        self.env.start()
        from amplifier_web.service import AppService
        self.runtime = SimpleNamespace(close=AsyncMock(), start=AsyncMock(), send=AsyncMock())
        self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)

    async def asyncTearDown(self):
        await self.app.close()
        self.env.stop()
        self.tmp.cleanup()

    async def test_settings_authority_projection_and_no_model_runtime_call(self):
        current = self.app.state.get('configurationRevision', 0)
        before = await self.app.dispatch('providers.suggestionModels', {}, include_state=False)
        self.assertFalse(before['result']['suggestionModels']['enabled'])
        result = await self.app.dispatch('providers.configureSuggestions', {
            'enabled': False, 'transcriptMode': 'last_n', 'humanTurns': 6,
            'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
        self.assertEqual(result['result']['configurationRevision'], current + 1)
        self.assertEqual(self.app.browser_state()['configurationRevision'], current + 1)
        after = await self.app.dispatch('providers.suggestionModels', {}, include_state=False)
        self.assertEqual(result['result']['suggestionModels'], after['result']['suggestionModels'])
        self.assertNotEqual(before['result']['suggestionModels']['revision'],
                            after['result']['suggestionModels']['revision'])
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()
        with self.assertRaises(Exception):
            await self.app.dispatch('providers.configureSuggestions', {
                'enabled': False, 'transcriptMode': 'last_n', 'humanTurns': 6, 'scope': 'global'},
                origin='agent', caller_session_id=None, include_state=False)

    async def test_service_request_is_ui_private_and_bypasses_shared_action_receipt(self):
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        session.update(status='idle', messages=[
            {'id': 'human', 'role': 'user', 'text': 'What next?'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Review the plan.'}])
        host = self.app.prompt_suggestions
        host.settings = lambda: ({'enabled': True, 'provider': 'saved', 'model': 'model',
                                  'humanTurns': 6, 'transcriptMode': 'last_n'}, {'id': 'saved'}, 'revision')
        host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'PRIVATE CANDIDATE',
                                               'usage': None, 'cost': None})
        self.app.clients.attach('browser', kind='web', device='owner')
        self.app.clients.records['browser']['selectedSessionId'] = session['id']
        args = {'sessionId': session['id'], 'requestId': 'private-request',
                'expectedLastMessageId': 'assistant'}
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                result = await self.app.dispatch('session.suggestion.request', args,
                                                command_id='private-command', include_state=False)
                self.assertEqual(result['result']['status'], 'pending')
                await asyncio.gather(*tuple(host.jobs))
                result = await self.app.dispatch('session.suggestion.status',
                    {'sessionId': session['id'], 'requestId': 'private-request'}, include_state=False)
                self.assertEqual(result['result']['suggestion'], 'PRIVATE CANDIDATE')
                self.assertNotIn('PRIVATE CANDIDATE', repr(self.app.browser_state()))
                self.assertIsNone(self.app.db.execute('SELECT receipt FROM commands WHERE id=?',
                                                     ('private-command',)).fetchone())
        finally:
            BROWSER.reset(token)
        with self.assertRaises(Exception):
            await self.app.dispatch('session.suggestion.status',
                {'sessionId': session['id'], 'requestId': 'private-request'}, origin='agent',
                caller_session_id=session['id'], include_state=False)

    async def test_global_selection_reuses_connection_and_credential_rotation_changes_revision(self):
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai', 'config': {
                'api_key': '${SUGGESTION_FIXTURE_KEY}', 'default_model': 'chat-model'}}]}))
        with patch.dict(os.environ, {'SUGGESTION_FIXTURE_KEY': 'synthetic-one'}):
            result = configure(manager, {'enabled': True, 'provider': 'saved', 'model': 'cheap',
                                         'transcriptMode': 'last_n', 'humanTurns': 6}, self.root, 'global')
            one = result['suggestionModels']['revision']
            self.assertEqual(manager.config(self.root).providers[0]['config']['default_model'], 'chat-model')
            self.assertNotIn('synthetic-one', repr(result))
            with patch.dict(os.environ, {'SUGGESTION_FIXTURE_KEY': 'synthetic-two'}):
                self.assertNotEqual(one, describe(manager, self.root)['revision'])

    async def test_cookie_endpoint_rejects_bearer_spoof_and_partitions_principals(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from amplifier_web.auth import auth_required, new_session, SESSION_COOKIE, Sessions
        from amplifier_web.live_clients import client_context
        from amplifier_web.errors import AppError
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        session.update(status='idle', messages=[
            {'id': 'human', 'role': 'user', 'text': 'Human'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Reply'}])
        host = self.app.prompt_suggestions
        host.settings = lambda: ({'enabled': True, 'provider': 'saved', 'model': 'model',
                                  'humanTurns': 6, 'transcriptMode': 'last_n'}, {'id': 'saved'}, 'revision')
        host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'SECRET CANDIDATE',
                                               'usage': None, 'cost': None})
        for identity in ('one', 'two'):
            self.app.clients.attach(identity, kind='web')
            self.app.clients.records[identity]['selectedSessionId'] = session['id']
        @web.middleware
        async def errors(request, handler):
            try:
                return await handler(request)
            except AppError as exc:
                return web.json_response({'error': str(exc)}, status=403)
        http = web.Application(middlewares=[errors, auth_required, client_context])
        http.update(service=self.app, session_secret='synthetic-secret', control_token='control',
                    server_config={'session_ttl_seconds': 600}, sessions=Sessions(), allowed_origins=set())
        async def endpoint(request):
            payload = await request.json()
            result = await self.app.dispatch(payload['action'], payload['args'], include_state=False)
            return web.json_response(result)
        http.router.add_post('/api/actions', endpoint)
        cookie_one, cookie_two = new_session('synthetic-secret'), new_session('synthetic-secret')
        args = {'sessionId': session['id'], 'requestId': 'request', 'expectedLastMessageId': 'assistant'}
        async with TestClient(TestServer(http)) as client:
            def headers(cookie, identity='one', bearer=False):
                return {'X-Amplifier-Client': identity,
                        **({'Authorization': 'Bearer control'} if bearer else {}),
                        'Cookie': SESSION_COOKIE + '=' + cookie}
            response = await client.post('/api/actions', json={'action': 'session.suggestion.request', 'args': args},
                                         headers=headers(cookie_one, bearer=True))
            self.assertEqual(response.status, 403)  # Even valid cookie cannot bless bearer route.
            response = await client.post('/api/actions', json={'action': 'session.suggestion.request', 'args': args},
                                         headers=headers(cookie_one))
            self.assertEqual((await response.json())['result']['status'], 'pending')
            await asyncio.gather(*tuple(host.jobs))
            status = {'action': 'session.suggestion.status', 'args': {
                'sessionId': session['id'], 'requestId': 'request'}}
            for cookie, identity in ((cookie_two, 'one'), (cookie_one, 'two')):
                response = await client.post('/api/actions', json=status, headers=headers(cookie, identity))
                body = await response.json()
                self.assertEqual(body['result']['status'], 'expired')
                self.assertNotIn('SECRET', repr(body))
            response = await client.post('/api/actions', json=status, headers=headers(cookie_one))
            self.assertEqual((await response.json())['result']['suggestion'], 'SECRET CANDIDATE')
            response = await client.post('/api/actions', json=status, headers=headers(cookie_one, bearer=True))
            self.assertEqual(response.status, 403)
            self.assertNotIn('SECRET', await response.text())

    async def test_owned_probe_handshake_revokes_after_subprocess_creation(self):
        """Real process/pipes/cleanup; synthetic provider and zero network."""
        from amplifier_web.setup import SetupManager
        # The script represents a prepared provider waiting at final admission.
        script = (
            "import json,sys\n"
            "json.loads(sys.stdin.readline())\n"
            "print('{\"suggestionReady\":true}', flush=True)\n"
            "grant=json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'suggestion':{'status':'ready' if grant['admitted'] else 'stale',"
            "'suggestion':'candidate' if grant['admitted'] else None,'usage':None,'cost':None}}),flush=True)\n"
        )
        manager = SetupManager(self.root / 'app', global_only=True, probe_command=[sys.executable, '-c', script])
        manager.probe_snapshot = lambda *args: {'module': 'provider-fixture', 'raw': {}, 'config': {},
            'env': dict(os.environ), 'credentialGeneration': 'fixture', 'source': None, 'registryHome': str(self.root)}
        count = 0
        def admit():
            nonlocal count
            count += 1
            return count == 1
        try:
            result = await manager.probe('suggestion.complete', {
                'id': 'fixture', 'model': 'explicit', 'transcript': [], '_admit': admit}, str(self.root))
            self.assertEqual(result['status'], 'stale')
            self.assertIsNone(result['suggestion'])
            self.assertEqual(count, 2)
        finally:
            await manager.close()


if __name__ == '__main__':
    unittest.main()