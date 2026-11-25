"""Offline utility-request regressions; runnable with unittest or pytest."""
import asyncio
import copy
from contextlib import ExitStack
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
from amplifier_web.suggestion_provider import complete, parse, qualified_source_matches, INSTRUCTION, OUTPUT_CONTRACT
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
        self._state = {'settings': {'suggestedMessages': True}}  # Explicitly opted-in utility fixture.
        self.session = {'id': 'chat', 'status': 'idle', 'messages': [
            {'id': 'human', 'role': 'user', 'text': 'Plan the next step.', 'inputOrigin': 'ui'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'The plan is ready.'}]}
        self.history = SimpleNamespace(windows=TranscriptWindows(self))
        self.projections = SimpleNamespace(resolver=None)

    def _session(self, sid, hydrate=False):
        if sid != self.session['id'] or self.session.get('_deleting'):
            raise ValueError('missing')
        return self.session

    @property
    def state(self):
        return self._state


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

    async def cancel(self, request_id='request'):
        return await self.host.dispatch('session.suggestion.cancel',
                                       {'sessionId': 'chat', 'requestId': request_id}, 'ui')

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

    async def test_update_drain_blocks_new_work_cached_delivery_and_final_grant(self):
        ready = await self.terminal_request('before-drain')
        self.assertEqual(ready['status'], 'ready')
        record = next(iter(self.host.records.values()))
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        cache = list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))
        for phase in ('draining', 'replacing', 'releasing'):
            self.app.state['updates'] = {'drain': {'phase': phase, 'attemptId': 'update'}}
            with self.subTest(phase=phase):
                # The same currentness predicate supplies the subprocess's
                # final transport grant, as well as status/cache admission.
                self.assertFalse(self.host.current(record))
                self.assertEqual(record['_currentFailure'], 'current_eligibility')
                result = await self.request('during-' + phase)
                self.assertEqual(result['status'], 'unsupported')
                self.assertIsNone(result['suggestion'])
        self.assertEqual((await self.status('before-drain'))['status'], 'stale')
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_success_cache')), cache)
        self.assertEqual(len(self.calls), 1)

    async def test_newer_unconfirmed_input_fences_old_tail_without_rewriting_paid_claims(self):
        ready = await self.terminal_request('before-input')
        self.assertEqual(ready['status'], 'ready')
        record = next(iter(self.host.records.values()))
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        for status in ('pending', 'sending', 'failed', 'unknown', 'queued', 'queued_for_update'):
            pending = {'id': 'new-input', 'role': 'user', 'inputOrigin': 'ui',
                       'text': 'New saved work', 'delivery': {'status': status}}
            self.app.session['messages'].append(pending)
            self.app.session['messages'].append(
                {'id': 'private-tail', 'role': 'assistant', 'text': 'Not a public completion',
                 'metadata': {'private': True}})
            try:
                with self.subTest(status=status):
                    self.assertFalse(self.host.current(record))
                    result = await self.request('after-' + status)
                    self.assertEqual(result['status'], 'unsupported')
                    self.assertIsNone(result['suggestion'])
                    self.assertEqual((await self.status('before-input'))['status'], 'stale')
                    # Native history may not contain this saved input yet.
                    # Do not use its older completed assistant to bypass it.
                    with patch.dict(self.app.session, historyManaged=True), \
                            patch.object(self.app.history.windows, 'public_native_tail',
                                         side_effect=AssertionError('must fence before native read')):
                        with self.assertRaisesRegex(Unavailable, 'input_not_settled'):
                            witness(self.app, 'chat', self.config)
            finally:
                self.app.session['messages'].pop()
                self.app.session['messages'].pop()
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual(len(self.calls), 1)
        # A later settled assistant is new work, not a forever hold caused by
        # an earlier failed row. The old input still cannot enter the prompt.
        self.app.session['messages'][0:0] = [
            {'id': 'old-failed', 'role': 'user', 'text': 'Old failed work',
             'delivery': {'status': 'failed'}}]
        self.assertTrue(self.host.current(record))

    async def test_pending_capture_crossing_update_release_never_starts_completion(self):
        from amplifier_web.suggestion_source import capture
        captured, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            result = await capture(*args)
            captured.set()
            await release.wait()
            return result
        with patch('amplifier_web.prompt_suggestions.capture', delayed):
            pending = await self.request('cross-release')
            self.assertEqual(pending['status'], 'pending')
            await asyncio.wait_for(captured.wait(), 2)
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            self.app.state['updates'] = {'drain': {'phase': 'releasing', 'attemptId': 'update',
                                                   'releasingInput': ['another-chat', 'earlier-input']}}
            self.app.session['messages'].append({
                'id': 'later-input', 'role': 'user', 'inputOrigin': 'ui',
                'text': 'Saved during update', 'delivery': {'status': 'queued_for_update'}})
            release.set()
            await self.settle()
        self.assertEqual((await self.status('cross-release'))['status'], 'stale')
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())

    async def test_annotation_only_unsettled_tail_survives_partial_and_observation_filtering(self):
        from amplifier_web.suggestion_source import display_inputs
        pending = {'id': 'saved-input', 'inputId': 'exact-input', 'role': 'user',
                   'inputOrigin': 'ui', 'text': 'Saved but not settled',
                   'delivery': {'status': 'accepted'}}
        partial = {'id': 'partial', 'role': 'assistant', 'text': 'Not finished', 'partial': True}
        observation = {'id': 'observation', 'role': 'assistant', 'text': 'Internal update',
                       'observation': {'source': 'system', 'id': 'status'}}
        before = copy.deepcopy(self.app.session['messages'])
        for phase in ('queued', 'working', 'waiting', 'failed', 'unknown'):
            with self.subTest(phase=phase):
                self.app.session['messageAnnotations'] = {'input-status:exact-input': {
                    'inputStatus': {'version': 1, 'inputId': 'exact-input', 'phase': phase,
                                    'basis': 'execution_observed'}}}
                rows = before + [pending, partial, observation]
                self.app.session['messages'] = rows
                self.assertNotIn('inputStatus', pending)
                with self.assertRaisesRegex(Unavailable, 'input_not_settled'):
                    witness(self.app, 'chat', self.config)
                self.assertNotIn('inputStatus', pending)  # Annotation remains a projection.
                completed = {'id': 'completed-later', 'role': 'assistant', 'text': 'Later settled reply'}
                rows.append(completed)
                witness(self.app, 'chat', self.config)  # Old failed input is not a permanent hold.
                projected = public_rows(display_inputs(self.app, self.app.session, rows))
                self.assertEqual(projected[-1]['id'], 'completed-later')
                self.assertFalse({'saved-input', 'partial', 'observation'} & {row['id'] for row in projected})

    async def test_ready_retains_source_after_clock_expiry_and_reuses_new_audience_without_provider(self):
        async def reported(*args):
            self.calls.append('completion')
            return {'status': 'ready', 'suggestion': 'Please carry out the next step.',
                    'usage': {'input_tokens': 20, 'output_tokens': 8}, 'cost': None}
        self.host.complete = reported
        original = await self.terminal_request('original')
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        record = next(iter(self.host.records.values()))
        record['created'] -= 91
        self.host._expire(record)  # Even a previously scheduled callback cannot erase READY.
        self.assertEqual((await self.status('original'))['status'], 'ready')
        self.assertIsNone(original['expiresAt'])
        self.assertEqual(original['retention'], 'source')
        self.assertFalse(original['reused'])
        self.assertTrue(original['generationId'])
        self.app.clients.records['one']['selectedSessionId'] = 'elsewhere'
        self.app.clients.records['one']['view'] = {'draft': 'Private unsent draft'}
        self.app.clients.current.set('two')
        self.assertEqual((await self.status('original'))['status'], 'expired')
        self.app.clients.records.pop('one')  # Reload need not keep the old tab record alive.
        reused = await self.terminal_request('rebound')
        self.assertEqual(reused['status'], 'ready')
        self.assertEqual(reused['suggestion'], original['suggestion'])
        self.assertEqual(reused['generationId'], original['generationId'])
        self.assertEqual(reused['usage'], original['usage'])
        self.assertIsNone(reused['cost'])
        self.assertIsNone(reused['expiresAt'])
        self.assertEqual(reused['retention'], 'source')
        self.assertTrue(reused['reused'])
        self.assertEqual(self.calls, ['completion'])
        self.assertEqual(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                            (claims[0][0],)).fetchone(), claims[0])
        self.assertNotIn(original['suggestion'], repr(list(self.app.db.execute('SELECT * FROM suggestion_requests'))))

    async def test_superseded_regeneration_flag_is_rejected_and_legacy_claims_stay_inert(self):
        self.app.db.execute('INSERT INTO suggestion_requests VALUES (?,?)', ('old-intent', 'old-manual-input'))
        self.app.db.commit()
        for invalid in (True, False, 'true', 1, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Exception, 'automatic only'):
                await self.request('bad', regenerate=invalid)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')),
                         [('old-intent', 'old-manual-input')])

    async def test_corrupt_oversized_or_invalid_durable_cache_never_exposes_text_or_retries_claim(self):
        for index, corrupt in enumerate((
                '{not json', 'x' * 5000,
                json.dumps({'status': 'ready', 'suggestion': 'x' * 241, 'usage': None,
                            'cost': None, 'generationId': 'a' * 32}),
                json.dumps({'status': 'empty', 'suggestion': 'SECRET', 'usage': None,
                            'cost': None, 'generationId': 'a' * 32}),
                json.dumps({'status': 'failed', 'suggestion': 'SECRET', 'usage': None,
                            'cost': None, 'generationId': 'a' * 32}))):
            self.app.session['messages'][-1]['text'] = 'Cache source ' + str(index)
            await self.terminal_request('populate-' + str(index))
            self.app.db.execute('UPDATE suggestion_success_cache SET value=?', (corrupt,))
            self.app.db.commit()
            result = await self.terminal_request('corrupt-' + str(index))
            self.assertEqual(result['status'], 'duplicate_source')
            self.assertEqual(result['reason'], 'prior_attempt_unavailable')
            self.assertNotIn('SECRET', repr(result))
            self.assertIsNone(result['suggestion'])
            self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_success_cache').fetchone()[0], 0)
        self.assertEqual(len(self.calls), 5)

    async def test_cache_write_failure_cannot_publish_success_or_retry_provider(self):
        self.app.db.execute('''CREATE TRIGGER fail_success_insert BEFORE INSERT ON suggestion_success_cache
            BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END''')
        self.app.db.commit()
        result = await self.terminal_request('storage-failed')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['suggestion'])
        self.assertNotIn('synthetic disk failure', repr(result))
        self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_success_cache').fetchone()[0], 0)
        self.app.db.execute('DROP TRIGGER fail_success_insert')
        self.app.db.commit()
        repeated = await self.terminal_request('after-storage-failure')
        self.assertEqual(repeated['status'], 'duplicate_source')
        self.assertEqual(len(self.calls), 1)

    async def test_hash_only_legacy_paid_claim_is_not_reclassified_or_replayed_into_new_cache(self):
        from amplifier_web.suggestion_settings import digest
        from amplifier_web.suggestion_source import capture
        rows, _ = await capture(self.app, 'chat', self.config, 'assistant')
        source = digest(['suggestion-input-v1', 'cookie-one', 'chat', self.revision, rows])
        self.app.db.execute('INSERT INTO suggestion_requests VALUES (?,?)', ('legacy-unknown-request', source))
        self.app.db.commit()
        before = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        self.host.complete = AsyncMock(side_effect=AssertionError('legacy migration must never infer'))
        result = await self.terminal_request('new-document')
        self.assertEqual(result['status'], 'duplicate_source')
        self.assertEqual(result['reason'], 'prior_attempt_unavailable')
        self.assertIsNone(result['suggestion'])
        self.host.complete.assert_not_called()
        self.assertTrue(all(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                               (row[0],)).fetchone() == row for row in before))
        self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())

    async def test_durable_cache_lru_per_principal_total_bounds_and_eviction_keep_paid_claims(self):
        with patch('amplifier_web.prompt_suggestions.CACHE_PER_PRINCIPAL', 2), \
                patch('amplifier_web.prompt_suggestions.CACHE_TOTAL', 3):
            for i in range(3):
                sid = 'chat-' + str(i)
                self.app.session['id'] = sid
                self.app.clients.records['one']['selectedSessionId'] = sid
                await self.host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': sid, 'expectedLastMessageId': 'assistant'}, 'ui')
                await self.settle()
            rows = list(self.app.db.execute('SELECT session_id,length(CAST(value AS BLOB)) FROM suggestion_success_cache'))
            self.assertEqual({row[0] for row in rows}, {'chat-1', 'chat-2'})
            self.assertTrue(all(row[1] <= 4096 for row in rows))
            self.app.session['id'] = 'chat-1'
            self.app.clients.records['one']['selectedSessionId'] = 'chat-1'
            await self.host.dispatch('session.suggestion.request', {
                'sessionId': 'chat-1', 'requestId': 'touch', 'expectedLastMessageId': 'assistant'}, 'ui')
            await self.settle()
            token = BROWSER.set({'principal': 'other-cookie', 'valid': lambda: True})
            try:
                for i in (3, 4):
                    sid = 'chat-' + str(i)
                    self.app.session['id'] = sid
                    self.app.clients.records['one']['selectedSessionId'] = sid
                    await self.host.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': sid, 'expectedLastMessageId': 'assistant'}, 'ui')
                    await self.settle()
            finally:
                BROWSER.reset(token)
            self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_success_cache').fetchone()[0], 3)
            self.assertIsNone(self.app.db.execute(
                "SELECT 1 FROM suggestion_success_cache WHERE session_id='chat-2'").fetchone())
            before = len(self.calls)
            self.app.session['id'] = 'chat-0'
            self.app.clients.records['one']['selectedSessionId'] = 'chat-0'
            await self.host.dispatch('session.suggestion.request', {
                'sessionId': 'chat-0', 'requestId': 'evicted', 'expectedLastMessageId': 'assistant'}, 'ui')
            await self.settle()
            result = await self.host.dispatch('session.suggestion.status', {
                'sessionId': 'chat-0', 'requestId': 'evicted'}, 'ui')
            self.assertEqual(result['status'], 'duplicate_source')
            self.assertEqual(len(self.calls), before)

    async def test_ready_lru_is_bounded_status_keeps_exact_record_and_eviction_never_replays(self):
        with patch('amplifier_web.prompt_suggestions.MAX_RETAINED', 3):
            for i in range(3):
                self.app.session['messages'][-1]['text'] = 'Completed ' + str(i)
                await self.terminal_request('source-' + str(i))
            # Repeated exact status at capacity must not evict an unrelated
            # candidate, nor the exact oldest candidate being accessed.
            await self.status('source-0')  # Changed public source is correctly stale.
            self.assertEqual(len(self.host.records), 3)
            self.app.session['messages'][-1]['text'] = 'Completed 2'
            await self.status('source-2')
            await self.terminal_request('alias')
            self.assertEqual(len(self.host.records), 3)
            self.assertTrue((await self.status('alias'))['reused'])
            # Evict both aliases by introducing unrelated complete inputs.
            for i in range(3, 6):
                self.app.session['messages'][-1]['text'] = 'Completed ' + str(i)
                await self.terminal_request('source-' + str(i))
            self.assertEqual(len(self.host.records), 3)
            self.app.session['messages'][-1]['text'] = 'Completed 2'
            absent = await self.terminal_request('evicted-new-id')
            self.assertEqual(absent['status'], 'duplicate_source')
            self.assertEqual(absent['reason'], 'prior_attempt_unavailable')
            self.assertEqual(len(self.calls), 6)

    async def test_ready_lru_touch_and_cache_capture_revocation_preserve_authority(self):
        from amplifier_web.prompt_suggestions import capture as original_capture
        with patch('amplifier_web.prompt_suggestions.MAX_RETAINED', 3):
            await self.terminal_request('original')
            await self.terminal_request('alias-one')
            await self.terminal_request('alias-two')
            self.assertEqual(len(self.host.records), 3)
            await self.status('original')
            await self.terminal_request('alias-three')
            self.assertEqual(len(self.host.records), 3)
            ids = {row['requestId'] for row in self.host.records.values()}
            self.assertIn('original', ids)
            self.assertNotIn('alias-one', ids)
        async def revoke(*args):
            rows, key = await original_capture(*args)
            self.app.session['interruptionRevision'] = 1
            return rows, key
        self.host.complete = AsyncMock(side_effect=AssertionError('cache must not mask revoked authority'))
        with patch('amplifier_web.prompt_suggestions.capture', side_effect=revoke):
            denied = await self.terminal_request('changed-during-capture')
        self.assertEqual(denied['status'], 'stale')
        self.assertIsNone(denied['suggestion'])
        self.assertFalse(denied['reused'])
        self.host.complete.assert_not_called()

    async def test_ready_reuse_requires_original_epoch_source_stop_config_and_authenticated_principal(self):
        original = await self.terminal_request('original')
        self.app.clients.current.set('two')
        original_complete = self.host.complete
        self.host.complete = AsyncMock(side_effect=AssertionError('same paid input cannot infer automatically'))
        # Same canonical text/config with a different Stop/owner/epoch witness
        # cannot rebind an old READY even though its SQL input digest matches.
        for index, change in enumerate((
                patch.dict(self.app.session, interruptionRevision=1),
                patch.dict(self.app.session, nativeIdentity='other-native-owner'),
                patch('amplifier_web.auth._read_epoch', return_value=123))):
            with self.subTest(index=index), change:
                denied = await self.terminal_request('changed-witness-' + str(index))
                self.assertEqual(denied['status'], 'duplicate_source')
                self.assertIsNone(denied['suggestion'])
                self.assertFalse(denied['reused'])
        self.host.complete.assert_not_called()
        with patch.dict(self.app.session, archived=True):
            self.assertEqual((await self.request('archived'))['status'], 'stale')
        with patch.dict(self.app.session, _deleting=True):
            with self.assertRaisesRegex(ValueError, 'missing'):
                await self.request('deleting')
        self.app.session['messages'][-1]['text'] = 'Actually edited same ID'
        self.host.complete = original_complete
        changed = await self.terminal_request('edited')
        self.assertFalse(changed['reused'])
        self.assertNotEqual(changed['generationId'], original['generationId'])
        self.revision = 'new-settings'
        changed = await self.terminal_request('settings')
        self.assertFalse(changed['reused'])
        token = BROWSER.set({'principal': 'foreign-cookie', 'valid': lambda: True})
        try:
            self.assertIsNone((await self.status('original'))['suggestion'])
            foreign = await self.terminal_request('foreign-own')
            self.assertFalse(foreign['reused'])
            self.assertNotEqual(foreign['generationId'], changed['generationId'])
        finally:
            BROWSER.reset(token)
        token = BROWSER.set({'principal': 'cookie-one', 'valid': lambda: False})
        try:
            with self.assertRaisesRegex(Exception, 'browser displaying'):
                await self.request('revoked')
        finally:
            BROWSER.reset(token)
        self.assertEqual(len(self.calls), 4)

    async def test_known_terminal_disposition_is_private_and_cancelled_or_failed_never_auto_retries(self):
        for index, state in enumerate(('empty', 'failed', 'timeout', 'cancelled')):
            self.app.session['messages'][-1]['text'] = 'Terminal input ' + str(index)
            self.host.complete = AsyncMock(return_value={
                'status': state, 'suggestion': None, 'usage': None, 'cost': None})
            await self.terminal_request(state)
            repeated = await self.terminal_request('after-' + state)
            self.assertEqual(repeated['status'], 'empty' if state == 'empty' else 'duplicate_source')
            if state == 'empty':
                self.assertTrue(repeated['reused'])
            else:
                self.assertEqual(repeated['reason'], 'prior_attempt_' + state)
            self.host.complete.assert_awaited_once()
        self.assertNotIn('prior_attempt_', repr(list(self.app.db.execute('SELECT * FROM suggestion_requests'))))

    async def test_confirmed_enable_accepts_existing_assistant_source_and_focus_reuses_it(self):
        original = copy.deepcopy(self.app.session)
        self.app.state['settings']['suggestedMessages'] = False
        self.assertEqual((await self.request('off'))['status'], 'disabled')
        self.assertFalse(self.host.jobs)
        self.assertEqual(self.calls, [])
        # This fixture changes the confirmed preference, not the history. The
        # real settings commit/CAS is covered by the AppService cases below.
        self.app.state['settings']['suggestedMessages'] = True
        ready = await self.terminal_request('after-enable')
        self.assertEqual(ready['status'], 'ready')
        self.assertFalse(ready['reused'])
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        for _ in range(3):
            self.assertEqual(await self.status('after-enable'), ready)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        rebound = await self.terminal_request('same-source-visit')
        self.assertTrue(rebound['reused'])
        self.assertEqual(rebound['generationId'], ready['generationId'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.app.session, original)

    async def test_terminal_status_recovery_retains_original_source_and_reason_without_replay(self):
        for state, reason in (('empty', None), ('failed', 'transport_authentication'),
                              ('unsupported', 'runtime_not_prepared'), ('timeout', None),
                              ('cancelled', None)):
            with self.subTest(state=state):
                self.app.session['messages'][-1]['text'] = 'Distinct source ' + state
                self.host.complete = AsyncMock(return_value={
                    'status': state, 'suggestion': None, 'usage': None, 'cost': None, 'reason': reason})
                terminal = await self.terminal_request('terminal-' + state)
                self.assertEqual(terminal['status'], state)
                self.assertEqual(terminal['reason'], reason)
                self.assertTrue(terminal['sourceKey'])
                self.assertEqual(terminal['configRevision'], self.revision)
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                for _ in range(3):
                    self.assertEqual(await self.status('terminal-' + state), terminal)
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                self.host.complete.assert_awaited_once()
                # Same-cookie tabs are presentation partitions, not a claimed
                # security boundary, but a foreign principal gets no disposition.
                token = BROWSER.set({'principal': 'other-owner', 'valid': lambda: True})
                try:
                    foreign = await self.status('terminal-' + state)
                    self.assertEqual(foreign['status'], 'expired')
                    self.assertIsNone(foreign['sourceKey'])
                    self.assertIsNone(foreign['reason'])
                finally:
                    BROWSER.reset(token)

    async def test_expired_pending_exact_recovery_cannot_buy_another_attempt(self):
        entered = asyncio.Event()
        async def pending(*args):
            self.calls.append('one admitted call')
            entered.set()
            await asyncio.Event().wait()
        self.host.complete = pending
        await self.request('lost-pending')
        await entered.wait()
        record = next(iter(self.host.records.values()))
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        self.host._expire(record)
        await self.settle()
        self.assertEqual((await self.status('lost-pending'))['status'], 'expired')
        self.assertEqual((await self.request('lost-pending'))['status'], 'expired')
        # Simulate request-LRU loss while preserving the actual durable claim.
        self.host.records.clear()
        result = await self.terminal_request('lost-pending')
        self.assertEqual(result['status'], 'expired')
        self.assertEqual(result['reason'], 'prior_attempt_unavailable')
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual(len(self.calls), 1)

    async def test_cancel_preserves_completed_outcome_and_exact_receipt_without_replay(self):
        for state, reason in (('failed', 'construction_connection'), ('empty', None),
                              ('timeout', None), ('unsupported', 'runtime_not_prepared'),
                              ('cancelled', None), ('stale', 'capture_source_changed')):
            with self.subTest(state=state):
                request_id = 'completed-' + state
                self.app.session['messages'][-1]['text'] = 'Distinct input ' + state
                self.host.complete = AsyncMock(return_value={
                    'status': state, 'suggestion': None, 'usage': {'input_tokens': 17, 'output_tokens': 2},
                    'cost': 0.125, 'reason': reason})
                terminal = await self.terminal_request(request_id)
                self.assertEqual(terminal['status'], state)
                self.assertEqual(terminal['reason'], reason)
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                cache = list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))
                for _ in range(2):
                    self.assertEqual(await self.cancel(request_id), terminal)
                    self.assertEqual(await self.status(request_id), terminal)
                # Best-effort cancellation after navigation is still allowed
                # for this exact owner; it does not rewrite that past outcome.
                with patch.dict(self.app.clients.records['one'], selectedSessionId='elsewhere'):
                    self.assertEqual(await self.cancel(request_id), terminal)
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_success_cache')), cache)
                self.host.complete.assert_awaited_once()

    async def test_cancel_preserves_immediate_terminal_admission_refusals(self):
        cases = (
            ('disabled', patch.dict(self.app.state['settings'], suggestedMessages=False)),
            ('busy', patch.dict(self.app.session, status='working')),
            ('missing_selection', patch.object(self.host, 'settings',
                                               return_value=(self.config, None, self.revision))),
            ('unsupported', patch.dict(self.app.session, sessionKind='internal')),
        )
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        for state, gate in cases:
            with self.subTest(state=state), gate:
                terminal = await self.request('not-admitted-' + state)
                self.assertEqual(terminal['status'], state)
                self.assertEqual(await self.cancel('not-admitted-' + state), terminal)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual(self.calls, [])

    async def test_ready_cancel_retains_current_success_but_not_a_display_authority_grant(self):
        ready = await self.terminal_request('completed-ready')
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        cache = list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))
        self.assertEqual(await self.cancel('completed-ready'), ready)
        self.assertEqual(await self.status('completed-ready'), ready)
        rebound = await self.terminal_request('ready-rebound')
        self.assertTrue(rebound['reused'])
        self.assertEqual(rebound['generationId'], ready['generationId'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))), len(cache))
        # A foreign cookie cannot use cancel to acquire the completed body.
        token = BROWSER.set({'principal': 'foreign', 'valid': lambda: True})
        try:
            foreign = await self.cancel('completed-ready')
            self.assertEqual(foreign['status'], 'expired')
            self.assertIsNone(foreign['suggestion'])
        finally:
            BROWSER.reset(token)
        # Selection is optional only for withdrawal, never delivery of READY.
        with patch.dict(self.app.clients.records['one'], selectedSessionId='elsewhere'):
            withdrawn = await self.cancel('completed-ready')
            self.assertEqual(withdrawn['status'], 'stale')
            self.assertEqual(withdrawn['reason'], 'current_selected')
            self.assertIsNone(withdrawn['suggestion'])
        self.assertEqual((await self.status('completed-ready'))['status'], 'stale')
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests'))[:len(claims)], claims)
        self.assertEqual(len(self.calls), 1)

    async def test_ready_cancel_keeps_existing_source_stop_config_and_auth_fences(self):
        for dimension in ('source', 'stop', 'config', 'off', 'epoch', 'auth'):
            with self.subTest(dimension=dimension):
                self.app.session['messages'][-1]['text'] = 'Before ' + dimension
                ready = await self.terminal_request('ready-' + dimension)
                self.assertEqual(ready['status'], 'ready')
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                record = next(reversed(self.host.records.values()))
                changes = {
                    'source': lambda: patch.dict(self.app.session['messages'][-1], text='Changed'),
                    'stop': lambda: patch.dict(self.app.session, interruptionRevision=77),
                    'config': lambda: patch.object(self.host, 'settings',
                                                   return_value=(self.config, {'id': 'explicit-account'}, 'other')),
                    'off': lambda: patch.dict(self.app.state['settings'], suggestedMessages=False),
                    'epoch': lambda: patch.object(self.host, '_epoch', return_value='other-epoch'),
                }
                with ExitStack() as stack:
                    if dimension == 'auth':
                        # Restore only the authentication closure, not all
                        # record fields changed by the actual stale transition.
                        stack.callback(record.__setitem__, 'authenticated', record['authenticated'])
                        record['authenticated'] = lambda: False
                    else:
                        stack.enter_context(changes[dimension]())
                    refused = await self.cancel('ready-' + dimension)
                    self.assertEqual(refused['status'], 'stale')
                    self.assertIsNone(refused['suggestion'])
                self.assertEqual((await self.status('ready-' + dimension))['status'], 'stale')
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual(len(self.calls), 6)

    async def test_pending_cancel_is_idempotent_and_late_failure_or_success_cannot_replace_it(self):
        for late in ('failed', 'ready', 'exception'):
            with self.subTest(late=late):
                self.app.session['messages'][-1]['text'] = 'Pending input ' + late
                entered, settling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
                async def respond(*args):
                    self.calls.append('one admitted completion')
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        settling.set()
                        await release.wait()
                        if late == 'exception':
                            raise ConnectionError('PRIVATE late provider prose')
                        return {'status': late, 'suggestion': 'Too late.' if late == 'ready' else None,
                                'usage': {'input_tokens': 4}, 'cost': 0.01,
                                'reason': 'construction_connection' if late == 'failed' else None}
                self.host.complete = respond
                request_id = 'pending-' + late
                await self.request(request_id)
                await entered.wait()
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                cancelled = await self.cancel(request_id)
                await settling.wait()
                try:
                    self.assertEqual(cancelled['status'], 'cancelled')
                    self.assertEqual(await self.cancel(request_id), cancelled)
                    self.assertEqual((await self.request('busy-' + late))['status'], 'busy')
                    self.assertTrue(self.host.jobs, 'Cancellation retains physical occupancy')
                finally:
                    release.set()
                    await self.settle()
                self.assertEqual(await self.status(request_id), cancelled)
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                self.assertFalse(list(self.app.db.execute('SELECT * FROM suggestion_success_cache')))
                repeated = await self.terminal_request('after-cancel-' + late)
                self.assertEqual(repeated['status'], 'duplicate_source')
                self.assertEqual(repeated['reason'], 'prior_attempt_cancelled')
        self.assertEqual(len(self.calls), 3)

    async def test_pending_cancel_blocks_other_client_until_physical_settlement(self):
        entered, settling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def respond(*args):
            self.calls.append('entered')
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                settling.set()
                await release.wait()
                raise
        self.host.complete = respond
        await self.request('pending')
        await entered.wait()
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        await self.host.dispatch('session.suggestion.cancel', {'sessionId': 'chat', 'requestId': 'pending'}, 'ui')
        await settling.wait()
        self.app.clients.current.set('two')
        try:
            self.assertEqual((await self.request('too-early'))['status'], 'busy')
        finally:
            release.set()
            await self.settle()
        absent = await self.terminal_request('auto-return')
        self.assertEqual(absent['status'], 'duplicate_source')
        self.assertEqual(absent['reason'], 'prior_attempt_cancelled')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                            (claims[0][0],)).fetchone(), claims[0])


    async def test_same_request_and_new_request_same_source_do_not_replay(self):
        await self.request()
        await self.request()
        await self.settle()
        await self.request()
        self.assertTrue((await self.terminal_request('other'))['reused'])
        self.assertEqual(len(self.calls), 1)
        await self.host.close()
        self.host = PromptSuggestions(self.app)
        self.host.settings = lambda: (self.config, {'id': 'explicit-account'}, self.revision)
        self.assertTrue((await self.terminal_request('request'))['reused'])
        self.assertTrue((await self.terminal_request('third'))['reused'])

    async def test_new_browser_document_and_disposable_fence_do_not_repeat_paid_source(self):
        await self.request()
        await self.settle()
        self.app.history.windows.invalidate({'chat'})
        self.app.clients.current.set('two')
        self.assertTrue((await self.terminal_request('new-document'))['reused'])
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
            self.assertEqual((await self.terminal_request('uncertain'))['status'], 'expired')
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
        # Historical claim lookup remains indexed. The independently bounded
        # success table uses exact principal/session keys; trimming visits only
        # that <=128-row cache, never the 5000+ durable claim history.
        claim_selects = [sql for sql in selects if 'FROM suggestion_requests' in sql]
        self.assertTrue(all('WHERE identity=' in sql or 'WHERE source' in sql for sql in claim_selects))
        self.assertFalse(any('count(' in sql.lower() for sql in statements))
        self.assertEqual(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                            (original_claim[0],)).fetchone(), original_claim)
        self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_requests').fetchone()[0], 5005)

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
        self.assertTrue((await self.terminal_request('after-restart'))['reused'])
        self.host.complete.assert_not_called()

    async def test_nonready_expiry_is_absolute_and_ready_switches_to_explicit_source_retention(self):
        before = time.time() * 1000
        pending = await self.request()
        after = time.time() * 1000
        self.assertGreaterEqual(pending['expiresAt'], before + 60000)
        self.assertLessEqual(pending['expiresAt'], after + 60000)
        await self.settle()
        self.assertIsNone((await self.status())['expiresAt'])
        self.assertEqual((await self.request())['retention'], 'source')
        record = next(iter(self.host.records.values()))
        self.host._expire(record)
        self.assertEqual((await self.status())['status'], 'ready')
        self.assertTrue(record['expiry'].cancelled())
        self.assertEqual(record['_expiresAt'], pending['expiresAt'])
        await self.host.dispatch('session.suggestion.cancel',
                                 {'sessionId': 'chat', 'requestId': 'request'}, 'ui')
        # A best-effort cancellation cannot turn completed success into a
        # cancelled attempt or shorten its retention. Actual new input/Stop
        # still invalidates the body through the ordinary currentness check.
        self.assertEqual((await self.status())['retention'], 'source')
        self.assertIsNone((await self.status())['expiresAt'])
        self.app.session['interruptionRevision'] = 1
        self.assertEqual((await self.status())['status'], 'stale')
        self.assertIsNone((await self.status())['retention'])
        self.assertEqual((await self.status())['expiresAt'], pending['expiresAt'])

    async def test_external_and_shared_accepted_inputs_do_not_require_native_generation_status(self):
        for participation in ('exclusive', 'shared'):
            with self.subTest(participation=participation):
                self.app.session['externalAgent'] = {'participation': participation}
                self.app.session['messages'] = [
                    {'id': 'human', 'inputId': 'external-' + participation, 'role': 'user',
                     'text': 'Accepted external human ' + participation, 'inputOrigin': 'ui',
                     'delivery': {'status': 'accepted', 'scope': 'conversation'}, 'roomSequence': 1},
                    {'id': 'assistant', 'role': 'assistant', 'text': 'External response ' + participation,
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
            {'id': 'legacy', 'role': 'user', 'text': 'Unknown legacy input'},
            {'id': 'human-latest', 'role': 'user', 'text': 'Real latest input', 'inputOrigin': 'ui'}]
        await self.request()
        await self.settle()
        self.assertEqual((await self.status())['status'], 'ready')
        self.assertEqual([row['inputActor']['kind'] for row in self.calls[0]],
                         ['human', 'human', 'agent'])
        self.assertEqual([row['text'] for row in self.calls[0]][1:],
                         ['Real latest input', 'The plan is ready.'])

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
        with patch('amplifier_web.automatic_history.directory', return_value=root):
            ordinary = await self.app.history.windows.read('chat', limit=100)
            last = ordinary['items'][-1]['id']
            await self.request(expectedLastMessageId=last)
            await self.settle()
            self.assertEqual((await self.status())['status'], 'ready')
        outbound = self.calls[0]
        self.assertEqual([row['text'] for row in outbound],
                         ['Known human', 'Current spoken request', 'Completed reply'])
        self.assertEqual([row['inputActor']['kind'] for row in outbound],
                         ['human', 'human', 'agent'])
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
            self.assertTrue((await self.terminal_request('native-restart', expectedLastMessageId=last))['reused'])
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

    async def test_native_older_gap_before_exchange_does_not_allow_peer_reply_or_stale_cache(self):
        from amplifier_web.transcript_window import RECORD_LIMIT
        root = Path(self.tmp.name)
        path = root / 'transcript.jsonl'
        def user(text, identity, source='user'):
            return {'role': 'user', 'content': text, 'metadata': {'amplifier_input': {
                'version': 1, 'kind': 'user', 'id': identity, 'source': source}}}
        old = [user('Older prefix that is not covered', 'old'),
               {'role': 'tool', 'content': 't' * RECORD_LIMIT}]
        exchange = [user('First real request', 'human'),
                    user('Do not send peer content', 'peer', 'agent'),
                    {'role': 'assistant', 'content': 'Do not send the peer-only reply'},
                    user('Next real request', 'new-human'),
                    {'role': 'assistant', 'content': 'Here is the complete latest reply.'}]
        path.write_text(''.join(json.dumps(row) + '\n' for row in old + exchange))
        self.app.db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, receipt TEXT)')
        self.app.session.update(historyManaged=True, nativeProject=str(root), nativeIdentity='chat')
        with patch('amplifier_web.automatic_history.directory', return_value=root):
            page = await self.app.history.windows.read('chat', limit=20)
            self.assertTrue(page['unavailableRanges'])
            last = page['items'][-1]['id']
            self.assertEqual((await self.terminal_request('after-old-gap', expectedLastMessageId=last))['status'],
                             'ready')
            self.assertEqual([row['text'] for row in self.calls[0]], [
                'First real request', 'Next real request', 'Here is the complete latest reply.'])
            with path.open('a') as stream:
                for row in (user('Newest peer input', 'peer-tail', 'agent'),
                            {'role': 'assistant', 'content': 'Peer-only reply at the latest tail.'}):
                    stream.write(json.dumps(row) + '\n')
            self.assertEqual((await self.status('after-old-gap'))['status'], 'stale')
            page = await self.app.history.windows.read('chat', limit=20)
            latest = await self.terminal_request('peer-tail', expectedLastMessageId=page['items'][-1]['id'])
            self.assertEqual((latest['status'], latest['generationId']), ('empty', None))
            self.assertEqual(len(self.calls), 1)

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
        self.assertEqual((await self.terminal_request('before-capture'))['status'], 'expired')
        self.host.complete.assert_not_called()

    async def test_status_does_not_read_or_hydrate_transcript(self):
        await self.request()
        await self.settle()
        self.app.history.windows.read = AsyncMock(side_effect=AssertionError('read'))
        for _ in range(8):
            self.assertEqual((await self.status())['status'], 'ready')
        self.app.history.windows.read.assert_not_called()

    async def test_currentness_classifier_preserves_original_gates_and_only_static_dimensions(self):
        await self.request()
        await self.settle()
        record = next(iter(self.host.records.values()))
        def original():
            from amplifier_web.suggestion_settings import digest
            try:
                config, _, revision = self.host.settings()
                client = self.app.clients.records.get(record['client'], {})
                if (self.host.closed or self.app.closed or not record['authenticated']()
                        or record['epoch'] != self.host._epoch()
                        or digest([
                            record['principal'], client.get('deviceOwner', 'local-owner'),
                            record['client']]) != record['audience']
                        or client.get('selectedSessionId') != record['sessionId']
                        or record['configRevision'] != revision
                        or self.app._state['settings']['suggestedMessages'] is not True):
                    return False
                session = self.app._session(record['sessionId'], hydrate=False)
                from amplifier_web.prompt_suggestions import eligible, witness
                return (not session.get('archived') and not session.get('_deleting')
                        and eligible(self.app, session) and session.get('status') in {'idle', 'ready'}
                        and witness(self.app, record['sessionId'], config) == record['sourceKey'])
            except (ValueError, KeyError, OSError):
                return False
        cases = [
            ('closed', patch.object(self.host, 'closed', True)),
            ('auth', patch.dict(record, authenticated=lambda: False)),
            ('epoch', patch.object(self.host, '_epoch', return_value='PRIVATE epoch')),
            ('audience', patch.dict(self.app.clients.records['one'], deviceOwner='PRIVATE owner')),
            ('selected', patch.dict(self.app.clients.records['one'], selectedSessionId='PRIVATE session')),
            ('config', patch.object(self.host, 'settings', return_value=(self.config, {}, 'PRIVATE revision'))),
            ('preference', patch.dict(self.app._state['settings'], suggestedMessages=False)),
            ('session', patch.object(self.app, '_session', side_effect=ValueError('PRIVATE path'))),
            ('archived', patch.dict(self.app.session, archived=True)),
            ('deleting', patch.dict(self.app.session, _deleting=True)),
            ('eligibility', patch.dict(self.app.session, sessionKind='internal')),
            ('busy', patch.dict(self.app.session, status='working')),
            ('sourcewitness', patch.dict(record, sourceKey='PRIVATE input')),
            ('sourcewitness', patch('amplifier_web.prompt_suggestions.witness', side_effect=OSError('PRIVATE file'))),
        ]
        for name, change in cases:
            with self.subTest(name=name), ExitStack() as stack:
                stack.enter_context(patch.object(self.app, '_session', return_value=self.app.session))
                stack.enter_context(change)
                self.assertFalse(original())
                self.assertEqual(self.host.current_failure(record), 'current_' + name)
                record.pop('_currentFailure', None)
                self.assertFalse(self.host.current(record))
                self.assertEqual(record['_currentFailure'], 'current_' + name)
                self.assertNotIn('PRIVATE', record['_currentFailure'])
        record.pop('_currentFailure', None)
        self.assertTrue(original())
        self.assertIsNone(self.host.current_failure(record))
        self.assertTrue(self.host.current(record))
        # First failed gate is stable, rather than overwritten by later ones.
        authenticated = record['authenticated']
        record['authenticated'] = lambda: False
        try:
            self.assertFalse(self.host.current(record))
        finally:
            record['authenticated'] = authenticated
        with patch.dict(self.app.session, status='working'):
            self.assertFalse(self.host.current(record))
        self.assertEqual(record['_currentFailure'], 'current_auth')

    async def test_status_and_capture_mismatch_publish_only_finite_stale_reason_without_retry(self):
        await self.request()
        await self.settle()
        self.app.session['messages'][-1]['text'] = 'Actually changed public input'
        result = await self.status()
        self.assertEqual((result['status'], result['reason']), ('stale', 'current_source_rows_text'))
        self.assertNotIn('_currentFailure', result)
        self.assertEqual(len(self.calls), 1)
        self.app.session['messages'][-1]['text'] = 'Next captured input'
        with patch('amplifier_web.prompt_suggestions.capture', AsyncMock(return_value=([], 'different-key'))):
            result = await self.terminal_request('capture-key')
        self.assertEqual((result['status'], result['reason']), ('stale', 'capture_source_changed'))
        self.assertEqual(len(self.calls), 1)

    async def test_private_source_components_classify_changes_without_altering_total_gate(self):
        from amplifier_web.suggestion_source import SOURCE_COMPONENTS
        await self.request()
        await self.settle()
        record = next(iter(self.host.records.values()))
        components = copy.deepcopy(record['_sourceComponents'])
        self.assertEqual(set(components), set(SOURCE_COMPONENTS))
        self.assertTrue(all(len(value) == 64 for value in components.values()))
        cases = [
            ('owner_native', self.app.session, {'nativeIdentity': 'SECRET native'}),
            ('runtime_session', self.app.session, {'runtimeSessionId': 'SECRET runtime'}),
            ('created', self.app.session, {'createdAt': 123}),
            ('stop', self.app.session, {'interruptionRevision': 1}),
            ('rows_id', self.app.session['messages'][-1], {'id': 'SECRET id'}),
            ('rows_count', self.app.session['messages'][-1], {'role': 'user'}),
            ('rows_text', self.app.session['messages'][-1], {'text': 'SECRET candidate'}),
            ('rows_actor', self.app.session['messages'][-1], {'inputActor': {'kind': 'agent', 'id': 'SECRET actor'}}),
            ('rows_label', self.app.session['messages'][-1], {'agentLabel': 'SECRET label'}),
        ]
        for name, target, values in cases:
            with self.subTest(name=name), patch.dict(target, values):
                self.assertNotEqual(witness(self.app, 'chat', self.config), record['sourceKey'])
                self.assertEqual(self.host.current_failure(record), 'current_source_' + name)
            self.assertIsNone(self.host.current_failure(record))
        with patch.dict(self.app.session, messages=[*self.app.session['messages'], {
                'id': 'append', 'role': 'assistant', 'text': 'SECRET append'}]):
            self.assertEqual(self.host.current_failure(record), 'current_source_rows_count')
        with patch.dict(self.app.session, status='ready', title='SECRET title', updatedAt=999,
                        usage={'output': 123}, nativeRevision=['invisible', 1]):
            self.assertEqual(witness(self.app, 'chat', self.config), record['sourceKey'])
            self.assertIsNone(self.host.current_failure(record))
        self.assertEqual(record['_sourceComponents'], components)  # Admission baseline never moves.
        self.app.session['messages'][-1]['text'] = 'SECRET changed'
        result = await self.status()
        self.assertEqual(result['reason'], 'current_source_rows_text')
        self.assertNotIn('SECRET', repr(result))
        self.assertNotIn('_sourceComponents', result)
        self.assertTrue(all(value not in repr(result) for value in components.values()))
        self.assertNotIn('components', repr(list(self.app.db.execute('SELECT * FROM suggestion_requests'))))
        self.host._expire(record)
        self.assertNotIn('_sourceComponents', record)

    async def test_final_grant_first_false_dimension_survives_later_successful_recheck(self):
        host = self.host
        del host.complete  # Exercise the real host-to-setup final-admission closure.
        async def probe(action, args, workspace):
            self.assertEqual(action, 'suggestion.complete')
            with patch.object(host, '_epoch', return_value='PRIVATE newer epoch'):
                self.assertFalse(args['_admit']())
            return {'status': 'stale', 'suggestion': None, 'usage': None, 'cost': None}
        host._manager = SimpleNamespace(probe=AsyncMock(side_effect=probe), close=AsyncMock())
        result = await self.terminal_request('grant')
        self.assertEqual((result['status'], result['reason']), ('stale', 'current_epoch'))
        self.assertNotIn('PRIVATE', repr(result))
        host._manager.probe.assert_awaited_once()

    async def test_request_owner_passes_saved_effort_and_prompt_to_isolated_probe(self):
        del self.host.complete
        self.config.update(effort='high', prompt='Saved literal {instruction}.')
        manager = SimpleNamespace(probe=AsyncMock(return_value={
            'status': 'empty', 'suggestion': None, 'usage': None, 'cost': None}), close=AsyncMock())
        self.host._manager = manager
        result = await self.terminal_request('saved-options')
        self.assertEqual(result['status'], 'empty')
        payload = manager.probe.call_args.args[1]
        self.assertEqual(payload['effort'], 'high')
        self.assertEqual(payload['prompt'], 'Saved literal {instruction}.')
        self.assertEqual(payload['model'], 'cheap-model')
        manager.probe.assert_awaited_once()

    async def test_invisible_terminal_metadata_and_generic_save_do_not_stale_resident_capture(self):
        windows = self.app.history.windows
        from amplifier_web.suggestion_source import capture
        async def read_then_account(*args, **kwargs):
            page = await capture(*args, **kwargs)
            # Same invalidation invoked by service._save and session.naming /
            # runtime.ended events; none of these changes the public input.
            windows.invalidate({'chat'})
            self.app.session.update(status='ready', title='Automatic title', usage={'input': 10})
            self.app.session['messages'].append({'id': 'checkpoint', 'role': 'system',
                                                'text': 'Private checkpoint', 'visibility': 'internal'})
            return page
        with patch('amplifier_web.prompt_suggestions.capture', side_effect=read_then_account):
            pending = await self.request()
            await self.settle()
        ready = await self.status()
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(ready['sourceKey'], pending['sourceKey'])
        self.assertEqual(len(self.calls), 1)
        self.app.session['messages'].append({'id': 'new-public', 'role': 'assistant', 'text': 'Actual append'})
        self.assertEqual((await self.status())['status'], 'stale')

    async def test_native_checkpoint_and_backup_stamps_revalidate_same_public_input_without_poll_scan(self):
        root = Path(self.tmp.name) / 'native'
        root.mkdir()
        path = root / 'transcript.jsonl'
        raw = [{'role': 'user', 'inputOrigin': 'ui', 'content': 'Human request'},
               {'role': 'assistant', 'content': 'Completed reply'}]
        path.write_text(''.join(json.dumps(row) + '\n' for row in raw))
        self.app.session.update(historyManaged=True, nativeProject='fixture', nativeIdentity='chat')
        windows = self.app.history.windows
        with patch('amplifier_web.automatic_history.directory', return_value=root), \
                patch('amplifier_web.transcript_windows._native_page', wraps=_native_page) as read:
            ordinary = await windows.read('chat', limit=100)
            last = ordinary['items'][-1]['id']
            pending = await self.request(expectedLastMessageId=last)
            await self.settle()
            self.assertEqual((await self.status())['status'], 'ready')
            baseline = read.call_count
            for _ in range(8):
                windows.invalidate({'chat'})  # Generic non-message publication.
                self.assertEqual((await self.status())['sourceKey'], pending['sourceKey'])
            self.assertEqual(read.call_count, baseline)
            (root / 'transcript.jsonl.backup').write_bytes(path.read_bytes())
            self.app.session.update(status='ready', nativeRevision=['checkpoint', 2])
            self.assertEqual((await self.status())['status'], 'ready')
            self.assertEqual(read.call_count, baseline + 1)
            with path.open('a') as stream:
                stream.write(json.dumps({'role': 'system', 'content': 'Private idle checkpoint',
                                         'metadata': {'private': True}}) + '\n')
            self.assertEqual((await self.status())['sourceKey'], pending['sourceKey'])
            self.assertEqual(read.call_count, baseline + 2)
            for _ in range(8):
                self.assertEqual((await self.status())['status'], 'ready')
            self.assertEqual(read.call_count, baseline + 2)
            raw[-1]['content'] = 'Real same-row edit'
            path.write_text(''.join(json.dumps(row) + '\n' for row in raw))
            self.assertEqual((await self.status())['status'], 'stale')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('Private', repr(self.calls))

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
        self.app._state = {'settings': {'suggestedMessages': True}, 'updates': {'phase': 'activating'}}
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
    def test_component_fingerprints_preserve_exact_total_witness_and_single_bounded_native_read(self):
        from amplifier_web.suggestion_settings import digest
        from amplifier_web.suggestion_source import changed_component, SOURCE_COMPONENTS
        from amplifier_web.transcript_windows import _source
        with tempfile.TemporaryDirectory() as root:
            app = Service(Path(root))
            try:
                config = {'_inputBudget': 1000}
                def old_resident():
                    session = app.session
                    owner = [_source(session), session.get('archived'), session.get('_deleting'),
                             session.get('status') in {'idle', 'ready'}, session.get('interruptionRevision')]
                    return digest([owner, ('assistant', 'assistant'),
                                   select(public_rows(session['messages']), config['_inputBudget'])])
                baseline = {}
                self.assertEqual(witness(app, 'chat', config, components=baseline), old_resident())
                self.assertEqual(witness(app, 'chat', config), old_resident())
                self.assertEqual(len(baseline), 16)
                for key in SOURCE_COMPONENTS:
                    current = {**baseline, key: 'SECRET HASH'}
                    self.assertEqual(changed_component(baseline, current), 'current_source_' + key)
                self.assertEqual(changed_component(None, {'SECRET': 'value'}), 'current_sourcewitness')
                self.assertEqual(changed_component(baseline, {**baseline, 'SECRET': 'value'}), 'current_sourcewitness')
                app.session.update(historyManaged=True, nativeIdentity='native', nativeProject='project')
                page = {'items': copy.deepcopy(app.session['messages']), 'hasEarlier': False, 'unavailableRanges': []}
                with patch.object(app.history.windows, 'public_native_tail', return_value=page) as read:
                    observed = {}
                    total = witness(app, 'chat', config, components=observed)
                read.assert_called_once_with(app.session)
                self.assertEqual(total, old_resident())  # Same total owner/row expression as before.
                self.assertNotEqual(observed['storage_mode'], baseline['storage_mode'])
                # A fingerprint map is never used as an authority substitute.
                self.assertEqual(len(observed), 16)
                self.assertNotIn('Human', repr(observed))
            finally:
                app.db.close()

    def test_sanitizes_closed_and_unclosed_host_envelopes_payloads_and_nonhuman_inputs(self):
        rows = select(public_rows([
            {'id': '1', 'role': 'user', 'inputOrigin': 'ui', 'text': '<system-reminders>PRIVATE</system-reminders> Human'},
            {'id': '2', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Visible <context_file path="x">FILE'},
            {'id': '3', 'role': 'user', 'text': 'data:image/png;base64,PRIVATE', 'inputOrigin': 'approval'},
            {'id': '4', 'role': 'user', 'text': 'Scheduled', 'scheduledRunId': 'run'},
            {'id': '5', 'role': 'assistant', 'sourceMessageId': '2', 'text': 'Reply data:image/png;base64,PRIVATE'},
        ]), 1000)
        self.assertEqual(rows[0]['text'].strip(), 'Human')
        self.assertEqual(rows[1]['text'].strip(), 'Visible')
        self.assertNotIn('PRIVATE', repr(rows))
        self.assertEqual([row['id'] for row in rows], ['1', '2', '5'])

    def test_private_roles_delivery_recovery_and_labels(self):
        rows = [
            {'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Public human'},
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

    def test_latest_whole_reply_is_never_replaced_by_older_exchange(self):
        rows = [{'id': str(i), 'role': role, 'text': text} for i, (role, text) in enumerate([
            ('user', 'Older input'), ('assistant', 'Older reply'), ('user', 'Newest input'),
            ('assistant', 'x' * 25000 + '\nExact final line.')])]
        self.assertEqual(select(rows, 100), [])
        self.assertEqual(select(rows, 8365), rows[-2:])

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
                assert not isinstance(key, slice)
                assert key == -1 or key >= len(self) - 512
                return super().__getitem__(key)
        with tempfile.TemporaryDirectory() as root:
            app = Service(Path(root))
            app.session['messages'] = Tail(
                [{'id': str(i), 'role': 'user', 'inputOrigin': 'ui', 'text': 'x'} for i in range(35999)]
                + [{'id': 'last', 'role': 'assistant', 'text': 'answer'}])
            first = witness(app, 'chat')
            app.session['messages'][-1]['text'] = 'changed same id'
            self.assertNotEqual(first, witness(app, 'chat'))


class Provider(unittest.IsolatedAsyncioTestCase):
    async def test_one_tool_free_explicit_bounded_completion_and_reported_usage(self):
        provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(type='text', text='{"suggestion":"Please continue."}')],
            usage={'input_tokens': 10, 'output_tokens': 6})))
        result = await complete(provider, {}, 'explicit', 'Human:\ntest\n\nAssistant:\nreply', {})
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
        self.assertIsNone(provider.complete.call_args.kwargs['reasoning_effort'])
        self.assertIsNone(request.reasoning_effort)
        self.assertNotIn('extended_thinking', provider.complete.call_args.kwargs)
        self.assertEqual(request.messages[0].content, INSTRUCTION + '\n\n' + OUTPUT_CONTRACT)
        self.assertEqual(request.messages[1].content, 'Human:\ntest\n\nAssistant:\nreply')

    async def test_saved_prompt_is_literal_fixed_contract_separate_and_effort_uses_portable_options(self):
        prompt = 'Experiment with {transcript} and ${ENV}: prefer one concrete question.\nKeep this literal.'
        for effort in ('low', 'high', None):
            provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
                content=[SimpleNamespace(type='text', text='{"suggestion":null}')], usage=None)))
            result = await complete(provider, {'reasoning_effort': 'chat-default'}, 'explicit', [],
                                    {}, effort=effort, prompt=prompt)
            self.assertEqual(result['status'], 'empty')
            request = provider.complete.call_args.args[0]
            self.assertEqual(request.reasoning_effort, effort)
            self.assertEqual(provider.complete.call_args.kwargs, {'model': 'explicit', 'reasoning_effort': effort})
            self.assertEqual(request.messages[0].content, prompt + '\n\n' + OUTPUT_CONTRACT)
            self.assertEqual(request.max_output_tokens, 512)
            self.assertEqual(request.timeout, 15)
            self.assertFalse(request.tools)
            provider.complete.assert_awaited_once()

    async def test_query_constructs_both_adapter_shapes_with_utility_effort_not_saved_chat_defaults(self):
        from amplifier_web.provider_probe import query
        seen = []
        class OrdinaryAdapter:
            def __init__(self, config=None):
                self.config = config or {}
            def get_info(self):
                return {'defaults': {'model': self.config.get('default_model')}}
            def get_config_schema(self):
                return {'fields': []}
            async def complete(self, request, **kwargs):
                seen.append((copy.deepcopy(self.config), request, kwargs))
                return SimpleNamespace(content=[SimpleNamespace(type='text', text='{"suggestion":null}')])
        class KeywordOptionsAdapter(OrdinaryAdapter):
            async def complete(self, request, *, request_options=None, **kwargs):
                self.assertion = request_options
                return await super().complete(request, **kwargs)
        saved = {'api_key': 'synthetic-key', 'default_model': 'foreground-model',
                 'fallback_state_path': '',
                 'reasoning_effort': 'foreground-effort', 'effort': 'legacy-effort',
                 'reasoning': {'effort': 'legacy-reasoning'}, 'extended_thinking': False,
                 'thinking_type': 'enabled', 'thinking_budget_tokens': 9999,
                 'extra_request_params': {'model': 'other-model', 'max_tokens': 99999},
                 'raw': {'reasoning': {'effort': 'max'}},
                 'extra_body': {'max_output_tokens': 99999, 'tools': [{'name': 'not-admitted'}]}}
        before = copy.deepcopy(saved)
        for cls in (OrdinaryAdapter, KeywordOptionsAdapter):
            for effort in ('low', 'high', None):
                with patch('amplifier_web.provider_probe.provider_class', return_value=cls), \
                        patch('amplifier_web.provider_state_files.state_file_defaults', return_value={
                            'rate_limit_state_path': '/synthetic-shared-home/rate-limit-state.json',
                            'fallback_state_path': '/synthetic-shared-home/anthropic-fallback-state.json'}):
                    result = await query({'action': 'suggestion.complete', 'module': 'fixture',
                        'config': saved, 'model': 'explicit-selected', 'effort': effort,
                        'prompt': 'Experiment prompt.', 'transcript': [], '_admit': AsyncMock(return_value=True)})
                self.assertEqual(result['suggestion']['status'], 'empty')
                isolated, request, options = seen[-1]
                self.assertEqual(isolated['reasoning_effort'], effort)
                self.assertEqual(isolated['default_model'], 'explicit-selected')
                self.assertEqual(isolated['model'], 'explicit-selected')
                # Current host routing and explicit file opt-outs survive the
                # utility's isolated model/effort/retry configuration.
                self.assertEqual(isolated['rate_limit_state_path'],
                                 '/synthetic-shared-home/rate-limit-state.json')
                self.assertEqual(isolated['fallback_state_path'], '')
                self.assertEqual(request.reasoning_effort, effort)
                self.assertEqual(options, {'model': 'explicit-selected', 'reasoning_effort': effort})
                for key in ('effort', 'reasoning', 'extended_thinking', 'thinking_type', 'thinking_budget_tokens',
                            'extra_request_params', 'raw', 'extra_body'):
                    self.assertNotIn(key, isolated)
                self.assertEqual(isolated['max_retries'], 0)
                self.assertFalse(isolated['fallback_on_overload'])
        self.assertEqual(saved, before)
        self.assertEqual(len(seen), 6)  # One completion per requested attempt, never a repair.

    def test_utility_request_overrides_do_not_remove_connection_transport_or_mutate_saved_settings(self):
        from amplifier_web.suggestion_provider import utility_config
        saved = {
            'api_key': '${SAVED_CONNECTION_KEY}', 'base_url': 'https://connection.invalid/api',
            'default_headers': {'X-Transport': 'preserved'}, 'timeout': 120,
            'extra_request_params': {'model': 'other-model', 'reasoning_effort': 'max', 'max_tokens': 100000},
            'extra_body': {'tools': [{'name': 'unrequested'}]}, 'raw': True}
        original = copy.deepcopy(saved)
        for effort in ('low', 'high', None):
            isolated = utility_config(saved, 'selected-model', effort)
            for key in ('extra_request_params', 'extra_body', 'raw'):
                self.assertNotIn(key, isolated)
            for key in ('api_key', 'base_url', 'default_headers', 'timeout'):
                self.assertEqual(isolated[key], original[key])
            self.assertEqual(isolated['model'], 'selected-model')
            self.assertEqual(isolated['reasoning_effort'], effort)
            self.assertEqual(saved, original)

    async def test_invalid_instruction_options_refuse_before_any_provider_call(self):
        provider = SimpleNamespace(complete=AsyncMock())
        for effort, prompt in ((None, ''), (None, ' \n'), (None, 'é' * 8193),
                              (None, None), (None, 'NUL\0control'), (None, '\ud800'),
                              ('bad effort', INSTRUCTION), ('x' * 101, INSTRUCTION)):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                await complete(provider, {}, 'explicit', [], {}, effort=effort, prompt=prompt)
        provider.complete.assert_not_called()

    async def test_known_reasoning_blocks_are_never_read_or_projected_but_tools_still_refuse(self):
        class PrivateReasoning:
            def __init__(self, kind):
                self.type = kind
            @property
            def text(self):
                raise AssertionError('Private reasoning must never be read')
        text = SimpleNamespace(type='text', text='{"suggestion":"One public question?"}')
        for effort in ('low', 'high', None):
            reasoning = [PrivateReasoning(kind) for kind in ('thinking', 'redacted_thinking', 'reasoning')]
            provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
                content=[*reasoning, text], usage={'input_tokens': 10, 'output_tokens': 12})))
            result = await complete(provider, {}, 'explicit', [], {}, effort=effort)
            self.assertEqual(result['suggestion'], 'One public question?')
            self.assertNotIn('PrivateReasoning', repr(result))
            provider.complete.assert_awaited_once()
        for blocks in ([PrivateReasoning('thinking')], [text, SimpleNamespace(type='tool_call')],
                       [text, SimpleNamespace(type='unknown_action')]):
            provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=blocks)))
            with self.assertRaises(ValueError):
                await complete(provider, {}, 'explicit', [], {}, effort='high')
            provider.complete.assert_awaited_once()

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
                ('KernelInvalidRequestError', None, 'transport_invalid_request'),
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
                'id': 'saved-account', 'model': 'cheap-explicit', 'effort': 'low',
                'prompt': 'Saved {prompt}.', 'transcript': [], '_admit': lambda: True},
                str(self.root))
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(spawn.call_args.args[0], str(self.executable))
        self.assertEqual(Path(spawn.call_args.args[1]).name, 'provider_probe.py')
        payload = json.loads(process.stdin.write.call_args.args[0])
        self.assertIsNone(payload['source'])  # Never install the authored branch per request.
        self.assertEqual(payload['model'], 'cheap-explicit')
        self.assertEqual(payload['effort'], 'low')
        self.assertEqual(payload['prompt'], 'Saved {prompt}.')
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
    async def test_existing_paid_claim_does_not_block_untouched_older_chat_after_explicit_consent(self):
        from amplifier_web.service import AppService
        from amplifier_web.suggestion_settings import digest
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(
            config={'providers': [{'id': 'saved', 'module': 'provider-openai',
                                   'config': {'api_key': 'synthetic'}}]},
            prompt_suggestions={'enabled': True, 'transcriptMode': 'full', 'humanTurns': 6,
                                'provider': 'saved', 'model': 'exact', 'effort': None, 'prompt': 'Keep me.'}))
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, messages=[
            {'id': 'user-before-upgrade', 'role': 'user', 'text': 'An old question.', 'inputOrigin': 'ui'},
            {'id': 'old-reply', 'role': 'assistant', 'text': 'An old answer.'}])
        self.app._publish_changes(sessions={sid})
        await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}}, include_state=False)
        self.app.clients.attach('upgrade-browser', kind='web')
        device = self.app.clients.records['upgrade-browser'].get('deviceOwner', 'local-owner')
        audience = digest(['cookie', device, 'upgrade-browser'])
        old_identity = digest([audience, sid, 'old-request'])
        self.app.db.execute('INSERT INTO suggestion_requests VALUES (?,?)', (old_identity, 'prior-paid-hash'))
        self.app.db.commit()
        await self.app.close()
        self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)
        host = self.app.prompt_suggestions
        manager = host.manager
        host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'Please continue.',
                                               'usage': None, 'cost': None})
        self.app.clients.attach('upgrade-browser', kind='web')
        self.app.clients.records['upgrade-browser']['selectedSessionId'] = sid
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        authority = {'scope': 'global', 'configurationTarget': {'kind': 'global'}}
        async def save(**change):
            return (await self.app.dispatch('providers.configureSuggestions', {**authority, **change},
                                            include_state=False))['result']['suggestionModels']
        async def request(name):
            args = {'sessionId': sid, 'requestId': name}
            await host.dispatch('session.suggestion.request', {**args, 'expectedLastMessageId': 'old-reply'}, 'ui')
            await asyncio.gather(*tuple(host.jobs))
            return await host.dispatch('session.suggestion.status', args, 'ui')
        try:
            with self.app.clients.bind('upgrade-browser'):
                config = describe(manager, self.root)
                self.assertEqual((config['enabled'], config['model'], config['prompt']), (False, 'exact', 'Keep me.'))
                self.assertNotIn('transcriptMode', config)
                self.assertNotIn('humanTurns', config)
                self.assertTrue(self.app.state['settings']['suggestedMessages'])
                self.assertEqual((await request('before-consent'))['status'], 'disabled')
                before = manager.store.path(self.root, 'global').read_bytes()
                with self.assertRaisesRegex(Exception, 'confirm recent conversation sharing'):
                    await save(enabled=True, expectedSuggestionModelsRevision=config['revision'])
                self.assertEqual(manager.store.path(self.root, 'global').read_bytes(), before)
                self.assertFalse((await save(prompt='Only change instructions.'))['enabled'])
                with self.assertRaises(Exception):
                    await save(enabled=True, shareRecentConversation=True,
                               expectedSuggestionModelsRevision=config['revision'])
                self.assertTrue((await save(enabled=True, shareRecentConversation=True,
                    expectedSuggestionModelsRevision=describe(manager, self.root)['revision']))['enabled'])
                self.assertEqual((await request('old-request'))['status'], 'expired')
                host.complete.assert_not_called()
                self.assertEqual((await request('new-consent-older-chat'))['status'], 'ready')
                self.assertTrue((await request('revisit'))['reused'])
                host.complete.assert_awaited_once()
                self.assertEqual(self.app.db.execute(
                    'SELECT source FROM suggestion_requests WHERE identity=?', (old_identity,)).fetchone(),
                    ('prior-paid-hash',))
        finally:
            BROWSER.reset(token)

    async def test_peer_heavy_cold_snapshot_uses_exact_selected_context_and_latest_whole_reply(self):
        from amplifier_web.provider_catalog import model_key
        from amplifier_web.suggestion_provider import estimate_tokens
        host = self.app.prompt_suggestions
        manager = host.manager
        manager.store.update(self.root, 'global', lambda settings: settings.update(config={'providers': [
            {'id': account, 'module': 'provider-openai', 'config': {'api_key': 'synthetic-' + account}}
            for account in ('saved', 'other')]}))
        configure(manager, {'enabled': True, 'shareRecentConversation': True, 'provider': 'saved',
                            'model': 'utility'}, self.root, 'global')
        key = model_key(manager.catalog_key({'id': 'saved'}, str(self.app.data_dir)))
        other = model_key(manager.catalog_key({'id': 'other'}, str(self.app.data_dir)))
        def catalog(which, window):
            manager.catalog.put(which, {'models': [{'id': 'utility', 'context_window': window}],
                'modelsSupported': True, 'modelsProvenance': 'provider-api',
                'providerMetadata': {'module': 'provider-openai'}})
        catalog(other, 500_000)
        catalog(key, 128_000)

        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        prefix = [{'id': f'old-{i}', 'role': 'tool', 'text': 'Old tool output'} for i in range(530)]
        exchange = []
        for i in range(6):
            exchange.extend([
                {'id': f'ask-{i}', 'role': 'user', 'via': 'chat', 'inputOrigin': 'ui',
                 'inputActor': {'kind': 'human', 'id': 'PRIVATE-ID'}, 'createdAt': 10 + i,
                 'text': f'Question {i}: what is the next step?'},
                {'id': f'reply-{i}', 'role': 'assistant', 'source': 'amplifier', 'via': 'chat',
                 'agentLabel': 'PRIVATE-LABEL', 'createdAt': 20 + i,
                 'text': f'Reply {i}:\n' + 'Review the staged results before continuing. ' * 1500
                         + f'\nComplete final line {i}.'}])
        noise = []
        for i in range(46):
            noise.extend([
                {'id': f'peer-{i}', 'inputId': f'peer-input-{i}', 'role': 'user', 'inputOrigin': 'agent',
                 'text': 'PEER TASK PAYLOAD', 'peerEnvelope': {'senderSessionId': 'other-chat'}},
                {'id': f'peer-reply-{i}', 'inputId': f'peer-input-{i}', 'role': 'assistant',
                 'text': 'PEER-ONLY REPLY'},
                {'id': f'tool-{i}', 'role': 'tool', 'text': 'TOOL PAYLOAD'}])
        # Retirement must not disconnect any borrowed nested mutable rows.
        session.update(status='idle', historyManaged=False, messages=copy.deepcopy(prefix + exchange[:10] + noise + exchange[10:]))
        self.app._publish_changes(sessions={sid})
        self.app._state['selectedSessionId'] = None
        with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
            self.assertIn(sid, self.app.cold_display.retire(force=True))
        self.assertFalse(dict.__contains__(session, 'messages'))
        self.app._state['settings']['suggestedMessages'] = True
        self.app.clients.attach('plain-browser', kind='web')
        self.app.clients.records['plain-browser']['selectedSessionId'] = sid
        candidate = {'status': 'ready', 'suggestion': 'Please check the next result.', 'usage': None, 'cost': None}
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        async def request(name):
            args = {'sessionId': sid, 'requestId': name}
            await host.dispatch('session.suggestion.request', {**args, 'expectedLastMessageId': 'reply-5'}, 'ui')
            await asyncio.gather(*tuple(host.jobs))
            return await host.dispatch('session.suggestion.status', args, 'ui')
        try:
            with self.app.clients.bind('plain-browser'), \
                    patch.object(manager, 'discover_catalog', AsyncMock(side_effect=AssertionError('no discovery'))), \
                    patch.object(manager, 'probe', AsyncMock(return_value=candidate)) as probe:
                result = await request('large-window')
                self.assertEqual(result['status'], 'ready')
                probe.assert_awaited_once()
                action, payload, _ = probe.call_args.args
                self.assertEqual((action, payload['id'], payload['model']), ('suggestion.complete', 'saved', 'utility'))
                transcript = payload['transcript']
                self.assertEqual(transcript, '\n\n'.join(
                    ('Human' if row['role'] == 'user' else 'Assistant') + ':\n' + row['text']
                    for row in exchange[-8:]))
                self.assertGreater(estimate_tokens(transcript), 85_000)
                self.assertLess(estimate_tokens(transcript), 100_000)
                self.assertFalse(dict.__contains__(session, 'messages'))
                self.assertTrue((await request('same-source'))['reused'])
                probe.assert_awaited_once()

                catalog(key, 32_000)  # One whole exchange fits; the foreign account cannot donate its window.
                self.assertEqual((await request('smaller-same-consent'))['status'], 'duplicate_source')
                probe.assert_awaited_once()
                configure(manager, {'prompt': 'Offer one next action.'}, self.root, 'global')
                self.assertEqual((await request('small-window-new-intent'))['status'], 'ready')
                self.assertEqual(probe.await_count, 2)
                self.assertEqual(probe.call_args.args[1]['transcript'],
                                 'Human:\n' + exchange[-2]['text'] + '\n\nAssistant:\n' + exchange[-1]['text'])

                catalog(key, 4_000)  # Never truncate or send the older exchange instead.
                result = await request('unfit-window')
                self.assertEqual((result['status'], result['reason'], result['generationId']), ('empty', None, None))
                self.assertEqual(probe.await_count, 2)
        finally:
            BROWSER.reset(token)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AMPLIFIER_HOME': str(self.root / 'shared'),
                                          'AMPLIFIER_WEB_HOME': str(self.root / 'app')})
        self.env.start()
        from amplifier_web.service import AppService
        self.runtime = SimpleNamespace(close=AsyncMock(), start=AsyncMock(), send=AsyncMock())
        self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)
        # Existing utility cases qualify model behavior with owner opt-in.
        # Default-off and failed-save semantics have separate real-service cases.
        await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}}, include_state=False)

    async def asyncTearDown(self):
        await self.app.close()
        self.env.stop()
        self.tmp.cleanup()

    async def shared_migration_fixture(self, identity):
        """Real UI admission/capture/receipt; no ACP process or model starts."""
        from amplifier_web.shared_conversation import Participation
        self.app.server_config = {'externalAgents': [
            {'id': agent, 'label': agent.title(), 'command': sys.executable,
             'args': ['-c', 'raise AssertionError("offline observer fixture must not start ACP")']}
            for agent in ('alpha', 'beta')]}
        result = await self.app.dispatch('session.create', {
            'workspace': str(self.root), 'externalAgentIds': ['alpha', 'beta']},
            command_id='room-' + identity, include_state=False)
        session = self.app._session(result['result']['sessionId'])
        shared = self.app.external_conversations.shared
        floor = self.app.db.execute('SELECT COALESCE(MAX(rowid),0) FROM commands').fetchone()[0]
        self.app.db.execute('UPDATE suggestion_cache_policy SET legacy=1,command_floor=?', (floor,))
        self.app.db.execute('INSERT OR IGNORE INTO suggestion_requests VALUES (?,?)',
                            ('legacy-unknown', 'old-unprojectable-paid-claim'))
        self.app.db.commit()
        with patch.object(shared, '_schedule') as schedule:
            receipt = await self.app.dispatch('conversation.send', {
                'sessionId': session['id'], 'text': 'Direct new human work'},
                command_id=identity, include_state=False)
        schedule.assert_called_once()
        self.assertEqual(receipt['delivery'], 'accepted')
        source = shared.message(session, next(row['sourceMessageId'] for row in shared.room(session)['deliveries']
                                             if row['inputId'] == identity))
        delivery = next(row for row in shared.room(session)['deliveries']
                        if row['sourceMessageId'] == source['id'] and row['agentId'] == 'alpha')
        delivery['state'] = 'sending'
        turn = Participation(delivery['id'], identity, 'alpha', session['interruptionRevision'],
                             SimpleNamespace(close=AsyncMock()), attempted=True, speaking=True)
        shared.turns[(session['id'], 'alpha')] = turn
        session['externalStreams'] = {'alpha': {'text': 'Direct completed shared reply.'}}
        self.app.prompt_suggestions.complete = AsyncMock(side_effect=AssertionError('no inference'))
        return session, shared, turn, delivery, source, floor

    async def test_shared_direct_ui_completion_migration_preserves_dto_peer_ids_floor_and_claims(self):
        from amplifier_web.suggestion_settings import digest
        for label in (None, 'action', 'commandAction'):
            with self.subTest(label=label):
                session, shared, turn, delivery, source, floor = await self.shared_migration_fixture(
                    'direct-' + str(label))
                sid = session['id']
                raw = self.app.db.execute('SELECT receipt FROM commands WHERE id=?', (turn.input_id,)).fetchone()[0]
                receipt = json.loads(raw)
                receipt.pop('action', None)
                receipt.pop('commandAction', None)
                if label:
                    receipt[label] = 'conversation.send'
                self.app.db.execute('UPDATE commands SET receipt=? WHERE id=?',
                                    (json.dumps(receipt), turn.input_id))
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                source_before = copy.deepcopy(source)
                try:
                    with patch.object(self.app, '_message', wraps=self.app._message) as insertion:
                        async with self.app.lock:
                            shared._settle(session, turn, 'accepted', publish=True)
                    insertion.assert_called_once_with(session, 'assistant', 'Direct completed shared reply.',
                        source='external', agentId='alpha', agentLabel='Alpha', sourceMessageId=source['id'])
                    message = session['messages'][-1]
                    self.assertEqual(set(message), {
                        'id', 'role', 'text', 'via', 'createdAt', 'source',
                        'agentId', 'agentLabel', 'sourceMessageId', 'roomSequence'})
                    self.assertNotIn('inputId', message)
                    self.assertEqual(source, source_before)
                    peer, = [row for row in shared.room(session)['deliveries']
                             if row['sourceMessageId'] == message['id']]
                    self.assertEqual(peer['agentId'], 'beta')
                    self.assertEqual(peer['inputId'], message['id'])
                    self.assertNotEqual(peer['inputId'], turn.input_id)
                    proof = self.app.db.execute('SELECT message_key FROM suggestion_fresh_completion WHERE session_id=?',
                                                (sid,)).fetchone()
                    self.assertEqual(proof, (digest(message['id']),))
                    host = self.app.prompt_suggestions
                    self.assertTrue(host._migration_allows({'sessionId': sid}, [message]))
                    self.assertFalse(host._migration_allows({'sessionId': sid}, [{'id': 'other-assistant'}]))
                    self.assertEqual(self.app.db.execute(
                        'SELECT legacy,command_floor FROM suggestion_cache_policy').fetchone(), (1, floor))
                    self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                    host.complete.assert_not_called()
                finally:
                    shared.turns.pop((sid, 'alpha'), None)

    async def shared_migration_refusal(self, fault):
        session, shared, turn, delivery, source, floor = await self.shared_migration_fixture(fault)
        sid = session['id']
        receipt = json.loads(self.app.db.execute('SELECT receipt FROM commands WHERE id=?',
                                                (turn.input_id,)).fetchone()[0])
        state, publish = 'accepted', True
        if fault == 'peer-source':
            source.update(role='assistant', agentId='beta', inputOrigin='agent',
                          sourceMessageId='earlier-human')
        elif fault == 'agent-source': source['inputOrigin'] = 'agent'
        elif fault == 'unknown-source': source.pop('inputOrigin')
        elif fault == 'contradictory-actor': source['inputActor'] = {'kind': 'agent'}
        elif fault == 'partial-source': source['partial'] = True
        elif fault == 'private-source': source['metadata'] = {'private': True}
        elif fault == 'missing-source': session['messages'].remove(source)
        elif fault == 'ambiguous-source': session['messages'].append(copy.deepcopy(source))
        elif fault == 'ambiguous-input':
            session['messages'].append({**source, 'id': 'different-display-same-input'})
        elif fault == 'pruned-source':
            session['messages'].extend({'id': 'later-' + str(i), 'role': 'assistant', 'text': 'Later'}
                                       for i in range(100))
        elif fault == 'partial-result': publish = False
        elif fault == 'unknown-result': state, publish = 'unknown', False
        elif fault == 'silence': session['externalStreams']['alpha']['text'] = '[[PASS]]'
        elif fault == 'stopped-turn': turn.stop = True
        elif fault == 'replaced-turn': shared.turns[(sid, 'alpha')] = copy.copy(turn)
        elif fault == 'source-stop': source['interruptionRevision'] += 1
        elif fault == 'delivery-stop': delivery['interruptionRevision'] += 1
        elif fault == 'delivery-input': delivery['inputId'] = 'other-input'
        elif fault == 'delivery-agent': delivery['agentId'] = 'beta'
        elif fault == 'turn-input': turn.input_id = 'other-input'
        elif fault == 'missing-receipt':
            self.app.db.execute('DELETE FROM commands WHERE id=?', (turn.input_id,))
        elif fault == 'malformed-receipt':
            self.app.db.execute('UPDATE commands SET receipt=? WHERE id=?', ('[]', turn.input_id))
        elif fault == 'at-floor':
            floor = self.app.db.execute('SELECT rowid FROM commands WHERE id=?', (turn.input_id,)).fetchone()[0]
            self.app.db.execute('UPDATE suggestion_cache_policy SET command_floor=?', (floor,))
        else:
            if fault in {'action', 'commandAction'}: receipt[fault] = 'coordination.send'
            elif fault == 'wrong-session': receipt['sessionId'] = 'elsewhere'
            elif fault == 'wrong-input': receipt['inputId'] = 'elsewhere'
            elif fault == 'unknown-receipt': receipt['delivery'] = 'unknown'
            elif fault == 'rejected-receipt': receipt['accepted'] = False
            elif fault == 'error-receipt': receipt['error'] = 'unconfirmed'
            elif fault == 'status-receipt': receipt['status'] = 500
            elif fault == 'missing-navigation': receipt.pop('navigationPost')
            elif fault == 'wrong-fence': receipt['navigationPost']['fence'] = 'another-fence'
            elif fault == 'rejected-navigation': receipt['navigationPost']['disposition'] = 'rejected'
            else: self.fail('Unknown mutation ' + fault)
            self.app.db.execute('UPDATE commands SET receipt=? WHERE id=?',
                                (json.dumps(receipt), turn.input_id))
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        try:
            async with self.app.lock:
                shared._settle(session, turn, state, publish=publish)
            self.assertIsNone(self.app.db.execute(
                'SELECT 1 FROM suggestion_fresh_completion WHERE session_id=?', (sid,)).fetchone())
            self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
            self.assertEqual(self.app.db.execute(
                'SELECT legacy,command_floor FROM suggestion_cache_policy').fetchone(), (1, floor))
            self.app.prompt_suggestions.complete.assert_not_called()
            self.assertTrue(all('inputId' not in row for row in session['messages']
                                if row.get('source') == 'external'))
        finally:
            shared.turns.pop((sid, 'alpha'), None)

    async def test_shared_migration_direct_human_provenance_and_bounded_source_fail_closed(self):
        for fault in ('peer-source', 'agent-source', 'unknown-source', 'contradictory-actor',
                      'partial-source', 'private-source', 'missing-source', 'ambiguous-source',
                      'ambiguous-input', 'pruned-source'):
            with self.subTest(fault=fault):
                await self.shared_migration_refusal(fault)

    async def test_shared_migration_requires_exact_accepted_receipt_floor_and_navigation_fence(self):
        for fault in ('missing-receipt', 'malformed-receipt', 'at-floor', 'action', 'commandAction',
                      'wrong-session', 'wrong-input', 'unknown-receipt', 'rejected-receipt',
                      'error-receipt', 'status-receipt', 'missing-navigation', 'wrong-fence', 'rejected-navigation'):
            with self.subTest(fault=fault):
                await self.shared_migration_refusal(fault)

    async def test_shared_migration_partial_unknown_silence_and_changed_turn_never_prove_completion(self):
        for fault in ('partial-result', 'unknown-result', 'silence', 'stopped-turn', 'replaced-turn',
                      'source-stop', 'delivery-stop', 'delivery-input', 'delivery-agent', 'turn-input'):
            with self.subTest(fault=fault):
                await self.shared_migration_refusal(fault)

    async def test_shared_run_acceptance_survives_optional_lookup_insert_and_trim_failures(self):
        """Real _run/end_turn/settlement and SQLite; only ACP/model is synthetic."""
        for fault in ('lookup', 'insert', 'trim'):
            with self.subTest(fault=fault):
                session, shared, turn, delivery, source, floor = await self.shared_migration_fixture(
                    'optional-' + fault)
                sid = session['id']
                db = self.app.db
                db.execute('DELETE FROM suggestion_fresh_completion')
                db.executemany('INSERT INTO suggestion_fresh_completion VALUES (?,?,?)',
                               [('older-' + str(i), 'old-proof-' + str(i), i) for i in range(128)])
                db.execute('CREATE TABLE IF NOT EXISTS suggestion_test_host_write (id TEXT PRIMARY KEY, message TEXT)')
                db.commit()
                proofs = list(db.execute('SELECT * FROM suggestion_fresh_completion ORDER BY session_id'))
                claims = list(db.execute('SELECT * FROM suggestion_requests'))
                source_before = copy.deepcopy(source)
                sequence = shared.room(session)['sequence']
                denied = []
                if fault == 'lookup':
                    def authorize(action, table, column, database, origin):
                        if action == sqlite3.SQLITE_READ and table == 'suggestion_cache_policy':
                            denied.append(table)
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK
                    db.set_authorizer(authorize)
                else:
                    operation = 'INSERT' if fault == 'insert' else 'DELETE'
                    db.create_function('suggestion_optional_failure_seen', 0,
                                       lambda: denied.append(fault) or 0)
                    db.execute('CREATE TEMP TRIGGER suggestion_optional_failure BEFORE ' + operation +
                               " ON suggestion_fresh_completion BEGIN SELECT suggestion_optional_failure_seen();"
                               " SELECT RAISE(ABORT, 'optional proof unavailable'); END")

                async def respond(*args, **kwargs):
                    # _run observes a valid end_turn, not a direct _settle test.
                    return SimpleNamespace(stop_reason='end_turn')
                participant = SimpleNamespace(
                    connect=AsyncMock(), attention=AsyncMock(return_value=None),
                    source_metadata=Mock(return_value={}), prompt=AsyncMock(side_effect=respond),
                    close=AsyncMock())
                turn.participant = participant
                original_message, original_capture = self.app._message, shared.capture
                def append_message(*args, **kwargs):
                    message = original_message(*args, **kwargs)
                    # Stand in for another canonical write in this same host
                    # transaction. Observer rollback must not remove it.
                    db.execute('INSERT INTO suggestion_test_host_write VALUES (?,?)', (sid, message['id']))
                    return message
                def capture_reply(current, message, mention=None):
                    # Releasing the optional savepoint must not commit the
                    # surrounding host write ahead of canonical publication.
                    with sqlite3.connect(self.root / 'app' / 'app.sqlite3') as other:
                        self.assertIsNone(other.execute(
                            'SELECT message FROM suggestion_test_host_write WHERE id=?', (sid,)).fetchone())
                    return original_capture(current, message, mention)
                try:
                    with patch.object(shared, '_schedule') as schedule, \
                            patch.object(self.app, '_message', side_effect=append_message) as insertion, \
                            patch.object(shared, 'capture', side_effect=capture_reply) as capture:
                        await shared._run(sid, turn)
                    if fault == 'lookup':
                        self.assertTrue(denied)
                    else:
                        self.assertEqual(denied, [fault], 'The intended optional SQL boundary must actually fail.')
                    self.assertEqual(delivery['state'], 'accepted')
                    self.assertEqual(shared.participant(session, 'alpha')['lastReceipt']['state'], 'accepted')
                    self.assertEqual(shared.participant(session, 'alpha')['status'], 'idle')
                    self.assertFalse(turn.stop or turn.retiring)
                    participant.prompt.assert_awaited_once()
                    participant.close.assert_not_called()
                    insertion.assert_called_once()
                    capture.assert_called_once()
                    schedule.assert_called_once()  # No real peer execution.
                    messages = [row for row in session['messages'] if row.get('source') == 'external']
                    self.assertEqual(len(messages), 1)
                    message = messages[0]
                    self.assertEqual(message['roomSequence'], sequence + 1)
                    self.assertNotIn('inputId', message)
                    self.assertFalse(message.get('partial'))
                    peer, = [row for row in shared.room(session)['deliveries']
                             if row['sourceMessageId'] == message['id']]
                    self.assertEqual((peer['agentId'], peer['inputId']), ('beta', message['id']))
                    self.assertEqual(source, source_before)
                    self.assertEqual(list(db.execute(
                        'SELECT * FROM suggestion_fresh_completion ORDER BY session_id')), proofs)
                    self.assertEqual(list(db.execute('SELECT * FROM suggestion_requests')), claims)
                    with sqlite3.connect(self.root / 'app' / 'app.sqlite3') as other:
                        self.assertEqual(other.execute(
                            'SELECT message FROM suggestion_test_host_write WHERE id=?', (sid,)).fetchone(),
                            (message['id'],))
                    self.app.prompt_suggestions.complete.assert_not_called()
                finally:
                    db.set_authorizer(None)
                    if fault != 'lookup':
                        db.execute('DROP TRIGGER suggestion_optional_failure')
                        db.create_function('suggestion_optional_failure_seen', 0, None)
                    shared.turns.pop((sid, 'alpha'), None)
                self.assertEqual(db.execute(
                    'SELECT legacy,command_floor FROM suggestion_cache_policy').fetchone(), (1, floor))

    async def test_synced_preference_default_off_two_browsers_new_client_and_restart_same_owner(self):
        from amplifier_web.service import AppService
        await self.app.close()
        self.app = AppService(self.root / 'fresh-owner', self.runtime, workspace=self.root)
        self.assertFalse(self.app.state['settings']['suggestedMessages'])
        await self.app.dispatch('session.create', {}, include_state=False)
        sid = self.app.state['selectedSessionId']
        session = self.app._session(sid)
        session['selection'] = {'instance': 'saved-chat', 'model': 'foreground', 'effort': None}
        original_selection = copy.deepcopy(session['selection'])
        self.app._publish_changes(sessions={sid})
        for identity in ('one', 'two'):
            self.app.clients.attach(identity, kind='web')
            self.app.clients.records[identity]['selectedSessionId'] = sid
            with self.app.clients.bind(identity):
                await self.app.dispatch('view.update', {'patch': {'draft': 'Private draft ' + identity}},
                                        include_state=False)
        before_drafts = {key: copy.deepcopy(row['drafts']) for key, row in self.app.clients.records.items()}
        before = copy.deepcopy(self.app.state['settings'])
        model_config = describe(self.app.prompt_suggestions.manager, self.root)
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        self.app.prompt_suggestions.complete = AsyncMock(side_effect=AssertionError('toggling is never inference'))
        # Observe two separately-bound cached projections before changing one.
        for identity in ('one', 'two'):
            with self.app.clients.bind(identity):
                self.assertFalse(self.app.browser_state()['settings']['suggestedMessages'])
        streams = {}
        for identity in ('one', 'two'):
            with self.app.clients.bind(identity):
                streams[identity] = self.app.subscribe()
                while not streams[identity].empty():
                    streams[identity].get_nowait()
        with self.app.clients.bind('one'):
            receipt = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                             command_id='synced-on', include_state=False)
            self.assertEqual(receipt['result'], {
                'settings': {'suggestedMessages': True, 'suggestedMessagesRevision': 1}, 'scope': 'installation-owner'})
        for identity in ('one', 'two'):
            queue = streams[identity]
            self.assertFalse(queue.empty(), 'Owner setting must publish to every connected browser')
            while not queue.empty():
                published = queue.get_nowait()
            self.assertTrue(published['settings']['suggestedMessages'])
            self.assertEqual(published['client']['id'], identity)
            self.assertEqual(published['view']['draft'], 'Private draft ' + identity)
            self.app.unsubscribe(queue)
            with self.app.clients.bind(identity):
                self.assertTrue(self.app.browser_state()['settings']['suggestedMessages'])
        self.app.clients.attach('third', kind='web')
        with self.app.clients.bind('third'):
            self.assertTrue(self.app.browser_state()['settings']['suggestedMessages'])
        self.assertEqual({key: row['drafts'] for key, row in self.app.clients.records.items()
                          if key in before_drafts}, before_drafts)
        self.assertEqual(self.app._session(sid)['selection'], original_selection)
        self.assertEqual(describe(self.app.prompt_suggestions.manager, self.root), model_config)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertEqual({key: value for key, value in self.app.state['settings'].items()
                          if key not in {'suggestedMessages', 'suggestedMessagesRevision'}},
                         {key: value for key, value in before.items()
                          if key not in {'suggestedMessages', 'suggestedMessagesRevision'}})
        self.app.prompt_suggestions.complete.assert_not_called()
        await self.app.close()
        self.app = AppService(self.root / 'fresh-owner', self.runtime, workspace=self.root)
        self.app.clients.attach('after-new-signin-document', kind='web')
        with self.app.clients.bind('after-new-signin-document'):
            self.assertTrue(self.app.browser_state()['settings']['suggestedMessages'])
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                    command_id='synced-off', include_state=False)
        await self.app.close()
        self.app = AppService(self.root / 'fresh-owner', self.runtime, workspace=self.root)
        self.assertFalse(self.app.state['settings']['suggestedMessages'])
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_synced_preference_rejects_invalid_unauthorized_or_failed_writes_without_optimistic_state(self):
        await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}}, include_state=False)
        before = copy.deepcopy(self.app.state['settings'])
        revision = self.app.state['revision']
        for invalid in ('true', 1, None, {}):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': invalid}}, include_state=False)
        with self.assertRaisesRegex(Exception, 'one boolean'):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True, 'voiceName': 'marin'}},
                                    include_state=False)
        with self.assertRaises(Exception):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                    origin='agent', caller_session_id=None, include_state=False)
        with self.assertRaisesRegex(Exception, 'revoked'):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                    admission_check=lambda: (_ for _ in ()).throw(ValueError('revoked')),
                                    include_state=False)
        self.app.db.execute('''CREATE TRIGGER reject_setting_update BEFORE UPDATE ON state_records
            WHEN NEW.kind='global' AND NEW.id='settings'
            BEGIN SELECT RAISE(ABORT,'synthetic settings commit failure'); END''')
        self.app.db.commit()
        with self.assertRaises(sqlite3.Error):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                    command_id='failed-save', include_state=False)
        self.assertEqual(self.app.state['settings'], before)
        self.assertEqual(self.app.state['revision'], revision)
        self.assertIsNone(self.app.db.execute("SELECT 1 FROM commands WHERE id='failed-save'").fetchone())
        self.app.db.execute('DROP TRIGGER reject_setting_update')
        self.app.db.commit()
        saved = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                        command_id='saved-on', include_state=False)
        replay = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': True}},
                                         command_id='saved-on', include_state=False)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['revision'], saved['revision'])
        with self.assertRaisesRegex(Exception, 'different contents'):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                    command_id='saved-on', include_state=False)
        self.app.db.execute('''CREATE TRIGGER reject_setting_off BEFORE UPDATE ON state_records
            WHEN NEW.kind='global' AND NEW.id='settings'
            BEGIN SELECT RAISE(ABORT,'synthetic settings off failure'); END''')
        self.app.db.commit()
        with self.assertRaises(sqlite3.Error):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}}, include_state=False)
        self.assertTrue(self.app.state['settings']['suggestedMessages'])
        self.assertTrue(self.app.browser_state()['settings']['suggestedMessages'])
        self.app.db.execute('DROP TRIGGER reject_setting_off')
        self.app.db.commit()

    async def test_synced_preference_setting_revision_protects_delayed_setup_from_other_client_explicit_off(self):
        await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}}, include_state=False)
        for identity in ('one', 'two'):
            self.app.clients.attach(identity, kind='web')
        captured = self.app.state['settings']['suggestedMessagesRevision']
        with self.app.clients.bind('two'):
            off = await self.app.dispatch('settings.update', {
                'patch': {'suggestedMessages': False}, 'expectedSuggestedMessagesRevision': captured},
                command_id='newer-off', include_state=False)
            self.assertEqual(off['result']['settings']['suggestedMessagesRevision'], captured + 1)
            replay = await self.app.dispatch('settings.update', {
                'patch': {'suggestedMessages': False}, 'expectedSuggestedMessagesRevision': captured},
                command_id='newer-off', include_state=False)
            self.assertTrue(replay['duplicate'])
            self.assertEqual(self.app.state['settings']['suggestedMessagesRevision'], captured + 1)
        # Model setup completes independently; it cannot advance or reset the
        # captured user preference revision.
        await self.app.dispatch('providers.configureSuggestions', {
            'enabled': False, 'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
        self.assertEqual(self.app.state['settings']['suggestedMessagesRevision'], captured + 1)
        revision = self.app.state['revision']
        cache = list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        with self.app.clients.bind('one'):
            with self.assertRaisesRegex(Exception, 'Suggested messages changed'):
                await self.app.dispatch('settings.update', {
                    'patch': {'suggestedMessages': True}, 'expectedSuggestedMessagesRevision': captured},
                    command_id='late-enable', include_state=False)
        self.assertFalse(self.app.state['settings']['suggestedMessages'])
        self.assertEqual(self.app.state['revision'], revision)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_success_cache')), cache)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        self.assertIsNone(self.app.db.execute("SELECT 1 FROM commands WHERE id='late-enable'").fetchone())
        for invalid in (-1, True, '1', None):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                await self.app.dispatch('settings.update', {
                    'patch': {'suggestedMessages': True}, 'expectedSuggestedMessagesRevision': invalid},
                    include_state=False)
        with self.assertRaisesRegex(Exception, 'requires suggestedMessages'):
            await self.app.dispatch('settings.update', {
                'patch': {'notifications': {}}, 'expectedSuggestedMessagesRevision': captured}, include_state=False)

    async def test_synced_preference_pending_progress_is_flushed_before_cas_and_duplicate_state(self):
        await self.app.dispatch('session.create', {}, include_state=False)
        sid = self.app.state['selectedSessionId']
        revision = self.app.state['revision']
        self.app._session(sid)['streaming'] = 'Coalesced progress'
        self.app._publish_progress(session_ids={sid}, record_only=True)
        self.assertTrue(self.app._progress_dirty)
        with self.assertRaisesRegex(Exception, 'Settings changed'):
            await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                    expected_revision=revision, include_state=False)
        self.assertTrue(self.app.state['settings']['suggestedMessages'])
        self.assertGreater(self.app.state['revision'], revision)
        result = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                         command_id='once-off', include_state=False)
        self.app._session(sid)['streaming'] = 'More coalesced progress'
        self.app._publish_progress(session_ids={sid}, record_only=True)
        replay = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                         command_id='once-off', include_state=True)
        self.assertTrue(replay['duplicate'])
        self.assertGreater(replay['state']['revision'], result['revision'])
        self.assertFalse(self.app._progress_dirty)
        self.assertFalse(replay['state']['settings']['suggestedMessages'])

    async def test_synced_preference_postcommit_publication_error_keeps_off_and_cancels_pending(self):
        # This real projection hook runs after the ordinary SQLite commit.
        host = self.app.prompt_suggestions
        with patch.object(host, 'invalidate', wraps=host.invalidate) as invalidate, \
                patch.object(self.app, 'status_emitter',
                             SimpleNamespace(publish=Mock(side_effect=OSError('projection failed')))):
            with self.assertRaisesRegex(OSError, 'projection failed'):
                await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                        command_id='committed-off', include_state=False)
            invalidate.assert_called_once_with(clear_cache=False)
        self.assertFalse(self.app.state['settings']['suggestedMessages'])
        durable = json.loads(self.app.db.execute(
            "SELECT value FROM state_records WHERE kind='global' AND id='settings'").fetchone()[0])['value']
        self.assertFalse(durable['suggestedMessages'])
        self.assertEqual(durable['suggestedMessagesRevision'],
                         self.app.state['settings']['suggestedMessagesRevision'])
        receipt = json.loads(self.app.db.execute(
            "SELECT receipt FROM commands WHERE id='committed-off'").fetchone()[0])
        self.assertFalse(receipt['result']['settings']['suggestedMessages'])
        replay = await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                         command_id='committed-off', include_state=False)
        self.assertTrue(replay['duplicate'])
        self.assertFalse(self.app.state['settings']['suggestedMessages'])

    async def test_synced_preference_disable_cancels_pending_other_browser_and_keeps_paid_claims_and_cache(self):
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, messages=[
            {'id': 'human', 'role': 'user', 'text': 'Task', 'inputOrigin': 'ui'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Complete'}])
        self.app._publish_changes(sessions={sid})
        for identity in ('one', 'two'):
            self.app.clients.attach(identity, kind='web')
            self.app.clients.records[identity]['selectedSessionId'] = sid
        host = self.app.prompt_suggestions
        host.settings = lambda: ({'enabled': True, 'provider': 'saved', 'model': 'model',
                                  'transcriptMode': 'last_n', 'humanTurns': 6}, {'id': 'saved'}, 'revision')
        entered, settling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def respond(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                settling.set()
                await release.wait()
                raise
        host.complete = AsyncMock(side_effect=respond)
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('one'):
                await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'pending', 'expectedLastMessageId': 'assistant'}, 'ui')
            await entered.wait()
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            with self.app.clients.bind('two'):
                await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}},
                                        include_state=False)
                self.assertFalse(self.app.browser_state()['settings']['suggestedMessages'])
            await asyncio.wait_for(settling.wait(), 3)
            self.assertTrue(host.jobs, 'Off cannot claim physical settlement early')
            with self.app.clients.bind('one'):
                self.assertFalse(self.app.browser_state()['settings']['suggestedMessages'])
                disabled = await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'off-request', 'expectedLastMessageId': 'assistant'}, 'ui')
                self.assertEqual(disabled['status'], 'disabled')
            host.complete.assert_awaited_once()
            self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        finally:
            release.set()
            await asyncio.gather(*tuple(host.jobs))
            BROWSER.reset(token)
        # Saved successful assistance is independent of the opt-in switch:
        # Off hides/cancels RAM work without purging a user's saved result.
        self.app.db.execute('INSERT INTO suggestion_success_cache VALUES (?,?,?,?,?)',
                            ('cookie', sid, 'synthetic-key', '{"status":"empty"}', 1))
        self.app.db.commit()
        cached = list(self.app.db.execute('SELECT * FROM suggestion_success_cache'))
        await self.app.dispatch('settings.update', {'patch': {'suggestedMessages': False}}, include_state=False)
        self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_success_cache')), cached)
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_durable_success_and_empty_recover_after_real_app_restart_without_draft_or_model_work(self):
        from amplifier_web.service import AppService
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai',
            'source': 'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
            'config': {'api_key': 'synthetic-key'}}]}))
        await self.app.dispatch('providers.configureSuggestions', {
            'enabled': True, 'shareRecentConversation': True, 'provider': 'saved', 'model': 'utility-model',
            'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
        originals = {}
        token = BROWSER.set({'principal': 'same-authenticated-cookie', 'valid': lambda: True})
        try:
            self.app.clients.attach('original', kind='web')
            for state in ('ready', 'empty'):
                await self.app.dispatch('session.create', {}, include_state=False)
                session = self.app._session()
                sid = session['id']
                session.update(status='idle', historyManaged=False, runtimeSessionId='old-worker-incarnation',
                    messages=[{'id': 'human', 'role': 'user', 'inputOrigin': 'ui',
                               'text': 'Human ' + state + ' example' * 2000},
                              {'id': 'assistant', 'role': 'assistant', 'text': 'Completed ' + state + ' next' * 550}])
                self.app._publish_changes(sessions={sid})
                self.app.clients.records['original']['selectedSessionId'] = sid
                with self.app.clients.bind('original'):
                    await self.app.dispatch('view.update', {'patch': {'draft': 'ACTUAL USER DRAFT ' + state}},
                                            include_state=False)
                    self.app.prompt_suggestions.complete = AsyncMock(return_value={
                        'status': state, 'suggestion': 'DURABLE PRIVATE GHOST' if state == 'ready' else None,
                        'usage': {'input_tokens': 40, 'output_tokens': 6}, 'cost': None})
                    args = {'sessionId': sid, 'requestId': state, 'expectedLastMessageId': 'assistant'}
                    await self.app.dispatch('session.suggestion.request', args, include_state=False)
                    await asyncio.gather(*tuple(self.app.prompt_suggestions.jobs))
                    result = (await self.app.dispatch('session.suggestion.status',
                        {'sessionId': sid, 'requestId': state}, include_state=False))['result']
                    self.assertEqual(result['status'], state)
                    self.app.prompt_suggestions.complete.assert_awaited_once()
                    originals[state] = (sid, result)
            before_claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            # Real private DB rows exist before publication. Draft/state/command
            # owners cannot accidentally serialize a candidate from this table.
            for table, column in (('client_views', 'value'), ('state_records', 'value'), ('commands', 'receipt')):
                self.assertNotIn('DURABLE PRIVATE GHOST',
                                 repr(list(self.app.db.execute(f'SELECT {column} FROM {table}'))))
            self.assertEqual((self.root / 'app' / 'app.sqlite3').stat().st_mode & 0o777, 0o600)
            self.app._state['selectedSessionId'] = None
            for client in self.app.clients.records.values():
                client['selectedSessionId'] = None
            with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                retired = self.app.cold_display.retire(force=True)
            self.assertTrue(all(sid in retired for sid, _ in originals.values()))
            self.assertTrue(all(not dict.__contains__(self.app._session(sid, hydrate=False), 'messages')
                                for sid, _ in originals.values()))
            await self.app.close()
            # Neither a fresh process's discovery salt nor a worker incarnation
            # is a change to the public input or saved model/account selection.
            with patch('amplifier_web.provider_catalog._UNQUALIFIED_GENERATION', 'fresh-process-salt'):
                self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)
                self.app.prompt_suggestions.complete = AsyncMock(side_effect=AssertionError('recovery must not infer'))
                self.assertFalse(self.app.prompt_suggestions.records)
                self.app._state['selectedSessionId'] = None
                with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                    self.app.cold_display.retire(force=True)
                for i in range(70):
                    self.app.history.windows._fence('unrelated-owner-' + str(i))
                self.app.clients.attach('new-document', kind='web')
                for state, (sid, original) in originals.items():
                    session = self.app._session(sid, hydrate=False)
                    session['runtimeSessionId'] = 'fresh-worker-incarnation'
                    self.app._publish_changes(sessions={sid})
                    self.app._state['selectedSessionId'] = None
                    self.app.clients.records['new-document']['selectedSessionId'] = None
                    with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                        self.app.cold_display.retire(force=True)
                    self.assertFalse(dict.__contains__(session, 'messages'), 'cold setup before utility lookup')
                    self.app.clients.records['new-document']['selectedSessionId'] = sid
                    with self.app.clients.bind('new-document'):
                        await self.app.prompt_suggestions.dispatch('session.suggestion.request', {
                            'sessionId': sid, 'requestId': 'reopened-' + state,
                            'expectedLastMessageId': 'assistant'}, 'ui')
                        await asyncio.gather(*tuple(self.app.prompt_suggestions.jobs))
                        recovered = await self.app.prompt_suggestions.dispatch('session.suggestion.status', {
                            'sessionId': sid, 'requestId': 'reopened-' + state}, 'ui')
                        self.assertEqual(recovered['status'], state)
                        self.assertEqual(recovered['suggestion'], original['suggestion'])
                        self.assertEqual(recovered['generationId'], original['generationId'])
                        self.assertEqual(recovered['usage'], original['usage'])
                        self.assertIsNone(recovered['cost'])
                        self.assertTrue(recovered['reused'])
                        self.assertNotEqual(recovered['configRevision'], original['configRevision'])
                        self.assertFalse(dict.__contains__(session, 'messages'))
                self.app.prompt_suggestions.complete.assert_not_called()
                self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_success_cache').fetchone()[0], 2)
                # A discovery-only revision or worker change can stale an old
                # immediate receipt, never delete the stable saved result.
                sid, original = originals['ready']
                self.app.clients.records['new-document']['selectedSessionId'] = sid
                with self.app.clients.bind('new-document'):
                    self.app._session(sid, hydrate=False)['runtimeSessionId'] = 'another-worker-incarnation'
                    stale = await self.app.prompt_suggestions.dispatch('session.suggestion.status', {
                        'sessionId': sid, 'requestId': 'reopened-ready'}, 'ui')
                    self.assertEqual(stale['status'], 'stale')
                    self.assertEqual(self.app.db.execute('SELECT count(*) FROM suggestion_success_cache').fetchone()[0], 2)
                    await self.app.prompt_suggestions.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': 'after-transient-status',
                        'expectedLastMessageId': 'assistant'}, 'ui')
                    await asyncio.gather(*tuple(self.app.prompt_suggestions.jobs))
                    recovered = await self.app.prompt_suggestions.dispatch('session.suggestion.status', {
                        'sessionId': sid, 'requestId': 'after-transient-status'}, 'ui')
                    self.assertEqual(recovered['generationId'], original['generationId'])
                    self.assertEqual(recovered['status'], 'ready')
                self.app.prompt_suggestions.complete.assert_not_called()
            for state, (sid, _) in originals.items():
                self.assertEqual(self.app.clients.records['original']['drafts'][sid], 'ACTUAL USER DRAFT ' + state)
            self.assertNotIn('DURABLE PRIVATE GHOST', repr(self.app.get_state()))
            self.assertTrue(all(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                                   (row[0],)).fetchone() == row for row in before_claims))
            self.runtime.start.assert_not_called()
            self.runtime.send.assert_not_called()
        finally:
            BROWSER.reset(token)

    async def test_stable_paid_claim_survives_stop_and_discovery_incarnation_without_automatic_retry(self):
        from amplifier_web.service import AppService
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai',
            'source': 'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
            'config': {'api_key': 'synthetic-key'}}]}))
        await self.app.dispatch('providers.configureSuggestions', {
            'enabled': True, 'shareRecentConversation': True, 'provider': 'saved', 'model': 'utility-model',
            'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, interruptionRevision=0, messages=[
            {'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Task'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Unchanged input'}])
        self.app._publish_changes(sessions={sid})
        host = self.app.prompt_suggestions
        host.complete = AsyncMock(side_effect=TimeoutError('unknown remote result'))
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            self.app.clients.attach('first', kind='web')
            self.app.clients.records['first']['selectedSessionId'] = sid
            with self.app.clients.bind('first'):
                await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'uncertain', 'expectedLastMessageId': 'assistant'}, 'ui')
                await asyncio.gather(*tuple(host.jobs))
            host.complete.assert_awaited_once()
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            session['interruptionRevision'] += 1
            self.app._publish_changes(sessions={sid})
            await self.app.close()
            with patch('amplifier_web.provider_catalog._UNQUALIFIED_GENERATION', 'different-process'):
                self.app = AppService(self.root / 'app', self.runtime, workspace=self.root)
                self.app.clients.attach('second', kind='web')
                self.app.clients.records['second']['selectedSessionId'] = sid
                host = self.app.prompt_suggestions
                host.complete = AsyncMock(side_effect=AssertionError('Stop plus salt is not a retry grant'))
                with self.app.clients.bind('second'):
                    await host.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': 'after-restart', 'expectedLastMessageId': 'assistant'}, 'ui')
                    await asyncio.gather(*tuple(host.jobs))
                    result = await host.dispatch('session.suggestion.status', {
                        'sessionId': sid, 'requestId': 'after-restart'}, 'ui')
                    self.assertEqual(result['status'], 'duplicate_source')
                    self.assertEqual(result['reason'], 'prior_attempt_unavailable')
                host.complete.assert_not_called()
            self.assertTrue(all(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                                   (row[0],)).fetchone() == row for row in claims))
        finally:
            BROWSER.reset(token)

    async def test_legacy_migration_blocks_unprojectable_old_tail_but_real_new_ui_completion_resumes_automatically(self):
        from amplifier_web.service import AppService
        class OfflineRuntime:
            def __init__(self):
                self.sent = []
            async def start(self, session, emit):
                pass
            async def send(self, session, text, input_id, emit):
                self.sent.append((session['id'], input_id))
                await emit('assistant.message', {'sessionId': session['id'], 'text': 'New completed reply',
                                                 'inputId': input_id, 'generationId': 'post-migration'})
                await emit('runtime.generation', {'sessionId': session['id'], 'event': 'generation.finished',
                    'generation_id': 'post-migration', 'input_ids': [input_id],
                    'active_job_ids': [], 'disposition': 'manager_turn_finished'})
                await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
            async def close(self):
                pass
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai',
            'source': 'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
            'config': {'api_key': 'synthetic-key'}}]}))
        await self.app.dispatch('providers.configureSuggestions', {
            'enabled': True, 'shareRecentConversation': True, 'provider': 'saved', 'model': 'utility-model',
            'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, messages=[
            {'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Old user'},
            {'id': 'old-assistant', 'role': 'assistant', 'text': 'Old completed response',
             'createdAt': time.time() + 999999}])
        self.app._publish_changes(sessions={sid})
        await self.app.close()
        # Exact old table shape, before cache-policy migration. The old salted
        # hash cannot be projected onto a conversation/outcome; preserve it.
        raw = sqlite3.connect(self.root / 'app' / 'app.sqlite3')
        raw.execute('DROP TABLE suggestion_cache_policy')
        raw.execute('INSERT INTO suggestion_requests VALUES (?,?)', ('legacy-unknown', 'unprojectable-old-salted-hash'))
        raw.commit()
        raw.close()
        offline_runtime = OfflineRuntime()
        with patch('amplifier_web.provider_catalog._UNQUALIFIED_GENERATION', 'migration-new-process'):
            self.app = AppService(self.root / 'app', offline_runtime, workspace=self.root)
            host = self.app.prompt_suggestions
            host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'A new automatic suggestion.',
                                                   'usage': None, 'cost': None})
            self.app.clients.attach('browser', kind='web')
            self.app.clients.records['browser']['selectedSessionId'] = sid
            token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
            try:
                with self.app.clients.bind('browser'):
                    # No model/provider work is used to establish the boundary.
                    host.observe_message(self.app._session(sid), {
                        'id': 'forged-import', 'role': 'assistant', 'source': 'native',
                        'inputId': 'made-up', 'createdAt': time.time() + 999999})
                    self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_fresh_completion').fetchone())
                    await host.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': 'old-tail', 'expectedLastMessageId': 'old-assistant'}, 'ui')
                    await asyncio.gather(*tuple(host.jobs))
                    result = await host.dispatch('session.suggestion.status', {
                        'sessionId': sid, 'requestId': 'old-tail'}, 'ui')
                    self.assertEqual(result['status'], 'duplicate_source')
                    self.assertEqual(result['reason'], 'migration_prior_attempt_unavailable')
                    host.complete.assert_not_called()
                    # A manufactured future timestamp/imported assistant row is
                    # not a canonical new UI admission/completion.
                    self.assertIsNone(self.app.db.execute(
                        'SELECT 1 FROM suggestion_fresh_completion').fetchone())
                    await self.app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Genuinely new work'},
                                            command_id='new-ui-input', include_state=False)
                    while self.app.tasks:
                        await asyncio.gather(*tuple(self.app.tasks))
                    current = self.app._session(sid)
                    self.assertEqual(current['status'], 'idle')
                    tail = current['messages'][-1]
                    self.assertEqual(tail['role'], 'assistant')
                    self.assertIsNotNone(self.app.db.execute(
                        'SELECT 1 FROM suggestion_fresh_completion WHERE session_id=?', (sid,)).fetchone())
                    # A proof cannot authorize a different/imported tail.
                    self.assertFalse(host._migration_allows({'sessionId': sid}, [{'id': 'forged-import'}]))
                    await host.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': 'new-tail', 'expectedLastMessageId': tail['id']}, 'ui')
                    await asyncio.gather(*tuple(host.jobs))
                    result = await host.dispatch('session.suggestion.status', {
                        'sessionId': sid, 'requestId': 'new-tail'}, 'ui')
                    self.assertEqual(result['status'], 'ready')
                    host.complete.assert_awaited_once()
                self.assertEqual(self.app.db.execute(
                    "SELECT source FROM suggestion_requests WHERE identity='legacy-unknown'").fetchone(),
                    ('unprojectable-old-salted-hash',))
                self.assertEqual(len(offline_runtime.sent), 1)  # Synthetic maintained runtime, not a provider.
            finally:
                BROWSER.reset(token)

    async def test_durable_cache_deletion_stop_and_config_invalidation_use_existing_private_transaction(self):
        from amplifier_web.state_records import UPSERT
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, interruptionRevision=0, messages=[
            {'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Build'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Ready'}])
        self.app._publish_changes(sessions={sid})
        host = self.app.prompt_suggestions
        config = {'enabled': True, 'provider': 'fixture', 'model': 'model',
                  'transcriptMode': 'last_n', 'humanTurns': 6}
        host.settings = lambda: (config, {'id': 'fixture'}, 'revision')
        host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'PRIVATE CACHE',
                                               'usage': None, 'cost': None})
        self.app.clients.attach('browser', kind='web')
        self.app.clients.records['browser']['selectedSessionId'] = sid
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'original', 'expectedLastMessageId': 'assistant'}, 'ui')
                await asyncio.gather(*tuple(host.jobs))
            cached = self.app.db.execute('SELECT * FROM suggestion_success_cache').fetchone()
            self.assertIsNotNone(cached)
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            session['interruptionRevision'] += 1
            self.app._publish_changes(sessions={sid})
            self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())
            # The ordinary canonical owner removal clears a saved body even
            # when no request/client is polling that chat.
            self.app.db.execute('INSERT INTO suggestion_success_cache VALUES (?,?,?,?,?)', cached)
            self.app.db.execute(UPSERT, ('session', sid, 'null'))
            self.app.db.commit()
            self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())
            self.app.db.execute('INSERT INTO suggestion_success_cache VALUES (?,?,?,?,?)', cached)
            host.invalidate()
            self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())
            self.app.db.execute('INSERT INTO suggestion_success_cache VALUES (?,?,?,?,?)', cached)
            self.app.db.commit()
            # Ordinary connection/source/account writes all converge on this
            # existing owner, including A->B->A without a suggestion poll.
            from amplifier_web.management import Management
            self.app.management = Management(self.app)
            await self.app.management.invalidate_configuration(warm=False, session_ids=set())
            self.assertIsNone(self.app.db.execute('SELECT 1 FROM suggestion_success_cache').fetchone())
            self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
        finally:
            BROWSER.reset(token)

    async def test_real_cold_retirement_and_hydration_preserve_canonical_public_freshness(self):
        from amplifier_web.suggestion_source import changed_component
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', historyManaged=False, messages=[
            {'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant',
             'inputOrigin': 'ui' if i % 2 == 0 else 'agent',
             'text': 'Public row ' + str(i) + ' ' + 'x' * 1100} for i in range(20)])
        self.app._publish_changes(sessions={sid})
        config = {'transcriptMode': 'last_n', 'humanTurns': 6}
        before = {}
        resident = witness(self.app, sid, config, components=before)
        from amplifier_web.suggestion_provider import transcript_budget
        expected = select(public_rows(dict.get(session, 'messages')), transcript_budget(INSTRUCTION))
        self.app._state['selectedSessionId'] = None
        with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
            self.assertIn(sid, self.app.cold_display.retire(force=True))
        self.assertFalse(dict.__contains__(session, 'messages'))
        after = {}
        cold = witness(self.app, sid, config, components=after)
        self.assertEqual(resident, cold)  # Storage representation alone is not a public-input change.
        self.assertEqual(changed_component(before, after), 'current_source_storage_mode')
        page = await self.app.history.windows.read(sid, limit=100, public_only=True)
        retained = self.app.history.windows.pages[(sid, page['windowKey'])][0]['items']
        actual = select(public_rows(retained), transcript_budget(INSTRUCTION))
        self.assertEqual(actual, expected)  # Real storage transition, identical canonical bounded input.
        self.assertFalse(dict.__contains__(session, 'messages'))  # No hydration needed to classify.
        self.assertNotIn('Public row', repr(after))
        self.app.cold_display.hydrate(session)
        self.assertTrue(dict.__contains__(session, 'messages'))
        self.assertEqual(witness(self.app, sid, config), resident)

    async def test_cold_utility_cache_revalidates_exact_owner_resource_and_signature_without_hydration(self):
        from amplifier_web.saved_message_window import read_saved_window
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        rows = [{'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant',
                 'inputOrigin': 'ui' if i % 2 == 0 else 'agent',
                 'text': 'Public row ' + str(i) + ' ' + 'x' * 1100} for i in range(20)]
        session.update(status='idle', messages=copy.deepcopy(rows))
        self.app._publish_changes(sessions={sid})
        self.app._state['selectedSessionId'] = None
        with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
            self.assertIn(sid, self.app.cold_display.retire(force=True))
        config = {'transcriptMode': 'last_n', 'humanTurns': 6}
        cold = self.app.history.windows._cold_source(session)
        path = cold.directory / (cold.identity + '.json')
        with patch('amplifier_web.saved_message_window.read_saved_window', wraps=read_saved_window) as read:
            baseline = witness(self.app, sid, config)
            for _ in range(8):
                self.app.history.windows.invalidate({sid})
                self.assertEqual(witness(self.app, sid, config), baseline)
            self.assertEqual(read.call_count, 1)
            # Physical stamp alone invalidates reuse, then identical public
            # content passes. No cached body is trusted across that change.
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
            self.assertEqual(witness(self.app, sid, config), baseline)
            self.assertEqual(read.call_count, 2)
            # Every public input dimension still changes semantic freshness,
            # even under an unchanged (now physically modified) resource ID.
            changes = [
                lambda values: values[-1].update(text='Real text edit'),
                lambda values: values.append({'id': 'append', 'role': 'assistant', 'text': 'New public reply'}),
                lambda values: values[10].update(inputActor={'kind': 'system', 'id': 'other'}),
                lambda values: values[-1].update(agentLabel='Changed speaker'),
            ]
            for change in changes:
                changed = copy.deepcopy(rows)
                change(changed)
                path.write_text(json.dumps(changed))
                self.assertNotEqual(witness(self.app, sid, config), baseline)
            path.write_text('invalid saved source')
            with self.assertRaises(ValueError):
                witness(self.app, sid, config)
        self.assertFalse(dict.__contains__(session, 'messages'))
        self.assertLessEqual(len(self.app.history.windows.pages), 32)
        self.assertLessEqual(self.app.history.windows.bytes, 32 * 1024 * 1024)

    async def test_nonpaid_pending_suggestion_survives_real_cooling_naming_and_hydration(self):
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', messages=[
            {'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant',
             'inputOrigin': 'ui' if i % 2 == 0 else 'agent',
             'text': 'Public completed input ' + str(i) + ' ' + 'x' * 1100} for i in range(20)])
        self.app._publish_changes(sessions={sid})
        host = self.app.prompt_suggestions
        config = {'enabled': True, 'provider': 'saved', 'model': 'explicit',
                  'humanTurns': 6, 'transcriptMode': 'last_n'}
        host.settings = lambda: (config, {'id': 'saved'}, 'revision')
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(*args):
            entered.set()
            await release.wait()
            return {'status': 'ready', 'suggestion': 'One next step.', 'usage': None, 'cost': None}
        host.complete = AsyncMock(side_effect=respond)
        self.app.clients.attach('browser', kind='web')
        self.app.clients.records['browser']['selectedSessionId'] = sid
        token = BROWSER.set({'principal': 'same-cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                pending = await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'cooling', 'expectedLastMessageId': '19'}, 'ui')
                await entered.wait()
                self.app._state['selectedSessionId'] = None  # No live stream subscription in fixture.
                with patch.object(self.app.cold_display, 'RECENT_LIMIT', 0):
                    self.assertIn(sid, self.app.cold_display.retire(force=True))
                self.assertFalse(dict.__contains__(session, 'messages'))
                record = next(iter(host.records.values()))
                self.assertTrue(host.current(record))
                self.assertFalse(dict.__contains__(session, 'messages'))
                await self.app.on_runtime_event('session.naming.progress', {
                    'sessionId': sid, 'rootSessionId': sid, 'completedInputs': ['18']})
                self.app.cold_display.hydrate(session)
                self.assertTrue(host.current(record))
                release.set()
                await asyncio.gather(*tuple(host.jobs))
                ready = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'cooling'}, 'ui')
                self.assertEqual(ready['status'], 'ready')
                self.assertEqual(ready['sourceKey'], pending['sourceKey'])
                host.complete.assert_awaited_once()
                session['interruptionRevision'] = 1
                stale = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'cooling'}, 'ui')
                self.assertEqual(stale['status'], 'stale')
                self.assertEqual(stale['reason'], 'current_source_stop')
        finally:
            release.set()
            BROWSER.reset(token)

    async def test_ordinary_native_naming_event_after_done_does_not_stale_public_suggestion(self):
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', messages=[
            {'id': 'human', 'role': 'user', 'text': 'A completed request', 'inputOrigin': 'ui'},
            {'id': 'reply', 'role': 'assistant', 'text': 'Completed public reply'}])
        host = self.app.prompt_suggestions
        config = {'enabled': True, 'provider': 'saved', 'model': 'explicit',
                  'humanTurns': 6, 'transcriptMode': 'last_n'}
        host.settings = lambda: (config, {'id': 'saved'}, 'revision')
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(*args):
            entered.set()
            await release.wait()
            return {'status': 'ready', 'suggestion': 'One next step.', 'usage': None, 'cost': None}
        host.complete = respond
        self.app.clients.attach('browser', kind='web')
        self.app.clients.records['browser']['selectedSessionId'] = sid
        token = BROWSER.set({'principal': 'same-cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                pending = await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'after-done', 'expectedLastMessageId': 'reply'}, 'ui')
                await entered.wait()
                prior_fence = self.app.history.windows._fence(sid)
                await self.app.on_runtime_event('session.naming.progress', {
                    'sessionId': sid, 'rootSessionId': sid, 'completedInputs': ['human']})
                self.assertNotEqual(self.app.history.windows._fence(sid), prior_fence)
                release.set()
                await asyncio.gather(*tuple(host.jobs))
                ready = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'after-done'}, 'ui')
                self.assertEqual(ready['status'], 'ready')
                self.assertEqual(ready['sourceKey'], pending['sourceKey'])
        finally:
            release.set()
            BROWSER.reset(token)

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
            self.assertTrue(all(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                                   (row[0],)).fetchone() == row for row in claims))
        finally:
            BROWSER.reset(token)

    async def test_migrated_decision_only_account_is_not_a_suggestion_choice_or_saved_readiness(self):
        from amplifier_web.decision_namespace import translate
        from amplifier_web.suggestion_settings import selected
        manager = self.app.prompt_suggestions.manager
        migrated = translate({'config': {'providers': [
            {'id': 'specialist', 'module': 'provider-specialist', 'config': {'chat_enabled': False}},
            {'id': 'chat', 'module': 'provider-openai', 'config': {'api_key': 'synthetic-chat-key'}}]},
            'prompt_suggestions': {'enabled': True, 'shareRecentConversation': True,
                                   'provider': 'specialist', 'model': 'saved-stale-model'}},
            {'provider-specialist': ({'decision_only': True}, lambda value: value)})
        self.assertNotIn('specialist', [row.get('id') for row in migrated['config']['providers']])
        self.assertIn('specialist', migrated['decision_models']['connections'])
        manager.store.update(self.root, 'global', lambda value: value.update(migrated))
        before = copy.deepcopy(manager.store.read(self.root, 'global'))
        config_revision = self.app.state.get('configurationRevision', 0)
        with patch.object(manager, 'probe', AsyncMock(side_effect=AssertionError('no probe'))) as probe, \
                patch.object(manager, 'discover_catalog', AsyncMock(side_effect=AssertionError('no discovery'))) as discover, \
                patch.object(manager, 'catalog_key', wraps=manager.catalog_key) as key:
            result = (await self.app.dispatch('providers.suggestionModels', {}, include_state=False))['result']
            self.assertEqual([row['id'] for row in result['providers']], ['chat'])
            self.assertEqual(result['providers'][0]['sharedCatalogKey'],
                             manager.catalog_key({'id': 'chat'}, str(self.app.data_dir)))
            config, row, _ = selected(manager, str(self.app.data_dir))
            self.assertEqual(config['provider'], 'specialist')  # No silent setting rewrite.
            self.assertIsNone(row)
            self.assertTrue(all(call.args[0].get('id') != 'specialist' for call in key.call_args_list))
            with self.assertRaisesRegex(Exception, 'saved chat connection'):
                await self.app.dispatch('providers.configureSuggestions', {
                    'enabled': True, 'provider': 'specialist', 'model': 'cannot-execute',
                    'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
            self.assertEqual(manager.store.read(self.root, 'global'), before)
            self.assertEqual(self.app.state.get('configurationRevision', 0), config_revision)
            self.assertEqual({row['id'] for row in manager.provider_rows(self.root)}, {'specialist', 'chat'})
            await self.app.dispatch('session.create', {}, include_state=False)
            session = self.app._session()
            session.update(status='idle', historyManaged=False, messages=[
                {'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'What next?'},
                {'id': 'assistant', 'role': 'assistant', 'text': 'Ready.'}])
            sid = session['id']
            self.app.clients.attach('suggestion-browser', kind='web')
            self.app.clients.records['suggestion-browser']['selectedSessionId'] = sid
            token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
            host = self.app.prompt_suggestions
            host.complete = AsyncMock(side_effect=AssertionError('no utility call'))
            claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
            try:
                with self.app.clients.bind('suggestion-browser'):
                    unavailable = await host.dispatch('session.suggestion.request', {
                        'sessionId': sid, 'requestId': 'stale-decision-choice',
                        'expectedLastMessageId': 'assistant'}, 'ui')
                self.assertEqual(unavailable['status'], 'missing_selection')
                self.assertIsNone(unavailable['suggestion'])
                self.assertFalse(host.jobs)
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                host.complete.assert_not_called()
            finally:
                BROWSER.reset(token)
            saved = await self.app.dispatch('providers.configureSuggestions', {
                'enabled': True, 'shareRecentConversation': True, 'provider': 'chat', 'model': 'ordinary-model',
                'scope': 'global', 'configurationTarget': {'kind': 'global'}}, include_state=False)
            self.assertEqual(saved['result']['suggestionModels']['provider'], 'chat')
            snapshot = manager.probe_snapshot('suggestion.complete', {'id': 'chat'}, str(self.app.data_dir))
            self.assertEqual(snapshot['module'], 'provider-openai')
            self.assertEqual(snapshot['raw'], {'api_key': 'synthetic-chat-key'})
            self.assertEqual(manager.store.read(self.root, 'global')['decision_models'], before['decision_models'])
            probe.assert_not_called()
            discover.assert_not_called()

    async def test_suggestion_roster_projects_ordinary_rows_before_same_alias_decision_accounts(self):
        from amplifier_web.decision_namespace import translate
        from amplifier_web.suggestion_settings import selected
        manager = self.app.prompt_suggestions.manager
        definitions = {'provider-only': ({'decision_only': True}, lambda value: value)}
        for fields, alias in (({}, 'only'), ({'instance_id': 'same-alias'}, 'same-alias')):
            with self.subTest(fields=fields):
                migrated = translate({'config': {'providers': [
                    {'module': 'provider-only', **fields, 'config': {'priority': 1, 'chat_enabled': False}},
                    {'id': alias, 'module': 'provider-openai',
                     'config': {'priority': 2, 'api_key': 'synthetic-chat-key'}}]}}, definitions)
                def replace(value):
                    value.clear()
                    value.update(migrated)
                manager.store.update(self.root, 'global', replace)
                all_rows = manager.provider_rows(self.root)
                self.assertEqual([row['id'] for row in all_rows], [alias, alias])
                self.assertEqual([row['module'] for row in all_rows], ['provider-only', 'provider-openai'])
                before = copy.deepcopy(manager.store.read(self.root, 'global'))
                result = await manager.perform('providers.suggestionModels', {'workspace': str(self.root)})
                ordinary, = result['providers']
                self.assertEqual((ordinary['id'], ordinary['module']), (alias, 'provider-openai'))
                self.assertEqual(ordinary['sharedCatalogKey'], manager.catalog_key({'id': alias}, str(self.root)))
                configure(manager, {'enabled': True, 'shareRecentConversation': True,
                                    'provider': alias, 'model': 'chat-model'}, self.root, 'global')
                _, selected_row, _ = selected(manager, self.root)
                self.assertEqual(selected_row['module'], 'provider-openai')
                self.assertEqual(manager.probe_snapshot('suggestion.complete', {'id': alias}, self.root)['module'],
                                 'provider-openai')
                self.assertEqual(manager.store.read(self.root, 'global')['decision_models'], before['decision_models'])
                # A disabled ordinary account cannot borrow the enabled decision
                # account's identical public alias to pass validation/readiness.
                manager.store.update(self.root, 'global', lambda value:
                                     value['config']['providers'][0].update(enabled=False))
                self.assertIsNone(selected(manager, self.root)[1])
                with self.assertRaisesRegex(ValueError, 'saved chat connection'):
                    configure(manager, {'enabled': True, 'provider': alias, 'model': 'chat-model'}, self.root, 'global')
                self.assertTrue(manager.provider_rows(self.root)[0]['enabled'])
                self.assertFalse(manager.provider_rows(self.root, ordinary_only=True)[0]['enabled'])

    async def test_settings_authority_projection_and_no_model_runtime_call(self):
        current = self.app.state.get('configurationRevision', 0)
        before = await self.app.dispatch('providers.suggestionModels', {}, include_state=False)
        self.assertFalse(before['result']['suggestionModels']['enabled'])
        self.assertIsNone(before['result']['suggestionModels']['effort'])
        self.assertEqual(before['result']['suggestionModels']['prompt'], INSTRUCTION)
        self.assertEqual(before['result']['suggestionModels']['defaultPrompt'], INSTRUCTION)
        result = await self.app.dispatch('providers.configureSuggestions', {
            'enabled': False,
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
                'enabled': False, 'scope': 'global'},
                origin='agent', caller_session_id=None, include_state=False)

    async def test_recommendation_roster_uses_current_exact_account_catalog_and_preserves_manual_choice(self):
        from amplifier_web.provider_catalog import model_key
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(
            config={'providers': []}))
        self.assertEqual((await manager.perform('providers.suggestionModels', {
            'workspace': str(self.root)}))['providers'], [])
        connections = [{'id': identity, 'module': 'provider-custom',
                        'config': {'base_url': 'https://' + identity + '.invalid',
                                   'api_key': 'synthetic-' + identity}}
                       for identity in ('one', 'two', 'unknown')]
        manager.store.update(self.root, 'global', lambda value: value.update(
            config={'providers': connections}))
        configure(manager, {'enabled': True, 'provider': 'one', 'model': 'Manual/Exact',
                            'prompt': 'Preserved instructions.', 'shareRecentConversation': True},
                  self.root, 'global')
        before = copy.deepcopy(manager.store.read(self.root, 'global'))
        revision = describe(manager, self.root)['revision']
        for identity in ('one', 'two'):
            key = manager.catalog_key({'id': identity}, str(self.root))
            model = identity + '/Current'
            manager.catalog.put(model_key(key), {
                'models': [{'id': model}], 'modelsSupported': True, 'modelsProvenance': 'provider-api',
                'providerMetadata': {'module': 'provider-custom', 'modelListInfo': {
                    'version': 1, 'source': 'provider-api',
                    'purpose_recommendations': {'prompt-suggestions': [model]}}}})
        with patch.object(manager, 'probe', AsyncMock(side_effect=AssertionError('no probe'))) as probe, \
                patch.object(manager, 'discover_catalog', AsyncMock(side_effect=AssertionError('no discovery'))) as discover:
            result = await manager.perform('providers.suggestionModels', {'workspace': str(self.root)})
            rows = {row['id']: row for row in result['providers']}
            for identity in ('one', 'two'):
                self.assertEqual(rows[identity]['suggestionRecommendation']['model'], identity + '/Current')
            self.assertIsNone(rows['unknown']['suggestionRecommendation'])
            self.assertEqual(result['suggestionModels']['model'], 'Manual/Exact')
            self.assertEqual(result['suggestionModels']['revision'], revision)
            # Current identity after an endpoint edit cannot borrow old metadata.
            manager.store.update(self.root, 'global', lambda value:
                                 value['config']['providers'][0]['config'].update(base_url='https://changed.invalid'))
            changed = await manager.perform('providers.suggestionModels', {'workspace': str(self.root)})
            changed_rows = {row['id']: row for row in changed['providers']}
            self.assertIsNone(changed_rows['one']['suggestionRecommendation'])
            self.assertEqual(changed_rows['two']['suggestionRecommendation']['model'], 'two/Current')
            self.assertEqual(manager.store.read(self.root, 'global')['prompt_suggestions'],
                             before['prompt_suggestions'])
        probe.assert_not_called()
        discover.assert_not_called()
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_suggestion_configuration_cas_rejects_stale_compact_sharing_without_side_effects(self):
        from amplifier_web.errors import AppError
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [
            {'id': account, 'module': 'provider-openai',
             'config': {'api_key': 'synthetic-' + account}} for account in ('old', 'new')]}))
        authority = {'scope': 'global', 'configurationTarget': {'kind': 'global'}}
        async def save(values):
            return (await self.app.dispatch('providers.configureSuggestions', {**authority, **values},
                                           include_state=False))['result']['suggestionModels']
        initial = await save({'enabled': True, 'provider': 'old', 'model': 'saved',
                              'prompt': 'Keep this instruction.', 'shareRecentConversation': True})
        # Another authorized client changes the selection before the staged save.
        newer = await save({'model': 'newest', 'expectedSuggestionModelsRevision': initial['revision']})
        before = manager.store.path(self.root, 'global').read_bytes()
        config_revision = self.app.state['configurationRevision']
        preference = copy.deepcopy(self.app.state['settings'])
        host = self.app.prompt_suggestions
        claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
        with patch.object(host, 'invalidate', wraps=host.invalidate) as invalidate, \
                patch.object(manager, 'probe', AsyncMock(side_effect=AssertionError('no provider call'))) as probe:
            with self.assertRaises(AppError) as raised:
                await save({'provider': 'new', 'model': 'staged',
                            'expectedSuggestionModelsRevision': initial['revision']})
            self.assertEqual((raised.exception.status, raised.exception.code),
                             (409, 'suggestion_configuration_changed'))
            self.assertEqual(manager.store.path(self.root, 'global').read_bytes(), before)
            self.assertEqual(self.app.state['configurationRevision'], config_revision)
            self.assertEqual(self.app.state['settings'], preference)
            self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
            invalidate.assert_not_called()
            # The reviewed refreshed selection authorizes this explicit save.
            accepted = await save({'provider': 'new', 'model': 'staged',
                                   'expectedSuggestionModelsRevision': newer['revision']})
            self.assertEqual((accepted['provider'], accepted['model'], accepted['enabled'], accepted['prompt']),
                             ('new', 'staged', True, 'Keep this instruction.'))
            invalidate.assert_called_once()
            probe.assert_not_called()
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_real_action_schema_prompt_only_reset_and_model_only_preserve_all_omitted_settings(self):
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai', 'config': {'api_key': 'synthetic-key'}}]}))
        authority = {'scope': 'global', 'configurationTarget': {'kind': 'global'}}
        async def save(values):
            return (await self.app.dispatch('providers.configureSuggestions', {**authority, **values},
                                           include_state=False))['result']
        initial = (await save({'enabled': True, 'provider': 'saved', 'model': 'utility',
                              'effort': 'low', 'prompt': 'Original advanced prompt.',
                              'shareRecentConversation': True}))['suggestionModels']
        core = ('enabled', 'provider', 'model', 'effort')
        # No fixture merge: these are the exact Advanced form payloads.
        edited = (await save({'prompt': 'Only the prompt changes.'}))['suggestionModels']
        self.assertEqual({key: edited[key] for key in core}, {key: initial[key] for key in core})
        self.assertEqual(edited['prompt'], 'Only the prompt changes.')
        reset = (await save({'prompt': edited['defaultPrompt']}))['suggestionModels']
        self.assertEqual(reset['prompt'], INSTRUCTION)
        self.assertEqual({key: reset[key] for key in core}, {key: initial[key] for key in core})
        changed_model = (await save({'model': 'other-utility'}))['suggestionModels']
        self.assertEqual(changed_model['model'], 'other-utility')
        for key in (*core, 'prompt'):
            if key != 'model':
                self.assertEqual(changed_model[key], reset[key])
        before = copy.deepcopy(manager.config(self.root).settings)
        revision = self.app.state['configurationRevision']
        # Validation runs under the normal writer lock; it must not reach the
        # actual file replacement or change the existing saved bytes.
        before_bytes = manager.store.path(self.root, 'global').read_bytes()
        with patch('amplifier_foundation.settings.atomic_write',
                   side_effect=AssertionError('Invalid settings must not be published')) as write:
            with self.assertRaises(Exception):
                await save({'prompt': ' \n'})
            write.assert_not_called()
        self.assertEqual(manager.config(self.root).settings, before)
        self.assertEqual(manager.store.path(self.root, 'global').read_bytes(), before_bytes)
        self.assertEqual(self.app.state['configurationRevision'], revision)
        self.assertEqual(len({row['revision'] for row in (initial, edited, reset, changed_model)}), 4)
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_prompt_effort_save_reset_validation_and_revision_fence_existing_private_request(self):
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai', 'config': {
                'api_key': 'synthetic-key', 'default_model': 'foreground', 'reasoning_effort': 'foreground-effort'}}]}))
        fields = {'enabled': True, 'provider': 'saved', 'model': 'utility-model',
                  'shareRecentConversation': True,
                  'scope': 'global', 'configurationTarget': {'kind': 'global'}}
        async def save(**values):
            return (await self.app.dispatch('providers.configureSuggestions', {**fields, **values},
                                           include_state=False))['result']
        result = await save(effort='low', prompt='Custom {transcript} prompt.')
        self.assertEqual(result['suggestionModels']['effort'], 'low')
        self.assertEqual(result['suggestionModels']['prompt'], 'Custom {transcript} prompt.')
        first = result['suggestionModels']['revision']
        preserved = await save()
        self.assertEqual(preserved['suggestionModels']['effort'], 'low')
        self.assertEqual(preserved['suggestionModels']['prompt'], 'Custom {transcript} prompt.')
        self.assertNotEqual(first, preserved['suggestionModels']['revision'])
        before = describe(manager, self.root)
        for invalid in ({'prompt': ''}, {'prompt': ' \n'}, {'prompt': 'é' * 8193},
                        {'prompt': 'NUL\0control'}, {'effort': ''}, {'effort': 'bad effort'}, {'effort': 'x' * 101}):
            with self.subTest(invalid=list(invalid)), self.assertRaises(Exception):
                await save(**invalid)
            self.assertEqual(describe(manager, self.root), before)
        reset = await save(effort=None, prompt=before['defaultPrompt'])
        self.assertIsNone(reset['suggestionModels']['effort'])
        self.assertEqual(reset['suggestionModels']['prompt'], INSTRUCTION)
        original_connection = copy.deepcopy(manager.config(self.root).providers[0])
        await self.app.dispatch('session.create', {}, include_state=False)
        session = self.app._session()
        sid = session['id']
        session.update(status='idle', messages=[
            {'id': 'human', 'role': 'user', 'text': 'A request', 'inputOrigin': 'ui'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'A completed reply'}])
        host = self.app.prompt_suggestions
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(record, config, rows):
            entered.set()
            await release.wait()
            return {'status': 'ready', 'suggestion': 'A private candidate.', 'usage': None, 'cost': None}
        host.complete = AsyncMock(side_effect=respond)
        self.app.clients.attach('browser', kind='web')
        self.app.clients.records['browser']['selectedSessionId'] = sid
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            with self.app.clients.bind('browser'):
                await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'pending-old', 'expectedLastMessageId': 'assistant'}, 'ui')
                await entered.wait()
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                changed = await save(effort='high', prompt='New useful instruction.')
                self.assertNotEqual(reset['suggestionModels']['revision'], changed['suggestionModels']['revision'])
                release.set()
                await asyncio.gather(*tuple(host.jobs))
                status = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'pending-old'}, 'ui')
                self.assertEqual(status['status'], 'stale')
                host.complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'New private candidate.',
                                                       'usage': None, 'cost': None})
                await host.dispatch('session.suggestion.request', {
                    'sessionId': sid, 'requestId': 'new-config', 'expectedLastMessageId': 'assistant'}, 'ui')
                await asyncio.gather(*tuple(host.jobs))
                status = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'new-config'}, 'ui')
                self.assertEqual(status['status'], 'ready')  # Same source + new exact config is new intent.
                await save(effort='high', prompt='A later prompt edit.')
                status = await host.dispatch('session.suggestion.status', {
                    'sessionId': sid, 'requestId': 'new-config'}, 'ui')
                self.assertEqual(status['status'], 'stale')
                self.assertEqual(self.app.db.execute('SELECT * FROM suggestion_requests WHERE identity=?',
                                                     (claims[0][0],)).fetchone(), claims[0])
                self.assertEqual(manager.config(self.root).providers[0], original_connection)
        finally:
            release.set()
            BROWSER.reset(token)
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()

    async def test_saved_effort_uses_existing_model_schema_validation_without_discovery(self):
        manager = self.app.prompt_suggestions.manager
        manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai', 'config': {'api_key': 'synthetic-key'}}]}))
        args = {'enabled': True, 'provider': 'saved', 'model': 'utility',
                'shareRecentConversation': True}
        with patch.object(manager.catalog, 'peek', return_value={'providerMetadata': {
                'configSchema': {'fields': [{'id': 'reasoning_effort', 'choices': ['low', 'high']}]}}}), \
                patch.object(manager, 'probe', AsyncMock(side_effect=AssertionError('save must not probe'))) as probe:
            configure(manager, {**args, 'effort': 'low'}, self.root, 'global')
            with self.assertRaisesRegex(ValueError, 'reasoning effort is unavailable'):
                configure(manager, {**args, 'effort': 'unadvertised'}, self.root, 'global')
            configure(manager, {**args, 'effort': None}, self.root, 'global')
        probe.assert_not_called()

    async def test_real_suggestion_roster_uses_exact_global_shared_catalog_keys_without_discovery(self):
        from amplifier_web.management import Management
        from amplifier_web.setup import SetupManager
        from amplifier_web.provider_catalog import configuration_key, model_key
        self.app.management = Management(self.app)
        shared = self.app.management.provider_catalog
        ordinary = SetupManager(self.app.data_dir, catalog=shared, global_only=True)
        connections = [
            {'id': 'saved', 'module': 'provider-openai',
             'source': 'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
             'config': {'api_key': 'synthetic-global-key', 'default_model': 'foreground'}},
            {'id': 'disabled', 'module': 'provider-anthropic', 'enabled': False,
             'config': {'api_key': 'synthetic-disabled-key'}}]
        ordinary.store.update(self.root, 'global', lambda value: value.update(config={'providers': connections}))
        workspace = str(self.app.data_dir)
        expected = {row['id']: ordinary.catalog_key({'id': row['id']}, workspace) for row in connections}
        for identity, key in expected.items():
            shared.put(model_key(key), {'models': [{'id': identity + '-global-model'}]})
        # Same ID is not an identity: workspace overrides and mounted forks
        # must not substitute their catalog for the global connection owner.
        fork = {**connections[0], 'source': 'git+https://example.invalid/provider-fork@main'}
        project = {'config': {'providers': [fork, {
            'id': 'workspace-only', 'module': 'provider-openai', 'config': {'api_key': 'workspace-only-key'}}]}}
        for directory in (self.root, self.app.data_dir):
            ordinary.store.update(directory, 'project', lambda value: value.update(copy.deepcopy(project)))
        local = SetupManager(self.app.data_dir, catalog=shared)
        workspace_key = local.catalog_key({'id': 'saved'}, workspace)
        mount_key = configuration_key(workspace, fork['module'], fork['config'], fork['source'],
                                      home=self.app.data_dir)
        self.assertNotEqual(expected['saved'], workspace_key)
        self.assertNotEqual(expected['saved'], mount_key)
        shared.put(model_key(workspace_key), {'models': [{'id': 'workspace-model'}]})
        shared.put(model_key(mount_key), {'models': [{'id': 'mounted-fork-model'}]})
        before = copy.deepcopy(ordinary.store.read(self.root, 'global'))
        with patch.object(SetupManager, 'probe', AsyncMock(side_effect=AssertionError('no probe'))) as probe, \
                patch.object(SetupManager, 'discover_catalog', AsyncMock(
                    side_effect=AssertionError('no discovery'))) as discover:
            result = (await self.app.dispatch('providers.suggestionModels', {}, include_state=False))['result']
            rows = {row['id']: row for row in result['providers']}
            self.assertEqual(set(rows), set(expected))
            self.assertFalse(rows['disabled']['enabled'])
            self.assertEqual({identity: row['sharedCatalogKey'] for identity, row in rows.items()}, expected)
            for identity, row in rows.items():
                self.assertEqual(shared.peek(model_key(row['sharedCatalogKey']))['models'],
                                 [{'id': identity + '-global-model'}])
            self.assertNotIn(workspace_key, [row['sharedCatalogKey'] for row in rows.values()])
            self.assertNotIn(mount_key, [row['sharedCatalogKey'] for row in rows.values()])
            for connection in connections:
                self.assertNotIn(connection['config']['api_key'], json.dumps(result))
            self.assertNotIn('workspace-only-key', json.dumps(result))
        probe.assert_not_called()
        discover.assert_not_called()
        self.assertEqual(ordinary.store.read(self.root, 'global'), before)
        self.runtime.start.assert_not_called()
        self.runtime.send.assert_not_called()
        await ordinary.close()
        await local.close()

    async def test_real_shared_management_catalog_validates_global_effort_before_write_without_probe(self):
        from amplifier_web.management import Management
        from amplifier_web.setup import SetupManager
        from amplifier_web.provider_catalog import model_key
        # Exercise an early headless read too: later normal app assembly must
        # bind the same existing utility manager to Management's shared owner.
        early = self.app.prompt_suggestions.manager
        self.app.management = Management(self.app)
        shared = self.app.management.provider_catalog
        ordinary = SetupManager(self.app.data_dir, catalog=shared, global_only=True)
        ordinary.store.update(self.root, 'global', lambda value: value.update(config={'providers': [{
            'id': 'saved', 'module': 'provider-openai',
            'source': 'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
            'config': {'api_key': 'synthetic-key', 'default_model': 'foreground-model'}}]}))
        key = model_key(ordinary.catalog_key({'id': 'saved'}, str(self.app.data_dir)))
        shared.put(key, {'models': [{'id': 'utility'}], 'modelsProvenance': 'provider-api',
                         'providerMetadata': {'configSchema': {'fields': [{
                             'id': 'reasoning_effort', 'choices': ['low', 'high']}]}}})
        args = {'enabled': True, 'provider': 'saved', 'model': 'utility',
                'shareRecentConversation': True, 'scope': 'global',
                'configurationTarget': {'kind': 'global'}}
        with patch.object(SetupManager, 'probe', AsyncMock(side_effect=AssertionError('no discovery'))) as probe:
            accepted = await self.app.dispatch('providers.configureSuggestions', {**args, 'effort': 'low'},
                                               include_state=False)
            self.assertEqual(accepted['result']['suggestionModels']['effort'], 'low')
            before = copy.deepcopy(ordinary.config(self.root).settings)
            revision = self.app.state['configurationRevision']
            before_bytes = early.store.path(self.root, 'global').read_bytes()
            with patch('amplifier_foundation.settings.atomic_write',
                       side_effect=AssertionError('Invalid effort must not be published')) as write:
                with self.assertRaisesRegex(Exception, 'reasoning effort is unavailable'):
                    await self.app.dispatch('providers.configureSuggestions', {**args, 'effort': 'unadvertised'},
                                            include_state=False)
                write.assert_not_called()
            self.assertEqual(ordinary.config(self.root).settings, before)
            self.assertEqual(early.store.path(self.root, 'global').read_bytes(), before_bytes)
            self.assertEqual(self.app.state['configurationRevision'], revision)
            self.assertIs(self.app.prompt_suggestions.manager, early)
            self.assertIs(early.catalog, shared)
            # Utility teardown owns its finite probes/logins, not shared
            # discovery tasks or their lifetime.
            with patch.object(shared, 'close', AsyncMock()) as close:
                await self.app.prompt_suggestions.close()
                close.assert_not_called()
            self.assertEqual(shared.peek(key)['models'][0]['id'], 'utility')
        probe.assert_not_called()
        await ordinary.close()

    async def test_effective_prompt_constants_change_revision_even_with_custom_saved_prompt(self):
        manager = self.app.prompt_suggestions.manager
        configure(manager, {'enabled': False, 'transcriptMode': 'last_n', 'humanTurns': 6,
                            'prompt': 'Custom experiment.', 'effort': None}, self.root, 'global')
        before = describe(manager, self.root)
        with patch('amplifier_web.suggestion_settings.INSTRUCTION', INSTRUCTION + ' Updated default.'):
            self.assertNotEqual(describe(manager, self.root)['revision'], before['revision'])
        with patch('amplifier_web.suggestion_settings.OUTPUT_CONTRACT', OUTPUT_CONTRACT + ' Updated contract.'):
            self.assertNotEqual(describe(manager, self.root)['revision'], before['revision'])

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
                                         'shareRecentConversation': True}, self.root, 'global')
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
                return web.json_response({'error': str(exc)}, status=exc.status)
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
            synced = {'action': 'settings.update', 'args': {'patch': {'suggestedMessages': False}}}
            for invalid_cookie in ('invalid-cookie', ''):
                response = await client.post('/api/actions', json=synced, headers=headers(invalid_cookie))
                self.assertEqual(response.status, 401)
                self.assertTrue(self.app.state['settings']['suggestedMessages'])
            # Both valid owner sign-ins share preferences, not private
            # candidates. No change to cookie-bound candidate ownership.
            response = await client.post('/api/actions', json=synced, headers=headers(cookie_two, 'two'))
            self.assertEqual(response.status, 200)
            self.assertFalse(self.app.state['settings']['suggestedMessages'])
            with self.app.clients.bind('one'):
                self.assertFalse(self.app.browser_state()['settings']['suggestedMessages'])
            response = await client.post('/api/actions', json={
                **synced, 'args': {'patch': {'suggestedMessages': True}}}, headers=headers(cookie_one))
            self.assertEqual(response.status, 200)
            host.complete.assert_not_called()
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
            calls = host.complete.await_count
            # Superseded manual API is rejected in the real action schema,
            # including false: no silent legacy compatibility path remains.
            for invalid in (True, False, 'true', 1, None):
                response = await client.post('/api/actions', json={
                    'action': 'session.suggestion.request',
                    'args': {**args, 'requestId': 'manual', 'regenerate': invalid}},
                    headers=headers(cookie_one))
                self.assertEqual(response.status, 400)
            response = await client.post('/api/actions', json={
                'action': 'session.suggestion.request', 'args': {**args, 'requestId': 'automatic-recovery'}},
                headers=headers(cookie_one, 'two'))
            self.assertEqual((await response.json())['result']['status'], 'pending')
            await asyncio.gather(*tuple(host.jobs))
            response = await client.post('/api/actions', json={'action': 'session.suggestion.status', 'args': {
                'sessionId': session['id'], 'requestId': 'automatic-recovery'}}, headers=headers(cookie_one, 'two'))
            body = (await response.json())['result']
            self.assertEqual(body['status'], 'ready')
            self.assertEqual(body['retention'], 'source')
            self.assertIsNone(body['expiresAt'])
            self.assertTrue(body['reused'])
            self.assertEqual(host.complete.await_count, calls)

    async def test_cookie_owner_can_cancel_pending_request_after_real_navigation_but_cannot_read_old_chat(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from amplifier_web.auth import auth_required, new_session, SESSION_COOKIE, Sessions
        from amplifier_web.live_clients import client_context
        from amplifier_web.errors import AppError
        await self.app.dispatch('session.create', {}, include_state=False)
        old = self.app._session()
        sid = old['id']
        old.update(status='idle', messages=[
            {'id': 'human', 'role': 'user', 'text': 'Human', 'inputOrigin': 'ui'},
            {'id': 'assistant', 'role': 'assistant', 'text': 'Completed reply'}])
        await self.app.dispatch('session.create', {}, include_state=False)
        other_sid = self.app._session()['id']
        host = self.app.prompt_suggestions
        host.settings = lambda: ({'enabled': True, 'provider': 'saved', 'model': 'utility',
                                  'humanTurns': 6, 'transcriptMode': 'last_n'}, {'id': 'saved'}, 'revision')
        entered, cleanup_entered, release, settled = (asyncio.Event() for _ in range(4))
        async def pending(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_entered.set()
                await release.wait()
                raise
            finally:
                settled.set()
        host.complete = AsyncMock(side_effect=pending)
        for identity in ('one', 'foreign'):
            self.app.clients.attach(identity, kind='web')
            self.app.clients.records[identity]['selectedSessionId'] = sid
        @web.middleware
        async def errors(request, handler):
            try:
                return await handler(request)
            except AppError as exc:
                return web.json_response({'error': str(exc)}, status=exc.status)
        http = web.Application(middlewares=[errors, auth_required, client_context])
        http.update(service=self.app, session_secret='synthetic-secret', control_token='control',
                    server_config={'session_ttl_seconds': 600}, sessions=Sessions(), allowed_origins=set())
        async def endpoint(request):
            payload = await request.json()
            return web.json_response(await self.app.dispatch(payload['action'], payload['args'], include_state=False))
        http.router.add_post('/api/actions', endpoint)
        cookie, foreign_cookie = new_session('synthetic-secret'), new_session('synthetic-secret')
        def headers(value=cookie, identity='one', bearer=False):
            return {'X-Amplifier-Client': identity, 'Cookie': SESSION_COOKIE + '=' + value,
                    **({'Authorization': 'Bearer control'} if bearer else {})}
        args = {'sessionId': sid, 'requestId': 'owned', 'expectedLastMessageId': 'assistant'}
        try:
            async with TestClient(TestServer(http)) as client:
                async def post(action, values, **header_values):
                    response = await client.post('/api/actions', json={'action': action, 'args': values},
                                                 headers=headers(**header_values))
                    return response.status, await response.json()
                code, result = await post('session.suggestion.request', args)
                self.assertEqual(code, 200)
                self.assertEqual(result['result']['status'], 'pending')
                await asyncio.wait_for(entered.wait(), 5)
                claims = list(self.app.db.execute('SELECT * FROM suggestion_requests'))
                code, _ = await post('session.select', {'id': other_sid})
                self.assertEqual(code, 200)
                self.assertEqual(self.app.clients.records['one']['selectedSessionId'], other_sid)
                cancel_args = {'sessionId': sid, 'requestId': 'owned'}
                # Different principal/client or a guessed request cannot affect
                # the original pending task or reveal its source/candidate.
                for values, header_values in (
                        (cancel_args, {'value': foreign_cookie}),
                        (cancel_args, {'identity': 'foreign'}),
                        ({**cancel_args, 'requestId': 'unknown'}, {})):
                    code, result = await post('session.suggestion.cancel', values, **header_values)
                    self.assertEqual(code, 200)
                    self.assertEqual(result['result']['status'], 'expired')
                    self.assertIsNone(result['result']['sourceKey'])
                    self.assertIsNone(result['result']['suggestion'])
                    self.assertFalse(cleanup_entered.is_set())
                code, _ = await post('session.suggestion.cancel', cancel_args, bearer=True)
                self.assertEqual(code, 403)
                self.assertFalse(cleanup_entered.is_set())
                # Request/status remain display-selection-bound after leaving A.
                for action, values in (('session.suggestion.request', args),
                                       ('session.suggestion.status', cancel_args)):
                    code, _ = await post(action, values)
                    self.assertEqual(code, 403)
                code, result = await post('session.suggestion.cancel', cancel_args)
                self.assertEqual(code, 200)
                self.assertEqual(result['result']['status'], 'cancelled')
                self.assertIsNone(result['result']['suggestion'])
                await asyncio.wait_for(cleanup_entered.wait(), 5)
                self.assertFalse(settled.is_set())
                self.assertTrue(host.jobs, 'owned slot remains until physical cleanup settles')
                release.set()
                await asyncio.gather(*tuple(host.jobs))
                self.assertTrue(settled.is_set())
                self.assertFalse(host.jobs)
                host.complete.assert_awaited_once()
                self.assertEqual(list(self.app.db.execute('SELECT * FROM suggestion_requests')), claims)
                code, _ = await post('session.suggestion.status', cancel_args)
                self.assertEqual(code, 403)
        finally:
            release.set()
            for task in tuple(host.jobs):
                task.cancel()
            if host.jobs:
                await asyncio.gather(*tuple(host.jobs), return_exceptions=True)

    async def test_owned_probe_handshake_revokes_after_subprocess_creation(self):
        """Real process/pipes/cleanup; synthetic provider and zero network."""
        from amplifier_web.setup import SetupManager
        from amplifier_web.update_drain import held
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
            allowed = not held(self.app)
            if count == 1:
                # Input was valid at preparation, but actual update release
                # begins before the subprocess asks for its final grant.
                self.app.state.setdefault('updates', {})['drain'] = {
                    'phase': 'releasing', 'attemptId': 'synthetic-update',
                    'releasingInput': ['other-chat', 'earlier-input']}
            return allowed
        try:
            result = await manager.probe('suggestion.complete', {
                'id': 'fixture', 'model': 'explicit', 'transcript': [], '_admit': admit}, str(self.root))
            self.assertEqual(result['status'], 'stale')
            self.assertIsNone(result['suggestion'])
            self.assertEqual(count, 2)
        finally:
            self.app.state.get('updates', {}).pop('drain', None)
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


class SuggestionConfigurationCAS(unittest.TestCase):
    """Real settings writer/lock with synthetic configuration and no providers."""

    def setUp(self):
        from amplifier_web.setup import SetupManager
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AMPLIFIER_HOME': str(self.root / 'shared'),
                                          'AMPLIFIER_WEB_HOME': str(self.root / 'app')})
        self.env.start()
        self.manager = SetupManager(self.root / 'app', global_only=True)
        self.manager.store.update(self.root, 'global', lambda value: value.update(config={'providers': [
            {'id': 'saved', 'module': 'provider-openai', 'config': {
                'api_key': 'synthetic-key', 'base_url': 'https://fixture.invalid'}}]}))
        self.initial = configure(self.manager, {'enabled': True, 'provider': 'saved', 'model': 'original',
                                               'shareRecentConversation': True,
                                               'prompt': 'Preserve me.'}, self.root, 'global')['suggestionModels']

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_compare_and_merge_use_fresh_locked_settings_not_earlier_read_snapshot(self):
        from amplifier_web.errors import AppError
        update = self.manager.store.update
        def interleaved(workspace, scope, mutator, **kwargs):
            # Another writer wins before this operation acquires the canonical
            # lock; the caller's outer inventory snapshot is now stale.
            update(workspace, scope, lambda value:
                   value['prompt_suggestions'].update(prompt='Newer settings.'))
            return update(workspace, scope, mutator, **kwargs)
        with self.manager.read_snapshot(self.root):
            snapshot = self.manager._config_snapshot
            with patch.object(self.manager.store, 'update', side_effect=interleaved):
                with self.assertRaises(AppError) as raised:
                    configure(self.manager, {'model': 'new',
                        'expectedSuggestionModelsRevision': self.initial['revision']}, self.root, 'global')
            self.assertIs(self.manager._config_snapshot, snapshot)
        self.assertEqual(raised.exception.status, 409)
        actual = describe(self.manager, self.root)
        self.assertEqual((actual['model'], actual['prompt']), ('original', 'Newer settings.'))
        with self.manager.read_snapshot(self.root):
            snapshot = self.manager._config_snapshot
            accepted = configure(self.manager, {'model': 'new',
                'expectedSuggestionModelsRevision': actual['revision']}, self.root, 'global')['suggestionModels']
            self.assertIs(self.manager._config_snapshot, snapshot)
        self.assertEqual((accepted['model'], accepted['enabled'], accepted['prompt']),
                         ('new', True, 'Newer settings.'))
        self.assertEqual(accepted, describe(self.manager, self.root))

    def test_two_competing_writers_with_same_revision_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from amplifier_web.errors import AppError
        from amplifier_web.setup import SetupManager
        barrier = Barrier(2)
        def writer(model):
            manager = SetupManager(self.root / 'app', global_only=True)
            update = manager.store.update
            def together(*args, **kwargs):
                barrier.wait(timeout=5)
                return update(*args, **kwargs)
            with patch.object(manager.store, 'update', side_effect=together):
                try:
                    result = configure(manager, {'model': model,
                        'expectedSuggestionModelsRevision': self.initial['revision']}, self.root, 'global')
                    return 'saved', result['suggestionModels']['model']
                except AppError as exc:
                    return exc.status, exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, ('first', 'second')))
        self.assertEqual(sum(status == 'saved' for status, _ in results), 1)
        self.assertEqual(sum(status == 409 for status, _ in results), 1)
        saved = next(value for status, value in results if status == 'saved')
        actual = describe(self.manager, self.root)
        self.assertEqual((actual['model'], actual['enabled'], actual['prompt']),
                         (saved, True, 'Preserve me.'))

    def test_same_account_binding_edit_and_a_b_a_are_not_old_confirmation(self):
        from amplifier_web.errors import AppError
        self.manager.store.update(self.root, 'global', lambda value:
                                 value['config']['providers'][0]['config'].update(base_url='https://changed.invalid'))
        with self.assertRaises(AppError):
            configure(self.manager, {'model': 'new',
                'expectedSuggestionModelsRevision': self.initial['revision']}, self.root, 'global')
        current = describe(self.manager, self.root)
        full = configure(self.manager, {'model': 'temporary',
            'expectedSuggestionModelsRevision': current['revision']}, self.root, 'global')['suggestionModels']
        recent = configure(self.manager, {'model': 'original',
            'expectedSuggestionModelsRevision': full['revision']}, self.root, 'global')['suggestionModels']
        self.assertNotEqual(current['revision'], recent['revision'])
        before = self.manager.store.path(self.root, 'global').read_bytes()
        with self.assertRaises(AppError):
            configure(self.manager, {'model': 'new',
                'expectedSuggestionModelsRevision': current['revision']}, self.root, 'global')
        self.assertEqual(self.manager.store.path(self.root, 'global').read_bytes(), before)


class Recommendations(unittest.TestCase):
    """Only synthetic metadata and a memory catalog; no provider construction."""

    def setUp(self):
        from amplifier_web.provider_catalog import ProviderCatalog
        self.manager = SimpleNamespace(catalog=ProviderCatalog())
        self.row = {'id': 'account-a', 'module': 'provider-custom',
                    'enabled': True, 'sharedCatalogKey': 'exact-account-a'}

    def put(self, *, key='exact-account-a', models=None, ranked=None,
            provenance='provider-api', module='provider-custom', **values):
        from amplifier_web.provider_catalog import model_key
        self.manager.catalog.put(model_key(key), {
            'models': models if models is not None else [{'id': 'Current/Exact'}],
            'modelsSupported': True, 'modelsProvenance': provenance,
            'providerMetadata': {'module': module, 'modelListInfo': {
                'version': 1, 'source': provenance,
                'purpose_recommendations': {'prompt-suggestions': ranked if ranked is not None else ['Current/Exact']}}},
            **values})

    def get(self):
        from amplifier_web.suggestion_settings import recommendation
        return recommendation(self.manager, self.row)

    def test_unknown_module_uses_its_exact_current_purpose_ranking(self):
        for family in ('Luna', 'Haiku', 'Company'):
            with self.subTest(family=family):
                current = family + '/Current-Exact'
                self.put(models=[{'id': family + '/old'}, {'id': current}],
                         ranked=[family + '/retired', current])
                self.assertEqual(self.get(), {
                    'model': current, 'provenance': 'provider-api', 'basis': 'provider-purpose'})

    def test_no_module_metadata_means_no_recommendation_not_inventory_order(self):
        self.assertIsNone(self.get())
        self.put(providerMetadata={'module': 'provider-custom', 'info': {
            'display_name': 'OpenAI Luna Anthropic Haiku', 'defaults': {'model': 'Current/Exact'}}})
        self.assertIsNone(self.get())

    def test_exact_case_aliases_and_available_ids_only(self):
        self.put(ranked=['current/exact', 'Retired-Model'])
        self.assertIsNone(self.get())
        self.put(models=[])
        self.assertIsNone(self.get())
        self.put(models=[{'id': 'Current/Exact'}, {'id': 'Current/Exact'}])
        self.assertIsNone(self.get())

    def test_account_module_disabled_and_signin_boundaries_cannot_donate_models(self):
        self.put(key='other-account')
        self.assertIsNone(self.get())
        self.put(module='provider-other')
        self.assertIsNone(self.get())
        self.put()
        for field, value in (('sharedCatalogKey', 'missing'), ('enabled', False),
                             ('authenticationRequired', True)):
            with self.subTest(field=field), patch.dict(self.row, {field: value}):
                self.assertIsNone(self.get())
        self.assertIsNotNone(self.get())

    def test_configured_static_and_simulated_origins_stay_honest(self):
        for provenance in ('configured', 'static', 'simulated', 'unsupported', None):
            self.put(provenance=provenance)
            with self.subTest(provenance=provenance):
                value = self.get()
                if provenance in {'configured', 'static'}:
                    self.assertEqual(value['provenance'], provenance)
                else:
                    self.assertIsNone(value)

    def test_failed_or_stale_catalog_never_recommends_retained_prior_inventory(self):
        from amplifier_web.provider_catalog import model_key
        self.put()
        key = model_key(self.row['sharedCatalogKey'])
        self.manager.catalog.failed_at[key] = 1
        self.assertIsNone(self.get())
        self.manager.catalog.failed_at.clear()
        self.manager.catalog.ttl = 0
        self.assertIsNone(self.get())

    def test_malformed_optional_metadata_fails_closed_without_breaking_settings(self):
        for metadata in ([], 'bad', {'module': 'provider-custom', 'modelListInfo': []},
                         {'module': 'provider-custom', 'modelListInfo': {'version': True}},
                         {'module': 'provider-custom', 'modelListInfo': {
                             'version': 2, 'source': 'provider-api'}}):
            with self.subTest(metadata=metadata):
                self.put(providerMetadata=metadata)
                self.assertIsNone(self.get())
        for ranked in ([], [''], ['x'] * 2, ['x'] * 33, ['x' * 201], [None], 'Current/Exact'):
            with self.subTest(ranked=ranked):
                self.put(ranked=ranked)
                self.assertIsNone(self.get())
        for models in (1, {'id': 'Current/Exact'}, 'Current/Exact'):
            with self.subTest(models=models):
                self.put(models=models)
                self.assertIsNone(self.get())

    def test_recommendation_read_never_copies_or_encodes_large_model_bodies(self):
        from amplifier_web import provider_catalog as catalog
        key = catalog.model_key(self.row['sharedCatalogKey'])
        dumps = json.dumps
        def key_encoding_only(value, *args, **kwargs):
            self.assertEqual(value, key, 'Only the fixed catalog key may be encoded during projection')
            return dumps(value, *args, **kwargs)
        for count in (1, 1800):
            for ranked in (False, True):
                with self.subTest(rows=count, ranked=ranked), tempfile.TemporaryDirectory() as root:
                    self.manager.catalog = catalog.ProviderCatalog(Path(root) / 'catalog.json')
                    values = {} if ranked else {'providerMetadata': {'module': self.row['module']}}
                    self.put(models=[{'id': 'Current/Exact' if index == 0 else str(index),
                                      'opaque': {'body': 'x' * 1536, 'nested': list(range(10))}}
                                     for index in range(count)], **values)
                    # Constructor/disk decoding retain their existing cost.
                    # Both the resident and lazy-restored *read* must avoid a
                    # bulk deepcopy, JSON encode or extra cache hydration.
                    for restored in (False, True):
                        if restored:
                            self.manager.catalog = catalog.ProviderCatalog(self.manager.catalog.path)
                            self.assertFalse(self.manager.catalog.entries)
                        with patch.object(catalog.copy, 'deepcopy', side_effect=AssertionError('bulk copy')), \
                                patch.object(catalog.json, 'dumps', side_effect=key_encoding_only):
                            for _ in range(3):
                                value = self.get()
                                self.assertEqual(value is not None, ranked)
                                if ranked:
                                    self.assertEqual(value['model'], 'Current/Exact')
                                    value['model'] = 'caller edit'
                        if restored:
                            self.assertFalse(self.manager.catalog.entries, 'Scalar reads do not hydrate model bodies')
                    # Existing whole-catalog readers still return detached
                    # content and can hydrate the lazy record normally.
                    view = self.manager.catalog.peek(key)
                    self.assertEqual(len(view['models']), count)
                    view['models'].clear()
                    self.assertEqual(len(self.manager.catalog.peek(key)['models']), count)

    def test_absent_purpose_metadata_never_walks_model_inventory(self):
        class GuardedRows(list):
            blocked = False
            def __iter__(self):
                if self.blocked:
                    raise AssertionError('Negative projection walked model rows')
                return super().__iter__()
        self.put(models=GuardedRows([{'id': 'Current/Exact'}]),
                 providerMetadata={'module': self.row['module'], 'modelListInfo': {
                     'version': 1, 'source': 'provider-api'}})
        GuardedRows.blocked = True
        try:
            for _ in range(3):
                self.assertIsNone(self.get())
        finally:
            GuardedRows.blocked = False

    def test_lazy_projection_preserves_freshness_and_retention_boundaries(self):
        from amplifier_web import provider_catalog as catalog
        now = [1000.]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'catalog.json'
            self.manager.catalog = catalog.ProviderCatalog(path, clock=lambda: now[0])
            self.put()
            cache = catalog.ProviderCatalog(path, ttl=60, max_age=120, clock=lambda: now[0])
            self.manager.catalog = cache
            self.assertIsNotNone(self.get())
            self.assertFalse(cache.entries)
            now[0] += 61
            self.assertIsNone(self.get(), 'Stale disk data is not a recommendation')
            self.assertTrue(cache.saved, 'TTL is not deletion')
            now[0] += 60
            self.assertIsNone(self.get(), 'Retired disk data cannot project')
            self.assertFalse(cache.saved)
            self.assertIsNone(cache.peek(catalog.model_key(self.row['sharedCatalogKey'])))


if __name__ == '__main__':
    unittest.main()