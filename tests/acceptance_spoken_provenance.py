"""Receiving boundary: real private voice -> worker -> append -> checkpoint/resume.

An installed, credential-free SDK loopback fixture. No audio/hearing claim.
Each run uses a new isolated directory; earlier receipts are never overwritten.
The installed CI hook is selected before first mount.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import uuid

spec = importlib.util.spec_from_file_location(
    'foundations_acceptance_helper', Path(__file__).with_name('acceptance_unified_foundations.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class Harness(base.Harness):
    def __init__(self, root):
        super().__init__(root, 'full')
        self.scenario = 'spoken-provenance'
        self.result['scenario'] = self.scenario
        self.result['check_scopes'] = {name: {'present': True, 'status': 'not_started'}
                                      for name in ('preflight', 'voice_append', 'cold_resume',
                                                   'typed_forge', 'native_gate', 'cleanup')}
        self.result['limitations'] = [
            'OpenAI SDK loopback only; no real vendor account, audio, browser or hearing qualification.',
            'Installed-Python worker command; default uv launcher remains unqualified.',
            'Append membership is provisional and cannot authorize assistant suppression.',
        ]
        self.scope('preflight', 'in_progress')

    def installed_sources(self):
        sources = super().installed_sources()
        import importlib
        import importlib.metadata
        hook = importlib.import_module('amplifier_module_hook_context_intelligence')
        distribution = importlib.metadata.distribution('amplifier-module-hook-context-intelligence')
        path = Path(hook.__file__).resolve()
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        self.check('ci_hook_noneditable', path.is_relative_to(Path(sys.prefix))
                   and not direct.get('dir_info', {}).get('editable'))
        sources['amplifier_module_hook_context_intelligence'] = str(path.parent)
        self.result['ci_hook_source'] = str(path.parent)
        return sources

    async def run(self):
        import yaml
        from amplifier_web.voice_messages import RESPONSE_METADATA
        from amplifier_web.shared_state_probe import text_content
        self.credentials_absent()
        app, workspace = self.configure_home()
        self.sources = self.installed_sources()
        base_url = await self.server()
        # Select the exact installed implicit hook, preserving normal host
        # composition/config and immutable activation guards.
        settings = {'bundle': {'app': []}, 'sources': {'modules': {'hook-context-intelligence':
            self.sources['amplifier_module_hook_context_intelligence']}}}
        base.private_write(self.root / 'native/settings.yaml', yaml.safe_dump(settings))
        bundle = self.bundle(self.sources, 'provider-openai', {
            'api_key': 'offline-synthetic-key', 'base_url': base_url,
            'default_model': base.MODEL, 'use_streaming': False,
            'max_retries': 0, 'timeout': 10,
        }, 'spoken-provenance.yaml')
        service, runtime = self.service(app, workspace)
        sid = str(uuid.uuid4())
        self.result['session_id'] = sid
        self.stage('create')
        await service.dispatch('session.create', {'id': sid, 'workspace': str(workspace),
                                                 'bundle': str(bundle)}, include_state=False)
        await service.dispatch('session.naming', {'id': sid, 'automatic': False}, include_state=False)
        self.scope('preflight', 'completed')
        self.scope('voice_append', 'in_progress')
        self.stage('private_voice')
        receipt = await service.voice_delegate(base.WRAPPER, base.VOICE_ID, sid,
            call_id=base.CALL_ID, delegation_id=base.DELEGATION_ID)
        self.check('private_accepted', receipt.get('accepted') and bool(receipt.get('voicePresentation')))
        response = await service.wait_for_response(sid, input_id=base.VOICE_ID, timeout=60)
        self.remember_processes(runtime)
        self.finished(service, sid, base.VOICE_ID, response)
        self.check('one_real_sdk_post', len(self.posts()) == 1)
        path, rows = self.transcript(workspace, sid)
        self.canonical_input(rows, base.VOICE_ID, base.WRAPPER)
        assistants = [row for row in rows if row.get('role') == 'assistant'
                      and text_content(row) == base.ANSWER]
        self.check('generated_assistant_once', len(assistants) == 1)
        marker = assistants[0].get('metadata', {}).get(RESPONSE_METADATA)
        self.check('generated_append_provenance', isinstance(marker, dict)
            and marker.get('rootSessionId') == sid
            and marker.get('generationId') == response['generation_id']
            and marker.get('inputIds') == [base.VOICE_ID] and bool(marker.get('appendId'))
            and marker.get('bindings') == [{'commandId': base.VOICE_ID,
                'acceptedInputId': base.VOICE_ID, 'voiceCallId': base.CALL_ID}])
        self.result['generated_marker'] = marker
        await self.capacity(service, sid, 'voice', 1)
        budget = await runtime.control(sid, 'budget.set', {'contextTokens': 16384})
        self.check('context_budget_set', budget.get('contextTokens') == 16384)
        self.scope('voice_append', 'completed')
        self.scope('cold_resume', 'in_progress')
        self.stage('cold_resume_no_input')
        process = runtime.workers[sid]['process']
        await runtime.stop(sid)
        self.check('original_worker_stopped', process.returncode is not None)
        before = path.read_bytes()
        await service.close()
        service, runtime = self.service(app, workspace)
        report = await self.start(service, runtime, sid)
        self.check('cold_remount_resumed', report.get('resumed') is True)
        self.check('resume_did_not_execute', len(self.posts()) == 1)
        budget = await runtime.control(sid, 'budget.get', {})
        self.check('context_budget_restored', budget.get('contextTokens') == 16384)
        _, resumed = self.transcript(workspace, sid)
        resumed_assistants = [row for row in resumed if row.get('role') == 'assistant'
                             and text_content(row) == base.ANSWER]
        self.check('append_identity_and_full_body_survive_resume', len(resumed_assistants) == 1
            and resumed_assistants[0].get('metadata', {}).get(RESPONSE_METADATA) == marker
            and resumed_assistants[0]['content'] == assistants[0]['content'])
        self.result['before_resume_sha256'] = hashlib.sha256(before).hexdigest()
        self.result['after_resume_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.scope('cold_resume', 'completed')
        # A typed wrapper quotation has the same words but no private capability.
        self.stage('typed_forge')
        self.scope('typed_forge', 'in_progress')
        await self.send(service, runtime, sid, 'typed-wrapper-quotation', base.WRAPPER)
        self.check('typed_one_new_post', len(self.posts()) == 2)
        _, final = self.transcript(workspace, sid)
        answers = [row for row in final if row.get('role') == 'assistant' and text_content(row) == base.ANSWER]
        self.check('typed_answer_not_stamped', len(answers) == 2
                   and RESPONSE_METADATA not in answers[-1].get('metadata', {}))
        self.check('original_voice_append_retained', answers[0].get('metadata', {}).get(RESPONSE_METADATA) == marker)
        body = self.posts()[1]['body']
        self.check('private_metadata_not_sent_to_sdk', RESPONSE_METADATA not in json.dumps(body))
        def contains(value, text):
            if isinstance(value, str):
                return text in value
            if isinstance(value, dict):
                return any(contains(item, text) for item in value.values())
            if isinstance(value, list):
                return any(contains(item, text) for item in value)
            return False
        self.check('canonical_voice_body_retained_in_request', contains(body, base.WRAPPER))
        self.check('no_unexpected_endpoints', not any(row.get('unexpected') for row in self.requests))
        self.scope('typed_forge', 'completed')
        self.stage('native_eligibility')
        self.scope('native_gate', 'in_progress')
        native_bundle = self.bundle(self.sources, 'provider-openai', {
            'api_key': 'offline-synthetic-key', 'base_url': base_url,
            'default_model': 'gpt-6-astra', 'use_streaming': False,
            'max_retries': 0, 'timeout': 10,
        }, 'spoken-native-gate.yaml')
        plan = yaml.safe_load(native_bundle.read_text())
        plan['session']['orchestrator']['config'] = {'native_provider': True}
        base.private_write(native_bundle, yaml.safe_dump(plan, sort_keys=False))
        native_sid = str(uuid.uuid4())
        await service.dispatch('session.create', {'id': native_sid, 'workspace': str(workspace),
            'bundle': str(native_bundle), 'select': False}, include_state=False)
        await service.dispatch('session.naming', {'id': native_sid, 'automatic': False}, include_state=False)
        await self.start(service, runtime, native_sid)
        status = await runtime.control(native_sid, 'native.status', {})
        self.result['native_gate'] = status
        self.check('native_loopback_gate_confirmed', status.get('enabled') is True
                   and status.get('supported') is False
                   and status.get('model') == 'gpt-6-astra'
                   and status.get('reason') == 'This configured endpoint does not support the native OpenAI transport.')
        self.check('native_probe_no_generation', len(self.posts()) == 2
                   and not service._session(native_sid).get('generations'))
        self.scope('native_gate', 'blocked')
        self.result['limitations'] = [
            'Root installed context-simple and actual OpenAI SDK loopback only; no live vendor account.',
            'No hearing, audio delivery, browser, native-provider, mixed steering or alternate-context acceptance.',
            'Actual native eligibility rejects loopback; required native generated-row acceptance is BLOCKED.',
            'Append scope is provisional; final generation-membership projection is not implemented here.',
            'Direct installed-Python worker command; default uv launcher remains unqualified.',
        ]
        self.stage('verified')


async def execute(root):
    harness = Harness(root)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await asyncio.wait_for(harness.run(), 180)
        harness.result['status'] = 'passed'
    except BaseException as exc:
        harness.record_failure(exc)
        harness.result['status'] = 'failed'
    finally:
        try:
            await harness.cleanup()
        except BaseException as exc:
            harness.result['status'] = 'failed'
            harness.result['cleanup_error_type'] = type(exc).__name__
        finally:
            base.private_write(root / 'result.json', json.dumps(harness.result, indent=2) + '\n')
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
    print(json.dumps({'status': harness.result['status'], 'stage': harness.result['stage'],
                      'result': str(root / 'result.json')}))
    return 0 if harness.result['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    base.private_write(root / 'pid', str(os.getpid()) + '\n')
    return asyncio.run(execute(root))


if __name__ == '__main__':
    sys.exit(main())