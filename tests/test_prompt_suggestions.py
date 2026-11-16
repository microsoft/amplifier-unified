"""Offline utility-request regressions; runnable with unittest or pytest."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from amplifier_web.prompt_suggestions import PromptSuggestions, BROWSER
from amplifier_web.suggestion_provider import complete, parse, qualified_source_matches
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
            {'id': 'human', 'role': 'user', 'text': 'Plan the next step.', 'inputOrigin': 'ui'},
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

    async def terminal_request(self, request_id, **values):
        await self.request(request_id, **values)
        await self.settle()
        return await self.status(request_id)

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
        self.assertEqual((await self.terminal_request('other'))['status'], 'duplicate_source')
        self.assertEqual(len(self.calls), 1)
        await self.host.close()
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.assertEqual((await self.request())['status'], 'expired')
        self.assertEqual((await self.terminal_request('third'))['status'], 'duplicate_source')

    async def test_new_browser_document_and_disposable_fence_do_not_repeat_paid_source(self):
        await self.request()
        await self.settle()
        self.app.history.windows.invalidate({'chat'})
        self.app.clients.current.set('two')
        self.assertEqual((await self.terminal_request('new-document'))['status'], 'duplicate_source')
        self.assertEqual(len(self.calls), 1)

    async def test_historical_claims_do_not_impose_lifetime_quota_or_scan_before_admission(self):
        # Preserve a failed/uncertain attempt, then grow only unrelated claim
        # metadata beyond the former 4096-installation-lifetime hard stop.
        async def fail(*args):
            self.calls.append('uncertain-attempt')
            raise TimeoutError()
        self.host.complete = fail
        await self.request('uncertain')
        await self.settle()
        self.assertEqual((await self.status('uncertain'))['status'], 'timeout')
        original_claim = self.app.db.execute('SELECT * FROM suggestion_requests').fetchone()
        from amplifier_web.suggestion_settings import digest
        self.app.db.executemany('INSERT INTO suggestion_requests VALUES (?,?)',
            ((digest(['historical-request', i]), digest(['historical-source', i])) for i in range(5000)))
        self.app.db.commit()
        await self.host.close()
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.host.complete = AsyncMock(return_value={
            'status': 'ready', 'suggestion': 'A new useful next step.', 'usage': None, 'cost': None})
        statements = []
        self.app.db.set_trace_callback(statements.append)
        try:
            self.assertEqual((await self.request('uncertain'))['status'], 'expired')
            self.assertEqual((await self.terminal_request('same-source'))['status'], 'duplicate_source')
            self.app.session['messages'][-1]['text'] = 'A genuinely new completed source'
            self.assertEqual((await self.request('new-source'))['status'], 'pending')
            await self.settle()
            self.assertEqual((await self.status('new-source'))['status'], 'ready')
        finally:
            self.app.db.set_trace_callback(None)
        self.host.complete.assert_awaited_once()
        selects = [sql for sql in statements if sql.upper().startswith('SELECT')]
        self.assertTrue(selects)
        self.assertTrue(all('WHERE identity=' in sql or 'WHERE source=' in sql for sql in selects))
        self.assertFalse(any('count(' in sql.lower() for sql in statements))
        self.assertEqual(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                            (original_claim[0],)).fetchone(), original_claim)
        self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_requests').fetchone()[0], 5003)

    async def test_provider_refusal_reason_is_allowlisted_without_leaking_arbitrary_text(self):
        allowed = ('custom_source_not_prepared', 'runtime_not_prepared', 'qualified_source_mismatch')
        for index, reason in enumerate((*allowed, 'private transcript and credential')):
            self.app.session['messages'][-1]['text'] = 'Different source ' + str(index)
            self.host.complete = AsyncMock(return_value={
                'status': 'unsupported', 'suggestion': None, 'usage': None, 'cost': None, 'reason': reason})
            await self.request('reason-' + str(index))
            await self.settle()
            result = await self.status('reason-' + str(index))
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['reason'], reason if reason in allowed else None)
            self.assertNotIn('credential', repr(result))
            self.host.complete.assert_awaited_once()

    async def test_failed_reason_is_private_finite_and_unknown_sdk_payloads_are_discarded(self):
        for index, reason in enumerate(('transport_authentication', 'parse_invalid_output',
                                        'output_invalid_output', 'transport_https://private/key')):
            self.app.session['messages'][-1]['text'] = 'New failed source ' + str(index)
            self.host.complete = AsyncMock(return_value={
                'status': 'failed', 'suggestion': None, 'usage': None, 'cost': None, 'reason': reason,
                'diagnostic': {'body': 'SECRET candidate https://private/key'}, 'error': 'SECRET'})
            result = await self.terminal_request('failure-' + str(index))
            self.assertEqual(result['reason'], reason if index < 3 else None)
            self.assertNotIn('SECRET', repr(result))
            self.assertNotIn('diagnostic', result)
            self.assertNotIn('SECRET', repr(list(self.host.records.values())))
            self.app.clients.current.set('two')
            self.assertEqual((await self.status('failure-' + str(index)))['status'], 'expired')
            self.app.clients.current.set('one')
        self.assertNotIn('transport_authentication',
                         repr(list(self.app.db.execute('SELECT * FROM suggestion_requests'))))

    async def test_recreated_window_and_request_owners_with_different_access_order_do_not_replay(self):
        await self.request()
        await self.settle()
        await self.host.close()
        await self.app.history.windows.close()
        self.app.history.windows = TranscriptWindows(self.app)
        # Exercise both an unrelated first access and eviction of chat's fence.
        self.app.history.windows._fence('unrelated')
        self.app.history.windows._fence('chat')
        for i in range(70):
            self.app.history.windows._fence('other-' + str(i))
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.host.complete = AsyncMock(side_effect=AssertionError('replayed'))
        self.app.clients.current.set('two')
        self.assertEqual((await self.terminal_request('after-restart'))['status'], 'duplicate_source')
        self.host.complete.assert_not_called()

    async def test_expiry_is_absolute_admission_epoch_milliseconds_and_never_extended(self):
        before = time.time() * 1000
        pending = await self.request()
        after = time.time() * 1000
        self.assertGreaterEqual(pending['expiresAt'], before + 60000)
        self.assertLessEqual(pending['expiresAt'], after + 60000)
        await self.settle()
        self.assertEqual((await self.status())['expiresAt'], pending['expiresAt'])
        self.assertEqual((await self.request())['expiresAt'], pending['expiresAt'])
        record = next(iter(self.host.records.values()))
        self.host._expire(record)
        self.assertEqual((await self.status())['status'], 'expired')
        self.assertEqual((await self.status())['expiresAt'], pending['expiresAt'])

    async def test_external_and_shared_accepted_inputs_do_not_require_native_generation_status(self):
        for participation in ('exclusive', 'shared'):
            with self.subTest(participation=participation):
                self.app.session['externalAgent'] = {'participation': participation}
                self.app.session['messages'] = [
                    {'id': 'human', 'inputId': 'external-' + participation, 'role': 'user',
                     'text': 'Accepted external human ' + participation, 'inputOrigin': 'ui',
                     'delivery': {'status': 'accepted', 'scope': 'conversation'}, 'roomSequence': 1},
                    {'id': 'assistant', 'role': 'assistant', 'text': 'External response',
                     'agentId': 'agent-a', 'agentLabel': 'Agent A', 'roomSequence': 2}]
                with patch('amplifier_web.input_status.annotate', side_effect=AssertionError('native progress')):
                    self.assertEqual((await self.request(participation))['status'], 'pending')
                    await self.settle()
                    self.assertEqual((await self.status(participation))['status'], 'ready')
                sent = self.calls[-1]
                self.assertEqual(sent[0]['text'], 'Accepted external human ' + participation)
                self.assertEqual(sent[0]['inputActor']['kind'], 'human')
                self.assertEqual(sent[1]['agentId'], 'agent-a')
        self.assertEqual(len(self.calls), 2)

    async def test_unknown_and_agent_system_actor_inputs_never_gain_human_voice(self):
        self.app.session['messages'][0]['inputActor'] = {'kind': 'human', 'id': 'person'}
        self.app.session['messages'][1:1] = [
            {'id': 'agent', 'role': 'user', 'text': 'Agent says go', 'inputOrigin': 'ui',
             'inputActor': {'kind': 'agent', 'id': 'agent-b'}},
            {'id': 'system', 'role': 'user', 'text': 'System input', 'inputOrigin': 'ui',
             'inputActor': {'kind': 'system', 'id': 'host'}},
            {'id': 'legacy', 'role': 'user', 'text': 'Unknown legacy input'}]
        self.config['humanTurns'] = 1
        await self.request()
        await self.settle()
        self.assertEqual((await self.status())['status'], 'ready')
        self.assertEqual([row['inputActor']['kind'] for row in self.calls[0]],
                         ['human', 'agent', 'system', 'unknown', 'agent'])
        self.assertEqual([row['text'] for row in self.calls[0]][1:4],
                         ['Agent says go', 'System input', 'Unknown legacy input'])

    async def test_native_peer_voice_and_legacy_provenance_exact_outbound_projection(self):
        from amplifier_web.voice_input_projection import PREFIX, START, END
        root = Path(self.tmp.name)
        voice = PREFIX + START + json.dumps([{'role': 'user', 'text': 'PRIVATE REFERENCE HISTORY'}]) + END + 'Current spoken request'
        def native(text, identity, source='user', **extra):
            return {'role': 'user', 'content': text, 'metadata': {'amplifier_input': {
                'version': 1, 'kind': 'user', 'id': identity, 'source': source}}, **extra}
        raw = [native('Known human', 'human-input'),
               native('Peer input', 'peer-input'),  # Source=user is not sufficient when exact peer receipt exists.
               native('System actor', 'system-input', inputActor={'kind': 'system', 'id': 'host'}),
               {'role': 'user', 'content': 'Legacy unknown'},
               native(voice, 'voice:call:request'),
               {'role': 'assistant', 'content': 'Completed reply'}]
        (root / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in raw))
        self.app.db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, receipt TEXT)')
        self.app.db.execute('INSERT INTO commands VALUES (?,?)', ('peer-input', json.dumps({
            'commandAction': 'coordination.send', 'origin': 'agent'})))
        self.app.session.update(historyManaged=True, nativeProject=str(root), nativeIdentity='chat',
            messageAnnotations={'input-status:' + identity: {'inputStatus': {
                'version': 1, 'inputId': identity, 'phase': 'done', 'basis': 'generation_finished'}}
                for identity in ('human-input', 'peer-input', 'system-input', 'voice:call:request')})
        self.config['transcriptMode'] = 'full'
        with patch('amplifier_web.automatic_history.directory', return_value=root):
            ordinary = await self.app.history.windows.read('chat', limit=100)
            last = ordinary['items'][-1]['id']
            await self.request(expectedLastMessageId=last)
            await self.settle()
            self.assertEqual((await self.status())['status'], 'ready')
        outbound = self.calls[0]
        self.assertEqual([row['text'] for row in outbound],
                         ['Known human', 'Peer input', 'System actor', 'Legacy unknown',
                          'Current spoken request', 'Completed reply'])
        self.assertEqual([row['inputActor']['kind'] for row in outbound],
                         ['human', 'agent', 'system', 'unknown', 'human', 'agent'])
        self.assertNotIn('PRIVATE REFERENCE', repr(outbound))
        await self.host.close()
        await self.app.history.windows.close()
        self.app.history.windows = TranscriptWindows(self.app)
        for i in range(75):
            self.app.history.windows._fence('other-native-' + str(i))
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.host.complete = AsyncMock(side_effect=AssertionError('native source replay'))
        with patch('amplifier_web.automatic_history.directory', return_value=root):
            self.assertEqual((await self.terminal_request('native-restart', expectedLastMessageId=last))['status'],
                             'duplicate_source')
        self.host.complete.assert_not_called()

    async def test_native_utility_page_does_not_replace_ordinary_cached_page(self):
        root = Path(self.tmp.name)
        (root / 'transcript.jsonl').write_text(json.dumps({
            'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Internal existing display', 'visibility': 'internal'},
                {'type': 'text', 'text': 'Public reply'}]}) + '\n')
        self.app.session.update(historyManaged=True, nativeProject=str(root), nativeIdentity='chat')
        windows = self.app.history.windows
        with patch('amplifier_web.automatic_history.directory', return_value=root):
            ordinary = await windows.read('chat', limit=20)
            ordinary_key = ('chat', ordinary['windowKey'])
            retained = copy.deepcopy(windows.pages[ordinary_key])
            windows.initial['chat'] = {'key': ordinary_key}
            before = copy.deepcopy(windows.project(self.app.session))
            utility = await windows.read('chat', limit=20, public_only=True)
            self.assertNotEqual(utility['windowKey'], ordinary['windowKey'])
            self.assertEqual(utility['items'][0]['text'], 'Public reply')
            self.assertEqual(windows.pages[ordinary_key], retained)
            self.assertEqual(windows.project(self.app.session), before)
            again = await windows.read('chat', limit=20)
            self.assertEqual(again, ordinary)

    async def test_conflicting_request_id_is_rejected(self):
        await self.request()
        with self.assertRaisesRegex(Exception, 'different inputs'):
            await self.request(expectedLastMessageId='other')

    async def test_exact_tombstone_precedes_capture_and_cancelled_capture_never_replays_request(self):
        entered = asyncio.Event()
        async def pending_capture(*args):
            self.assertFalse(self.app.db.in_transaction)
            self.assertEqual(len(list(self.app.db.execute('SELECT identity FROM suggestion_requests'))), 1)
            entered.set()
            await asyncio.Event().wait()
        with patch('amplifier_web.prompt_suggestions.capture', side_effect=pending_capture):
            await self.request('before-capture')
            await entered.wait()
            await self.host.dispatch('session.suggestion.cancel',
                {'sessionId': 'chat', 'requestId': 'before-capture'}, 'ui')
            await self.settle()
        self.assertEqual(self.calls, [])
        await self.host.close()
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.host.complete = AsyncMock(side_effect=AssertionError('request replay'))
        self.assertEqual((await self.request('before-capture'))['status'], 'expired')
        self.host.complete.assert_not_called()

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
        rows = [{'id': str(i), 'role': 'user', 'text': str(i), 'inputOrigin': 'ui'} for i in range(9)]
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
        self.assertEqual(provider.complete.call_args.kwargs['model'], 'explicit')
        self.assertFalse(provider.complete.call_args.kwargs['extended_thinking'])

    async def test_provider_call_options_cannot_silently_select_connection_default_model(self):
        class OptionsProvider:
            def __init__(self):
                self.models = []

            async def complete(self, request, **options):
                # Anthropic's established dispatch selects here, not from
                # ChatRequest.model. Keep this regression independent of SDKs.
                self.models.append(options.get('model', 'different-connection-default'))
                return SimpleNamespace(content=[SimpleNamespace(type='text', text='{"suggestion":null}')])

        provider = OptionsProvider()
        result = await complete(provider, {'default_model': 'different-connection-default'},
                                'explicit-app-selection', [], {})
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(provider.models, ['explicit-app-selection'])

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

    async def test_real_query_classifies_only_fixed_stage_and_class_without_sdk_error_text(self):
        from amplifier_web.provider_probe import query
        from amplifier_web.suggestion_provider import failure, FAILURE_REASONS
        private = 'SECRET candidate credential https://private.invalid/key'
        class UnknownError(Exception):
            status_code = 401
            def __str__(self):
                raise AssertionError('Never inspect SDK error prose')
        for name, status, expected in (
                ('AuthenticationError', 401, 'transport_authentication'),
                ('RateLimitError', 429, 'transport_rate_limit'),
                ('NotFoundError', 404, 'transport_not_found'),
                ('BadRequestError', 400, 'transport_invalid_request'),
                ('TypeError', None, 'transport_type_error')):
            kind = type(name, (Exception,), {'status_code': status})
            exc = kind(private)
            with self.subTest(name=name):
                self.assertEqual(failure(exc, 'transport')['reason'], expected)
        self.assertEqual(failure(UnknownError(private), 'transport')['reason'], 'provider_failed')
        self.assertEqual(failure(ValueError(private), private)['reason'], 'provider_failed')
        cases = [
            (type('AuthenticationError', (Exception,), {'status_code': 401})(private),
             None, 'transport_authentication'),
            (UnknownError(private), None, 'provider_failed'),
            (None, SimpleNamespace(type='text', text=private), 'parse_invalid_output'),
            (None, SimpleNamespace(type='text', text='{"other":"SECRET"}'), 'output_invalid_output'),
            (None, SimpleNamespace(type='tool_call', text=private), 'output_invalid_output')]
        for exc, block, expected in cases:
            provider = SimpleNamespace(get_info=lambda: {}, complete=AsyncMock(
                side_effect=exc, return_value=SimpleNamespace(content=[block], usage=None)))
            with patch('amplifier_web.provider_probe.provider_class', return_value=object), \
                    patch('amplifier_web.provider_probe.construct_schema_provider',
                          return_value=SimpleNamespace(get_info=lambda: {})), \
                    patch('amplifier_web.provider_probe.config_schema', AsyncMock(return_value={'fields': []})), \
                    patch('amplifier_web.provider_probe.construct_provider', return_value=provider), \
                    patch('amplifier_web.provider_probe.close_provider', AsyncMock()) as close:
                result = await query({'action': 'suggestion.complete', 'module': 'fixture',
                                      'config': {}, 'model': 'explicit', 'transcript': [],
                                      '_admit': AsyncMock(return_value=True)})
            self.assertEqual(result['suggestion']['reason'], expected)
            self.assertIn(expected, FAILURE_REASONS)
            self.assertNotIn('SECRET', repr(result))
            self.assertNotIn('diagnostic', result['suggestion'])
            provider.complete.assert_awaited_once()
            self.assertEqual(close.await_count, 2)
        with patch('amplifier_web.provider_probe.provider_class', side_effect=TypeError(private)):
            result = await query({'action': 'suggestion.complete', 'module': 'fixture'})
        self.assertEqual(result['suggestion']['reason'], 'construction_type_error')


class UtilitySourcePolicy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from amplifier_web.runtime_environment import receipt_directory
        from amplifier_web.runtime_qualification import frozen_manifest
        from amplifier_web.runtime_source_policy import with_origin
        from amplifier_web.setup import SetupManager
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'app'
        self.generation = 'a' * 32
        self.receipts = receipt_directory(self.home, self.generation)
        self.receipts.mkdir(parents=True)
        (self.home / 'updates/active.json').write_text(json.dumps({'current': self.generation}))
        self.project = self.home / 'runtime' / ('q-' + 'b' * 32)
        self.executable = self.project / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'not executed by this fixture')
        (self.receipts / 'runtime-project.json').write_text(json.dumps({'project': self.project.name}))
        self.module = 'provider-openai'
        self.name = 'amplifier-module-' + self.module
        self.url = 'https://github.com/microsoft/amplifier-module-provider-openai'
        self.revision = 'c' * 40
        self.authored = 'git+' + self.url + '@main'
        location = self.root / 'source-store/builds' / ('d' * 64)
        self.graph = [{'name': self.name, 'version': '1', 'path': str(location),
            'directUrl': {'url': location.as_uri(), 'dir_info': {'editable': False}},
            'cacheSource': {'url': self.url, 'ref': self.revision, 'revision': self.revision,
                            'dirty': False, 'subdirectory': '.', 'trackedContentSha256': 'e' * 64}}]
        self.policies = {self.name: with_origin({
            'url': self.url, 'ref': 'main', 'revision': self.revision, 'subdirectory': ''},
            'verified-cache-branch')}
        base = ('[project]\nname="fixture-worker"\nversion="1"\ndependencies=["' + self.name + '"]\n'
                '[tool.uv.sources]\n"' + self.name + '"={git="' + self.url + '",branch="main"}\n').encode()
        self.manifest = frozen_manifest(base, self.graph)
        self.save_receipts()
        self.manager = SetupManager(self.home, global_only=True)
        self.manager.probe_snapshot = lambda *args: {
            'module': self.module, 'raw': {}, 'config': {}, 'env': {},
            'credentialGeneration': 'fixture', 'source': self.authored, 'registryHome': str(self.home / 'foundation')}

    async def asyncTearDown(self):
        await self.manager.close()
        self.tmp.cleanup()

    def save_receipts(self):
        (self.receipts / 'runtime-installed.json').write_text(json.dumps(self.graph))
        (self.receipts / 'runtime-sources.json').write_text(json.dumps(self.policies))
        (self.receipts / 'runtime.toml').write_bytes(self.manifest)

    def matches(self, source=None, graph=None, policies=None, manifest=None):
        return qualified_source_matches(self.module, source or self.authored,
            self.graph if graph is None else graph, self.policies if policies is None else policies,
            self.manifest if manifest is None else manifest)

    def test_verified_authored_branch_matches_exact_shared_build_without_relabelling_catalog(self):
        from amplifier_web.provider_catalog import _qualified_provider_sources, generation_identity
        # Reproduce the actual defect shape: the legacy catalog chooses cached
        # SHA, while valid branch policy is carried separately by qualification.
        legacy = _qualified_provider_sources(str(self.home), generation_identity(self.home))
        self.assertEqual(legacy[self.module], 'git+' + self.url + '@' + self.revision)
        self.assertNotEqual(legacy[self.module], self.authored)
        before = copy.deepcopy((self.graph, self.policies, self.manifest))
        self.assertTrue(self.matches())
        self.assertTrue(self.matches(source='git+' + self.url + '@' + self.revision))
        self.assertTrue(self.matches(source='git+' + self.url + '.git@refs/heads/main'))
        self.assertEqual((self.graph, self.policies, self.manifest), before)

    def test_branch_equivalence_cannot_hide_changed_source_pin_or_unqualified_provenance(self):
        for source in ('git+https://example.invalid/fork@main', 'git+' + self.url + '@other',
                       'git+' + self.url + '@' + 'f' * 40, self.authored + '#subdirectory=other',
                       '/tmp/local-provider', self.authored + '#subdirectory=&other=true'):
            with self.subTest(source=source):
                self.assertFalse(self.matches(source=source))
        for field, value in (('revision', 'f' * 40), ('url', self.url + '-fork'),
                             ('subdirectory', 'other'), ('dirty', True),
                             ('trackedContentSha256', 'missing')):
            graph = copy.deepcopy(self.graph)
            graph[0]['cacheSource'][field] = value
            with self.subTest(cached=field):
                self.assertFalse(self.matches(graph=graph))
        self.assertFalse(self.matches(graph=[*self.graph, *self.graph]))
        self.assertFalse(self.matches(policies={}))
        for field, value in (('ref', 'other'), ('revision', 'f' * 40),
                             ('url', self.url + '-fork'), ('subdirectory', 'other')):
            policies = copy.deepcopy(self.policies)
            policies[self.name][field] = value
            with self.subTest(policy=field):
                self.assertFalse(self.matches(policies=policies))
        for kind in ('unrecorded', 'unresolved-reference', 'tag-reference', 'owned-cache-declaration'):
            policies = copy.deepcopy(self.policies)
            policies[self.name]['policyOrigin']['kind'] = kind
            with self.subTest(origin=kind):
                self.assertFalse(self.matches(policies=policies))
        policies = copy.deepcopy(self.policies)
        policies[self.name]['policyOrigin']['revision'] = 'f' * 40
        self.assertFalse(self.matches(policies=policies))

    def test_cached_source_requires_matching_frozen_path_and_noneditable_installed_binding(self):
        self.assertFalse(self.matches(manifest=self.manifest.replace(str(self.root).encode(), b'/other-owner')))
        for field, value in (('path', '/other-owner/build'),
                             ('directUrl', {'url': (self.root / 'other').as_uri(), 'dir_info': {'editable': False}})):
            graph = copy.deepcopy(self.graph)
            graph[0][field] = value
            self.assertFalse(self.matches(graph=graph))
        graph = copy.deepcopy(self.graph)
        graph[0]['directUrl']['dir_info']['editable'] = True
        self.assertFalse(self.matches(graph=graph))
        self.assertFalse(self.matches(manifest=b'not toml'))
        self.assertFalse(self.matches(manifest=self.manifest.replace(b'"editable" = false', b'"editable" = true')))

    async def test_normal_installed_branch_selection_uses_only_qualified_interpreter_worker_free(self):
        process = SimpleNamespace(
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock(return_value=b'{"suggestionReady":true}\n')),
            stderr=object(), returncode=0)
        terminal = json.dumps({'suggestion': {'status': 'empty', 'suggestion': None, 'usage': None, 'cost': None}}).encode()
        before = {name: (self.receipts / name).read_bytes() for name in
                  ('runtime-installed.json', 'runtime-sources.json', 'runtime.toml')}
        with patch('amplifier_web.probe_process.spawn', AsyncMock(return_value=process)) as spawn, \
                patch('amplifier_web.probe_process.stop', AsyncMock()) as stop, \
                patch('amplifier_web.probe_diagnostics.communicate', AsyncMock(return_value=terminal)), \
                patch('amplifier_web.probe_diagnostics.stderr_tail', AsyncMock(return_value='')), \
                patch('amplifier_web.provider_health.record'), \
                patch('amplifier_web.runtime.RuntimeManager._command', side_effect=AssertionError('uv sync')), \
                patch('amplifier_foundation.Bundle.prepare', AsyncMock(side_effect=AssertionError('prepare'))), \
                patch('amplifier_web.runtime_qualification.installed_graph', side_effect=AssertionError('Git scan')):
            result = await self.manager.probe('suggestion.complete', {
                'id': 'saved-account', 'model': 'cheap-explicit', 'transcript': [], '_admit': lambda: True},
                str(self.root))
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(spawn.call_args.args[0], str(self.executable))
        self.assertEqual(Path(spawn.call_args.args[1]).name, 'provider_probe.py')
        payload = json.loads(process.stdin.write.call_args.args[0])
        self.assertIsNone(payload['source'])  # Never install the authored branch per request.
        self.assertEqual(payload['model'], 'cheap-explicit')
        self.assertEqual(self.authored, 'git+' + self.url + '@main')  # No settings rewrite to SHA.
        self.assertEqual({name: (self.receipts / name).read_bytes() for name in before}, before)
        spawn.assert_awaited_once()
        stop.assert_awaited_once()

    async def test_unprepared_mismatched_and_disabled_choices_refuse_before_spawning(self):
        from amplifier_web.host.config import HostConfig
        with patch('amplifier_web.probe_process.spawn', AsyncMock(side_effect=AssertionError('spawn'))) as spawn:
            self.authored = 'git+https://example.invalid/fork@main'
            result = await self.manager.probe('suggestion.complete', {
                'id': 'saved-account', 'model': 'explicit', 'transcript': [], '_admit': lambda: True}, str(self.root))
            self.assertEqual(result['reason'], 'qualified_source_mismatch')
            (self.receipts / 'runtime-installed.json').unlink()
            result = await self.manager.probe('suggestion.complete', {
                'id': 'saved-account', 'model': 'explicit', 'transcript': [], '_admit': lambda: True}, str(self.root))
            self.assertEqual(result['reason'], 'qualified_source_mismatch')
            (self.receipts / 'runtime-project.json').unlink()
            result = await self.manager.probe('suggestion.complete', {
                'id': 'saved-account', 'model': 'explicit', 'transcript': [], '_admit': lambda: True}, str(self.root))
            self.assertEqual(result['reason'], 'runtime_not_prepared')
            del self.manager.probe_snapshot  # Exercise the ordinary disabled-connection guard.
            self.manager.config = lambda workspace: HostConfig(home=self.home, registry_home=self.home / 'foundation', settings={
                'config': {'providers': [{'id': 'saved-account', 'module': self.module,
                                          'enabled': False, 'source': self.authored}]}}, workspace=self.root)
            with self.assertRaises(ValueError):
                await self.manager.probe('suggestion.complete', {
                    'id': 'saved-account', 'model': 'explicit', 'transcript': [], '_admit': lambda: True}, str(self.root))
        spawn.assert_not_called()

    def test_direct_git_qualified_install_keeps_same_policy_equivalence(self):
        from amplifier_web.runtime_qualification import frozen_manifest
        direct = [{'name': self.name, 'version': '1', 'directUrl': {
            'url': self.url, 'subdirectory': '', 'vcs_info': {
                'vcs': 'git', 'requested_revision': self.revision, 'commit_id': self.revision}}}]
        base = ('[project]\nname="fixture-worker"\nversion="1"\ndependencies=["' + self.name + '"]\n').encode()
        manifest = frozen_manifest(base, direct)
        self.assertTrue(self.matches(graph=direct, manifest=manifest))
        self.assertTrue(self.matches(source='git+' + self.url + '@' + self.revision,
                                     graph=direct, manifest=manifest))
        direct[0]['directUrl']['vcs_info']['commit_id'] = 'f' * 40
        self.assertFalse(self.matches(graph=direct, manifest=manifest))


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

    async def test_uncertain_paid_input_survives_real_cold_retirement_and_reopen_without_replay(self):
        from amplifier_web.service import AppService
        from amplifier_web.cold_display import ColdRecord
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, messages=[
            {'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant',
             'inputOrigin': 'ui' if i % 2 == 0 else 'agent',
             'text': 'Public message ' + str(i) + ' ' + 'x' * 1100} for i in range(20)])
        self.assertIsInstance(session, ColdRecord)
        self.app._publish_changes(sessions={sid})
        config = {'enabled': True, 'provider': 'saved', 'model': 'explicit',
                  'humanTurns': 6, 'transcriptMode': 'last_n'}
        host = self.app.prompt_suggestions
        host.settings = lambda: (config, {'id': 'saved'}, 'revision')
        outbound = []
        async def uncertain(record, config, rows):
            from amplifier_web.suggestion_settings import digest
            expected = digest(['suggestion-input-v1', record['principal'], sid,
                               record['configRevision'], rows])
            self.assertFalse(self.app.db.in_transaction, 'paid claim must commit before transport')
            self.assertEqual(self.app.db.execute('SELECT source FROM suggestion_requests WHERE identity=?',
                                                (record['identity'],)).fetchone(), (expected,))
            outbound.append(copy.deepcopy(rows))
            raise TimeoutError('No terminal provider outcome')
        host.complete = uncertain
        self.app.clients.attach('browser', kind='web')
        self.app.clients.records['browser']['selectedSessionId'] = sid
        args = {'sessionId': sid, 'requestId': 'resident-attempt', 'expectedLastMessageId': '19'}
        token = BROWSER.set({'principal': 'same-cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                pending = await host.dispatch('session.suggestion.request', args, 'ui')
                self.assertEqual(pending['status'], 'pending')
                await asyncio.gather(*tuple(host.jobs))
                result = await host.dispatch('session.suggestion.status',
                    {'sessionId': sid, 'requestId': 'resident-attempt'}, 'ui')
                self.assertEqual(result['status'], 'timeout')
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            self.assertEqual(len(outbound), 1)
            # Ordinary committed ColdDisplay retirement; no mocked resource
            # references, hydration shortcut or replacement serialization.
            self.app._state['selectedSessionId'] = None
            self.app.clients.records['browser']['selectedSessionId'] = None
            with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                self.assertIn(sid, self.app.cold_display.retire(force=True))
            self.assertFalse(dict.__contains__(session, 'messages'))
            self.assertTrue(dict.get(session, '_coldFields', {}).get('messages'))
            await self.app.close()
            self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)
            host = self.app.prompt_suggestions
            host.settings = lambda: (config, {'id': 'saved'}, 'revision')
            host.complete = AsyncMock(side_effect=AssertionError('Unchanged cold source paid twice'))
            self.app.clients.attach('browser', kind='web')
            reopened = self.app._session(sid, hydrate=False)
            # Startup reconciliation may materialize a saved display owner.
            # Put it through the same ordinary retirement, not a mocked marker.
            self.app._state['selectedSessionId'] = None
            self.app.clients.records['browser']['selectedSessionId'] = None
            if dict.__contains__(reopened, 'messages'):
                with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                    self.assertIn(sid, self.app.cold_display.retire(force=True))
            self.assertFalse(dict.__contains__(reopened, 'messages'))
            self.app.clients.records['browser']['selectedSessionId'] = sid
            with self.app.clients.bind('browser'):
                pending = await host.dispatch('session.suggestion.request', {
                    **args, 'requestId': 'cold-attempt'}, 'ui')
                self.assertEqual(pending['status'], 'pending')
                await asyncio.gather(*tuple(host.jobs))
                result = await host.dispatch('session.suggestion.status',
                    {'sessionId': sid, 'requestId': 'cold-attempt'}, 'ui')
                self.assertEqual(result['status'], 'duplicate_source')
            host.complete.assert_not_called()
            self.assertFalse(dict.__contains__(reopened, 'messages'), 'bounded capture must not hydrate cold history')
            self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                                     (claims[0][0],))), claims)
        finally:
            BROWSER.reset(token)

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
            {'id': 'human', 'role': 'user', 'text': 'What next?', 'inputOrigin': 'ui'},
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
            {'id': 'human', 'role': 'user', 'text': 'Human', 'inputOrigin': 'ui'},
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
            # Same-cookie client IDs are a presentation partition, not a secret
            # tab credential. The same authenticated user can select known ID
            # "one"; the contract deliberately does not promise tab isolation.
            response = await client.post('/api/actions', json=status, headers=headers(cookie_one, 'one'))
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

    async def test_real_probe_protocol_preserves_safe_reason_with_malicious_sdk_error(self):
        """Real query/main, owned pipes and host decode; no SDK/network."""
        from amplifier_web.setup import SetupManager
        script = 'import sys; sys.path.insert(0, ' + repr(str(Path(__file__).resolve().parents[1])) + ')\n' + """
from types import SimpleNamespace
from amplifier_web import provider_probe as probe
class AuthenticationError(Exception):
    status_code = 401
    def __str__(self):
        raise AssertionError('Never read SDK body SECRET https://private/key')
class Fixture:
    def __init__(self, config=None): pass
    def get_info(self): return {}
    def get_config_schema(self): return {'fields': []}
    async def complete(self, request, **kwargs):
        raise AuthenticationError('SECRET candidate credential https://private/key')
probe.provider_class = lambda module: Fixture
probe.main()
"""
        manager = SetupManager(self.root / 'app', global_only=True, probe_command=[sys.executable, '-c', script])
        manager.probe_snapshot = lambda *args: {'module': 'provider-fixture', 'raw': {}, 'config': {},
            'env': dict(os.environ), 'credentialGeneration': 'fixture', 'source': None, 'registryHome': str(self.root)}
        try:
            with patch('amplifier_web.provider_health.generation', return_value='fixture'), \
                    patch('amplifier_web.provider_health.record') as health:
                result = await asyncio.wait_for(manager.probe('suggestion.complete', {
                    'id': 'fixture', 'model': 'explicit', 'transcript': [], '_admit': lambda: True}, str(self.root)), 15)
            health.assert_not_called()
            self.assertEqual(result, {'status': 'failed', 'suggestion': None, 'usage': None,
                                      'cost': None, 'reason': 'transport_authentication'})
            self.assertNotIn('SECRET', repr(result))
        finally:
            await manager.close()


if __name__ == '__main__':
    unittest.main()