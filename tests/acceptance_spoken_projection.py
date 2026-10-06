"""Installed ordinary SDK receiving + public browser projection continuation.

No prior result is rewritten or relabelled. No media/hearing qualification.
The late-call lifecycle is a synthetic disconnected call against real work.
"""
import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sys
import uuid

spec = importlib.util.spec_from_file_location('spoken_helper',
    Path(__file__).with_name('acceptance_spoken_provenance.py'))
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
base = previous.base


class Harness(previous.Harness):
    def __init__(self, root, browser_script):
        super().__init__(root)
        self.browser_script = browser_script
        self.result['scenario'] = 'spoken-projection-continuation'
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.public_runner = None
        self.browser = None

    async def response_ready(self, index):
        if index in {2, 5}:
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), 60)

    async def run(self):
        import yaml
        from amplifier_web.voice_messages import RESPONSE_METADATA, project_message, retain_receipt
        from amplifier_web.automatic_history import display_message
        from amplifier_web.conversation_export import messages, snapshot
        from amplifier_web.browser_detail import page, read_text
        from amplifier_web.history_query import query_history
        from amplifier_web.voice import VoiceCall, VoiceService
        self.credentials_absent()
        app_home, workspace = self.configure_home()
        self.sources = self.installed_sources()
        url = await self.server()
        base.private_write(self.root / 'native/settings.yaml', yaml.safe_dump({
            'bundle': {'app': []}, 'sources': {'modules': {
                'hook-context-intelligence': self.sources['amplifier_module_hook_context_intelligence']}}}))
        bundle = self.bundle(self.sources, 'provider-openai', {
            'api_key': 'offline-fixture-not-a-secret', 'base_url': url,
            'default_model': base.MODEL, 'use_streaming': False, 'max_retries': 0, 'timeout': 10,
        }, 'projection.yaml')
        service, runtime = self.service(app_home, workspace)
        sid = str(uuid.uuid4())
        self.result['session_id'] = sid
        await service.dispatch('session.create', {'id': sid, 'workspace': str(workspace),
            'bundle': str(bundle), 'title': 'Ordinary spoken projection acceptance'}, include_state=False)
        await service.dispatch('session.naming', {'id': sid, 'automatic': False}, include_state=False)
        self.stage('ordinary_private')
        admitted = await service.voice_delegate(base.WRAPPER, base.VOICE_ID, sid,
            call_id=base.CALL_ID, delegation_id=base.DELEGATION_ID)
        answer = await service.wait_for_response(sid, input_id=base.VOICE_ID, timeout=60)
        self.remember_processes(runtime)
        session = service._session(sid)
        receipt = session['voiceResponses'][answer['generation_id']]
        self.check('actual_private_input_accepted', admitted.get('accepted') is True)
        self.check('ordinary_exclusive_receipt', receipt['ownership'] == 'exclusive-private-voice')
        path, canonical = self.transcript(workspace, sid)
        raw = path.read_bytes()
        saved = [display_message(row, index, session) for index, row in enumerate(canonical)]
        saved = [row for row in saved if row and row.get('voiceResponseRef')]
        self.check('saved_relay_exact_append', len(saved) == 1 and
            saved[0]['voiceResponseRef']['appendId'] in receipt['appendIds'])
        self.check('saved_relay_projected', project_message(session, saved[0]).get('presentation') == 'backend-relay')
        live = [row for row in session['messages'] if row.get('role') == 'assistant']
        self.check('independent_live_relay_projected', bool(live) and
            all(project_message(session, row).get('presentation') == 'backend-relay' for row in live))
        self.check('public_page_body_removed', all(row.get('text') != base.ANSWER
            for row in page(session, 'messages')['items']))
        self.check('written_detail_original_body', read_text(session,
            project_message(session, live[0])['relayTextDetail'])['value'] == base.ANSWER)
        self.check('export_omits_private_relay', base.ANSWER not in snapshot(app_home, session, [])[0])
        self.check('projection_does_not_change_canonical_bytes', path.read_bytes() == raw)
        self.result['first_receipt'] = receipt
        self.stage('mixed_after_early_append')
        mixed_id = f'voice:{base.CALL_ID}:mixed_projection'
        await service.voice_delegate(base.WRAPPER, mixed_id, sid,
            call_id=base.CALL_ID, delegation_id='mixed_projection')
        await asyncio.wait_for(self.entered.wait(), 60)
        typed_id = 'actual-typed-steering'
        await service.dispatch('conversation.send', {'sessionId': sid, 'text': 'Apply a typed correction.'},
            command_id=typed_id, include_state=False)
        self.release.set()
        mixed = await service.wait_for_response(sid, input_id=typed_id, timeout=60)
        self.remember_processes(runtime)
        mixed_receipt = session['voiceResponses'][mixed['generation_id']]
        self.check('mixed_actual_complete_applied_set', mixed['input_ids'] == [mixed_id, typed_id])
        self.check('mixed_final_public', mixed_receipt['ownership'] == 'public-mixed')
        _, canonical = self.transcript(workspace, sid)
        early = [(i, row) for i, row in enumerate(canonical)
            if row.get('metadata', {}).get(RESPONSE_METADATA, {}).get('generationId') == mixed['generation_id']]
        self.check('actual_early_append_precedes_typed_apply', len(early) == 1 and
            early[0][1]['metadata'][RESPONSE_METADATA]['inputIds'] == [mixed_id])
        early_display = display_message(early[0][1], early[0][0], session)
        self.check('mixed_early_append_restored_public', project_message(session, early_display)['text'] == base.ANSWER)
        # An explicit stale-receipt adversary is a synthetic host-state check,
        # not a worker receipt or an extra model execution.
        synthetic = copy.deepcopy(session)
        retain_receipt(synthetic, {**mixed_receipt, 'inputIds': [mixed_id],
                                   'ownership': 'exclusive-private-voice'})
        self.check('synthetic_stale_exclusive_does_not_rehide', project_message(synthetic, early_display)['text'] == base.ANSWER)
        self.result['mixed_receipt'] = mixed_receipt
        self.stage('typed_identical_wrapper')
        await self.send(service, runtime, sid, 'typed-identical-wrapper', base.WRAPPER)
        self.check('typed_identical_wrapper_remains_public',
            any(row.get('role') == 'user' and row.get('text') == base.WRAPPER for row in messages(app_home, session)))
        self.stage('late_disconnected_call_real_work')
        self.entered.clear()
        self.release.clear()
        manager = VoiceService(service)
        call = VoiceCall(manager, sid)
        call.id, call.provider, call.client_id = 'late_original', 'live', 'synthetic-original-client'
        task = asyncio.create_task(call.execute('Continue after this call ends.', 'late_request'))
        await asyncio.wait_for(self.entered.wait(), 60)
        await call.close()  # No socket/no audio route. Accepted backend work continues.
        self.release.set()
        late = await asyncio.wait_for(task, 60)
        self.remember_processes(runtime)
        outcome = session['voiceCalls'][call.id]['outcomes'][late['generation_id']]
        self.check('late_real_work_verified_no_attempt', outcome['state'] == 'late-no-attempt')
        late_rows = [row for row in session['messages'] if row.get('generationId') == late['generation_id']]
        self.check('late_written_fallback_expands_original_only', bool(late_rows) and
            all(project_message(session, row)['writtenFallback']['expanded'] for row in late_rows))
        self.check('earlier_unknown_audio_stays_collapsed',
            not project_message(session, live[0])['writtenFallback']['expanded'])
        self.check('actual_sdk_post_count_five', len(self.posts()) == 5)
        self.check('no_unexpected_endpoints', not any(row.get('unexpected') for row in self.requests))
        _, canonical = self.transcript(workspace, sid)
        ids = [row.get('metadata', {}).get('amplifier_input', {}).get('id') for row in canonical if row.get('role') == 'user']
        for identity in [base.VOICE_ID, mixed_id, typed_id, 'typed-identical-wrapper', 'voice:late_original:late_request']:
            self.check('canonical_input_once_' + identity, ids.count(identity) == 1)
        search = await query_history(service, {'action': 'search', 'query': base.ANSWER}, sid)
        self.check('search_has_only_public_mixed_or_typed_answers',
            all(match['message_id'] not in {row['id'] for row in live}
                for item in search['items'] for match in item.get('matches', [])))
        self.result['late_outcome'] = outcome
        self.result['canonical_sha256_before_browser'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.stage('restart_browser')
        await runtime.stop(sid)
        await service.close()
        from aiohttp import web
        from amplifier_web.server import create_app
        from amplifier_web.deployment import validate_server
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        sock.setblocking(False)
        origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
        config = validate_server({'bind': ['127.0.0.1'], 'port': sock.getsockname()[1], 'public_origins': [origin]})
        public_app = await create_app(app_home, workspace=workspace, voice=False,
            background_updates=False, preload_providers=False, server_config=config)
        reopened = public_app['service']
        self.services.append(reopened)
        self.public_runner = web.AppRunner(public_app, access_log=None)
        await self.public_runner.setup()
        await web.SockSite(self.public_runner, sock).start()
        self.check('restart_receipt_exact', reopened._session(sid)['voiceResponses'][answer['generation_id']] == receipt)
        self.check('restart_call_outcome_exact', reopened._session(sid)['voiceCalls'][call.id]['outcomes'][late['generation_id']] == outcome)
        self.browser = await asyncio.create_subprocess_exec('node', str(self.browser_script),
            env={**os.environ, 'PROJECTION_URL': origin, 'PROJECTION_TOKEN': public_app['control_token'],
                 'PROJECTION_SESSION': sid, 'PROJECTION_OUTPUT': str(self.root / 'browser-result.json')},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, error = await asyncio.wait_for(self.browser.communicate(), 90)
        base.private_write(self.root / 'browser.log', output.decode() + error.decode())
        self.result['browser_exit_code'] = self.browser.returncode
        self.check('installed_browser_exit_zero', self.browser.returncode == 0)
        self.check('browser_reads_no_canonical_changes',
            hashlib.sha256(path.read_bytes()).hexdigest() == self.result['canonical_sha256_before_browser'])
        self.result['limitations'] = [
            'Ordinary context-simple/OpenAI SDK loopback only; native official endpoint guard unchanged and unqualified.',
            'Synthetic disconnected VoiceCall lifecycle against real accepted work; no negotiated media or hearing proof.',
            'Static synthetic cases are separate from the five actual SDK POSTs.',
            'This harness does not qualify minimal artifact-export identifier omission.',
            'Direct installed Python worker; the default launcher is outside this harness.',
        ]
        self.stage('verified_supported_increment')

    async def cleanup(self):
        if self.browser and self.browser.returncode is None:
            self.browser.terminate()
            await self.browser.wait()
        if self.public_runner:
            await self.public_runner.cleanup()
        await super().cleanup()


async def execute(root, script):
    harness = Harness(root, script)
    try:
        await asyncio.wait_for(harness.run(), 300)
        harness.result['status'] = 'passed-supported-increment'
    except BaseException as exc:
        harness.record_failure(exc)
        harness.result['status'] = 'failed'
    finally:
        await harness.cleanup()
        base.private_write(root / 'result.json', json.dumps(harness.result, indent=2) + '\n')
    print(json.dumps({'status': harness.result['status'], 'stage': harness.result['stage'],
                      'result': str(root / 'result.json')}))
    return 0 if harness.result['status'] == 'passed-supported-increment' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--browser-script', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    base.private_write(root / 'pid', str(os.getpid()))
    return asyncio.run(execute(root, args.browser_script.resolve()))


if __name__ == '__main__':
    sys.exit(main())