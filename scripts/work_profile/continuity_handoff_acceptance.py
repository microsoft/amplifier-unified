"""Opt-in real-provider task continuity, repeated compaction and Git handoff.

Runs only in a new private directory with synthetic history and a synthetic Git
repository. The existing provider/model/effort is preserved. No production host,
real workspace or saved user conversation is mounted. Full diagnostics remain
private; the public report contains bounded checks and source receipts only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import yaml

from acceptance import Observation, installed_revisions, private_json, setup


def git(root, *arguments):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *arguments],
                            cwd=root, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Synthetic Git operation failed: ' + arguments[0])
    return result.stdout.strip()


async def profile(folder, args):
    from amplifier_foundation import load_bundle
    bundle = await load_bundle(str(args.bundle), strict=True)
    mounted = bundle.to_mount_plan()
    root = args.module_root.resolve()
    sources = {
        'loop-live': root / 'worktrees/parity-loop-integration',
        'context-managed': root / 'worktrees/parity-context-checkpoints/modules/context-managed',
        'tool-transcript': root / 'worktrees/parity-context-checkpoints/modules/tool-transcript',
        'tool-bash': root / 'worktrees/parity-managed-process',
        'tool-web': root / 'worktrees/parity-truthful-web',
        'tool-exec': root / 'repos/amplifier-module-tool-exec',
    }
    if args.context_source:
        sources['context-managed'] = args.context_source.resolve()
    # Keep includes and namespace/resource authority. No replacement Markdown
    # instructions and no flattening of the Work graph.
    plan = {'bundle': {'name': 'continuity-acceptance', 'version': '0.1.0'},
            'includes': [{'bundle': str(args.bundle.resolve())}], 'tools': [], 'session': {}}
    for row in mounted.get('tools', []):
        if row.get('module') in sources:
            plan['tools'].append({**row, 'source': str(sources[row['module']])})
    for name, row in mounted.get('session', {}).items():
        if isinstance(row, dict) and row.get('module') in sources:
            plan['session'][name] = {**row, 'source': str(sources[row['module']])}
    plan['session']['orchestrator'].setdefault('config', {}).update(max_iterations=12)
    plan['session']['context'].setdefault('config', {}).update(
        max_tokens=24000, summarize_trigger=.12, summary_target_tokens=1200,
        summary_timeout=120, durable_checkpoints=True)
    path = folder / 'profile.md'
    path.write_text('---\n' + yaml.safe_dump(plan, sort_keys=False) + '---\n')
    composed = await load_bundle(str(path), strict=True)
    namespaces = sorted((getattr(composed, 'source_base_paths', {}) or {}).keys())
    return path, {name: git(path, 'rev-parse', 'HEAD') for name, path in sources.items()}, namespaces


class Host:
    def __init__(self, folder, observation):
        self.folder, self.observation = folder, observation
        self.runner = None

    async def start(self):
        from aiohttp import web
        import amplifier_web.runtime_worker as worker
        from amplifier_web.server import create_app
        self.app = await create_app(self.folder / 'app', workspace=self.folder / 'workspace',
                                    voice=False, background_updates=False, preload_providers=False)
        self.service = self.app['service']
        self.service.runtime.command = [sys.executable, str(Path(worker.__file__).resolve())]
        original = self.service.on_runtime_event
        async def observed(kind, data):
            self.observation.add(kind, data)
            await original(kind, data)
        self.service.on_runtime_event = observed
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        return self

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None


async def action(client, name, arguments, identity):
    return (await client._json('POST', '/api/actions',
        {'action': name, 'args': arguments, 'id': identity}))['result']


def public_text(observation, sid, start=0):
    return '\n'.join(event.get('text', '') for event in observation.events[start:]
        if event['kind'] == 'assistant.message' and event.get('sessionId') == sid)


async def turn(client, host, sid, prompt, marker, observation, identity, *, capture_compaction=None):
    start = len(observation.events)
    await client.command(sid, 'conversation.send', {'text': prompt}, command_id=identity)
    async def capture():
        await observation.wait(lambda: any(event.get('phase') == 'compacting'
            for event in observation.events[start:]), 180)
        state = await action(client, 'task.get', {'sessionId': sid}, identity + '-compacting-state')
        capture_compaction.update(state.get('continuity', {}))
    monitor = asyncio.create_task(capture()) if capture_compaction is not None else None
    try:
        await observation.wait(lambda: marker in public_text(observation, sid, start)
                               and host.service._session(sid)['status'] == 'idle', 240)
        if monitor and monitor.done():
            await monitor
    finally:
        if monitor and not monitor.done():
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
    return public_text(observation, sid, start)


def checkpoint(folder):
    paths = list((folder / 'app/sessions').glob('*/context-checkpoint.json'))
    if len(paths) != 1:
        raise RuntimeError('Expected exactly one real context checkpoint')
    return paths[0], json.loads(paths[0].read_text())


def history_files(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('transcript.jsonl')
            if '/cache/' not in str(p)}


async def run(args):
    from amplifier_web.session_client import SessionClient
    folder, provider = setup(args)
    workspace = folder / 'workspace'
    git(workspace, 'init', '-b', 'main')
    (workspace / 'LOCATION.txt').write_text('ORIGINAL-SOURCE\n')
    git(workspace, 'add', 'LOCATION.txt')
    git(workspace, '-c', 'user.name=Synthetic Acceptance', '-c', 'user.email=acceptance@example.invalid',
        'commit', '-m', 'Synthetic source baseline')
    bundle, sources, namespaces = await profile(folder, args)
    observation = Observation()
    host = Host(folder, observation)
    report = {'schema_version': 1, 'provider_instance': args.provider,
        'model': provider['config'].get('default_model'), 'effort': provider['config'].get('reasoning_effort'),
        'evidence': 'real configured provider, isolated Unified HTTP and worker, actual context summaries and Git handoff',
        'visual_browser': 'not tested by this harness', 'installed': installed_revisions(),
        'module_commits': sources, 'host_commit': git(Path(__file__).resolve().parents[2], 'rev-parse', 'HEAD'),
        'checks': {}, 'passed': False}
    import amplifier_web.automatic_history as history_module
    original_read = history_module.read_transcript
    history_errors = []
    def observed_read(*arguments, **keywords):
        try:
            return original_read(*arguments, **keywords)
        except Exception as exc:
            history_errors.append({'type': type(exc).__name__, 'message': str(exc)})
            raise
    history_module.read_transcript = observed_read
    sid = None
    try:
        await host.start()
        async with SessionClient(host.url, host.app['control_token'], 'continuity-live') as client:
            await client.create_session({'title': 'Synthetic continuity seed', 'bundle': str(bundle),
                'workspace': str(workspace)}, command_id='empty-seed')
            imported = [{'role': 'user', 'content': 'Our original objective is the ORCHID delivery report. Preserve original files. Initial delivery color BLUE.'}]
            for index in range(10):
                imported += [{'role': 'assistant', 'content': f'Synthetic archived evidence stage {index}. '
                    + ('Four sealed crates were checked against the manifest. No delivery has been executed. ' * 150)},
                    {'role': 'user', 'content': ('Correction: the current delivery color is ORANGE, superseding BLUE. '
                        'The independently recorded manifest reference is COPPER-4197.' if index == 2
                        else f'Retain the current delivery evidence; archived stage {index}.')}]
            # This older assistant block remains in the recent unsummarized
            # suffix after the first boundary, then becomes eligible at the
            # second live input. Both compactions are real provider calls.
            imported.append({'role': 'assistant', 'content': 'Final synthetic archived evidence. '
                + ('Four sealed crates were checked against the manifest. No delivery has been executed. ' * 150)})
            before = {row['id'] for row in host.service.state['sessions']}
            await client._json('POST', '/api/actions', {'action': 'history.importFile', 'id': 'history-seed',
                'args': {'format': 'json', 'content': json.dumps(imported),
                         'title': 'Synthetic repeated continuity', 'bundle': str(bundle)}})
            rows = [row for row in host.service.state['sessions'] if row['id'] not in before]
            if len(rows) != 1:
                raise RuntimeError('Expected one independently imported synthetic conversation')
            sid = rows[0]['id']
            await client._json('POST', '/api/actions', {'action': 'session.select', 'id': 'select', 'args': {'id': sid}})
            await client._json('POST', '/api/actions', {'action': 'view.update', 'id': 'draft',
                'args': {'sessionId': sid, 'patch': {'draft': 'UNSENT-CONTINUITY-DRAFT'}}})
            created = await action(client, 'task.create', {'sessionId': sid, 'expectedRevision': 0,
                'objective': 'Prepare the ORCHID delivery report while preserving originals.',
                'constraints': ['Do not send or execute a delivery.', 'Do not mark the saved task complete in this acceptance.'],
                'maxTurns': 1}, 'task-create')
            corrected = await action(client, 'task.update', {'sessionId': sid, 'expectedRevision': created['task']['revision'],
                'correction': 'The current delivery color is ORANGE, superseding BLUE.'}, 'task-correction')
            expected_task = corrected['task']
            report['checks']['goal_bound_before_input'] = corrected['goal']['condition'] == expected_task['objective']
            resources = await host.service.runtime.control(sid, 'configuration.exportResources')
            private_json(folder / 'resource-export.json', resources)
            report['bundle_namespaces'] = namespaces
            report['checks']['work_namespace_retained'] = 'work' in namespaces
            print(json.dumps({'phase': 'continuity-runtime-ready'}), flush=True)
            for phase in (1, 2):
                marker = f'CONTINUITY_PHASE_{phase}'
                text = await turn(client, host, sid,
                    'This is one bounded synthetic continuity check. State the original report name, latest delivery color, '
                    'and the independently recorded manifest reference from the earlier conversation. '
                    'No delivery, delegation, schedule, task mutation, or file writes. Keep the saved objective open. '
                    'Answer briefly and finish with ' + marker + '.', marker, observation, f'phase-{phase}')
                report['checks'][f'phase_{phase}_facts'] = all(value in text for value in ('ORCHID', 'ORANGE', 'COPPER-4197'))
                _, saved = checkpoint(folder)
                private_json(folder / f'checkpoint-phase-{phase}.json', saved)
                report[f'checkpoint_{phase}'] = {'sha256': saved['sha256'], 'throughMessage': saved['summary']['throughMessage'],
                    'identity': saved['identity'], 'summary_preserves_correction': 'ORANGE' in saved['summary']['text'],
                    'summary_preserves_reference': 'COPPER-4197' in saved['summary']['text']}
                print(json.dumps({'phase': f'continuity-phase-{phase}-finished'}), flush=True)
            report['checks']['two_semantic_compactions'] = sum(event.get('detail') == 'Conversation context prepared; continuing work.'
                for event in observation.events) >= 2
            report['checks']['summary_boundary_advanced'] = report['checkpoint_2']['throughMessage'] > report['checkpoint_1']['throughMessage']
            report['checks']['two_summaries_preserve_facts'] = all(report[f'checkpoint_{n}']['summary_preserves_correction']
                and report[f'checkpoint_{n}']['summary_preserves_reference'] for n in (1, 2))
            task_before = await action(client, 'task.get', {'sessionId': sid}, 'before-restart')
            report['checks']['task_stays_active'] = task_before['task']['status'] == 'active'
            native_before = history_files(folder)
            report['checks']['canonical_history_exists'] = bool(native_before)
            original_workspace = host.service._session(sid)['workspace']
            original_native = {key: host.service._session(sid).get(key)
                               for key in ('runtimeSessionId', 'nativeIdentity', 'nativeProject')}
            worker_pid = host.service.runtime.workers[sid]['process'].pid
        await host.close()
        await host.start()
        async with SessionClient(host.url, host.app['control_token'], 'continuity-live') as client:
            task_after = await action(client, 'task.get', {'sessionId': sid}, 'after-restart')
            report['checks']['task_restored_after_host_restart'] = all(task_after['task'][key] == task_before['task'][key]
                for key in ('id', 'objective', 'constraints', 'corrections', 'revision', 'status'))
            report['checks']['goal_restored_after_host_restart'] = task_after['goal']['condition'] == expected_task['objective']
            report['checks']['worker_replaced_on_restart'] = host.service.runtime.workers[sid]['process'].pid != worker_pid
            restored_during_compaction = {}
            text = await turn(client, host, sid,
                'Continue this saved synthetic task after restart. Briefly give the original report name, latest color and '
                'earlier manifest reference. No task mutation, delegation, delivery, schedule or writes. Finish with CONTINUITY_RESTART.',
                'CONTINUITY_RESTART', observation, 'after-restart-input', capture_compaction=restored_during_compaction)
            report['checks']['model_facts_after_restart'] = all(value in text for value in ('ORCHID', 'ORANGE', 'COPPER-4197'))
            after_request = await action(client, 'task.get', {'sessionId': sid}, 'after-restored-request')
            report['restored_continuity'] = after_request['continuity']
            report['continuity_during_first_restart_compaction'] = restored_during_compaction
            report['checks']['actual_checkpoint_restore_observed'] = (restored_during_compaction.get('status') == 'restored'
                or after_request['continuity'].get('status') == 'restored')
            report['checks']['checkpoint_supported_after_restart'] = after_request['continuity'].get('supported') is True
            report['checks']['checkpoint_not_rejected_after_restart'] = after_request['continuity'].get('status') in ('restored', 'ready', 'saved')
            inspected = await action(client, 'worktree.inspect', {'sessionId': sid}, 'inspect-source')
            checkout = await action(client, 'worktree.create', {'sessionId': sid,
                'sourceRevision': inspected['repository']['sourceRevision']}, 'create-checkout')
            target = Path(checkout['path'])
            target_evidence = 'TARGET-' + uuid.uuid4().hex[:12]
            (target / 'LOCATION.txt').write_text(target_evidence + '\n')
            receipt = await action(client, 'worktree.handoff', {'sessionId': sid, 'id': checkout['id'],
                'expectedExecutionRevision': 0}, 'handoff')
            async with asyncio.timeout(90):
                await asyncio.gather(*list(host.service.worktrees.jobs))
            receipt = host.service.worktrees.read(sid, receipt['id'])
            report['checks']['handoff_applied'] = receipt['phase'] == 'applied'
            report['checks']['handoff_no_input_replay'] = receipt.get('release', {}).get('inputsReplayed') is False
            current = host.service._session(sid)
            report['checks']['canonical_home_preserved'] = current['workspace'] == original_workspace
            report['checks']['native_identity_preserved'] = all(current.get(key) == value for key, value in original_native.items())
            report['checks']['execution_directory_changed'] = Path(current['workingDirectory']).resolve() == target.resolve()
            print(json.dumps({'phase': 'handoff-applied'}), flush=True)
            text = await turn(client, host, sid,
                'Perform one bounded acceptance read in the current execution folder. Use bash to run pwd and cat LOCATION.txt '
                'without an explicit cwd override. Report the actual location token and the original report name, latest color '
                'and earlier manifest reference. Do not change files or task state. Finish with CONTINUITY_HANDOFF.',
                'CONTINUITY_HANDOFF', observation, 'handoff-read')
            native = await host.service.runtime.control(sid, 'history.snapshot')
            private_json(folder / 'history.json', native)
            bash_results = [row for row in native['messages'] if row.get('role') == 'tool' and row.get('name') == 'bash']
            bash_evidence = '\n'.join(str(row.get('content')) for row in bash_results)
            report['checks']['actual_bash_target_evidence'] = target_evidence in bash_evidence and str(target.resolve()) in bash_evidence
            report['checks']['model_target_evidence'] = target_evidence in text
            report['checks']['model_facts_after_handoff'] = all(value in text for value in ('ORCHID', 'ORANGE', 'COPPER-4197'))
            report['checks']['original_source_file_preserved'] = (workspace / 'LOCATION.txt').read_text() == 'ORIGINAL-SOURCE\n'
            native_after = history_files(folder)
            report['checks']['canonical_history_preserved'] = all(path in native_after and native_after[path].startswith(data)
                for path, data in native_before.items())
            report['checks']['no_destination_history_copy'] = not list(target.rglob('transcript.jsonl'))
            final_task = await action(client, 'task.get', {'sessionId': sid}, 'final-task')
            report['checks']['task_and_correction_after_handoff'] = all(final_task['task'][key] == expected_task[key]
                for key in ('id', 'objective', 'constraints', 'corrections', 'revision', 'status'))
            state = await client._json('GET', '/api/state')
            report['checks']['selection_preserved'] = state['selectedSessionId'] == sid
            report['checks']['draft_preserved'] = state['view']['draft'] == 'UNSENT-CONTINUITY-DRAFT'
            report['usage'] = await host.service.runtime.control(sid, 'usage.inspect')
            report['passed'] = all(report['checks'].values())
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        (folder / 'error.txt').write_text(str(exc))
        if hasattr(host, 'service'):
            for identity, row in host.service.runtime.workers.items():
                private_json(folder / (identity + '-diagnostics.json'), row.get('stderr', []))
    finally:
        await host.close()
        history_module.read_transcript = original_read
        private_json(folder / 'history-read-errors.json', history_errors)
        report['history_read_error_count'] = len(history_errors)
        report['elapsed_seconds'] = round(time.monotonic() - observation.start, 3)
        report['semantic_compactions'] = sum(event.get('detail') == 'Conversation context prepared; continuing work.'
            for event in observation.events)
        private_json(folder / 'report.json', report)
        private_json(folder / 'events.json', observation.events)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'],
        'error_type': report.get('error_type'), 'report': str(folder / 'report.json')}), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--settings', type=Path, default=Path.home() / '.amplifier/settings.yaml')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--module-root', type=Path, required=True)
    parser.add_argument('--context-source', type=Path,
                        help='Reviewed local context module override for isolated pre-merge acceptance')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required for paid provider calls')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
