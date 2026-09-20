"""Opt-in one-shot scheduling acceptance with one bounded configured-provider turn.

All settings/history stay in a fresh private fixture. Preparation mode submits
no work. The live run uses the production scheduler and its real wall clock;
restart never retries an input or rewrites a run receipt.
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
from datetime import datetime, timezone

import yaml

from acceptance import Observation, installed_revisions, private_json, setup
from acceptance_cleanup import credential_values, redact_generated_credentials
from browser_acceptance import until, wait_js
from continuity_handoff_acceptance import action
from live_controls_acceptance import assistant_events, profile


class Host:
    def __init__(self, folder, observation):
        self.folder, self.observation = folder, observation
        self.port, self.runner = 0, None

    async def start(self):
        from aiohttp import web
        from amplifier_web.server import create_app
        import amplifier_web.runtime_worker as worker
        self.app = await create_app(self.folder/'app', workspace=self.folder/'workspace',
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
        site = web.TCPSite(self.runner, '127.0.0.1', self.port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.app['allowed_origins'] = self.app['allowed_origins'] | {self.url}
        return self

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None


def source_revision(path):
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def transcript_hashes(folder):
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (folder/'shared').rglob('transcript.jsonl') if 'cache' not in path.parts}


def admission_ids(usage):
    rows = usage['usage']['receipts']
    if len(rows) != usage['usage']['calls'] or not rows or any(not row.get('admittedAt') for row in rows):
        return None
    identities = [row.get('id') for row in rows]
    return sorted(identities) if all(identities) and len(set(identities)) == len(identities) else None


def bounded_calls(usage):
    return 1 <= usage['usage']['calls'] <= 2 and admission_ids(usage) is not None


def same_admissions(before, after):
    identities = admission_ids(before)
    return identities is not None and identities == admission_ids(after)


async def run(args):
    from amplifier_web.session_client import SessionClient
    from amplifier_foundation import load_bundle
    from playwright.async_api import async_playwright

    folder, provider = setup(args)
    observation = Observation()
    host, browser = Host(folder, observation), None
    report = {'schema_version': 1, 'scenario': 'prepare-only' if args.prepare_only else 'one-shot-live',
        'provider': args.provider, 'model': provider['config'].get('default_model'),
        'effort': provider['config'].get('reasoning_effort'), 'source': source_revision(Path(__file__).resolve().parents[2]),
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'installed': installed_revisions(), 'checks': {}, 'passed': False}
    try:
        bundle = await profile(folder, args)
        plan = yaml.safe_load(bundle.read_text().split('---')[1])
        plan['session']['orchestrator']['config']['max_iterations'] = 1
        bundle.write_text('---\n' + yaml.safe_dump(plan, sort_keys=False) + '---\n')
        mounted = await load_bundle(str(bundle), strict=True)
        report['modules'] = {row['module']: source_revision(Path(row['source']))
            for row in [*plan['session'].values(), *plan['tools']]}
        report['checks']['included_work_instructions'] = bool(mounted.instruction)
        report['checks']['single_iteration_bound'] = mounted.to_mount_plan()['session']['orchestrator']['config']['max_iterations'] == 1
        await host.start()
        async with SessionClient(host.url, host.app['control_token'], 'schedule-fixture') as client:
            target = await client.create_session({'title': 'Synthetic scheduled target', 'bundle': str(bundle),
                'workspace': str(folder/'workspace')}, command_id='target')
            sid = target['state']['selectedSessionId']
            task = await action(client, 'task.create', {'sessionId': sid, 'expectedRevision': 0,
                'objective': 'Preserve this synthetic task while one explicitly scheduled response is observed.',
                'constraints': ['Do not call tools, delegate, change the task or create schedules. Respond once to the supplied scheduled prompt.'],
                'maxTurns': 1}, 'task')
            report['checks']['task_goal_bound'] = task['goal']['task_id'] == task['task']['id'] and task['goal']['cap'] == 1
            other = await client.create_session({'title': 'Selected task remains here', 'bundle': str(bundle),
                'workspace': str(folder/'workspace')}, command_id='selected')
            selected = other['state']['selectedSessionId']
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                report['chromium'] = browser.version
                context = await browser.new_context(viewport={'width': 1440, 'height': 1050})
                async def authorize(route):
                    await route.continue_(headers={**route.request.headers, 'Authorization': 'Bearer ' + host.app['control_token']})
                await context.route(host.url + '/**', authorize)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(host.url)
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                await page.evaluate("id => window.amplifier.dispatch('session.select', {id})", selected)
                composer = page.get_by_role('textbox', name='Message Amplifier')
                await composer.fill('UNSENT-SCHEDULE-DRAFT')
                await wait_js(page, "window.amplifier.getState().view.draft === 'UNSENT-SCHEDULE-DRAFT'")
                marker = 'SCHEDULE_OBSERVED_' + os.urandom(6).hex().upper()
                due = round(time.time() + (120 if args.prepare_only else 8), 6)
                fields = {'sessionId': sid, 'prompt': 'This is the one explicitly authorized scheduled response. '
                    'Do not call any tools, delegate, change the saved task, or schedule anything. '
                    'Return exactly this single line: ' + marker,
                    'spec': {'kind': 'once', 'timezone': 'America/Los_Angeles',
                             'startAt': datetime.fromtimestamp(due, timezone.utc).isoformat()},
                    'kind': 'task', 'missedRunPolicy': 'skip', 'notificationPolicy': 'failures_only'}
                preview = await action(client, 'schedule.preview', fields, 'preview')
                report['checks']['concrete_future_preview'] = len(preview['occurrences']) == 1 and preview['occurrences'][0]['dueAt'] == due
                report['checks']['preview_binds_original_task'] = preview['binding']['taskId'] == task['task']['id']
                if args.prepare_only:
                    report['checks']['no_model_calls'] = (await action(client, 'capacity.read', {'sessionId':sid}, 'prepare-usage'))['usage']['calls'] == 0
                    report['checks']['no_schedule_activated'] = not (await action(client, 'schedule.list', {'sessionId':sid}, 'prepare-list'))['items']
                    report['passed'] = all(report['checks'].values())
                    await browser.close()
                    browser = None
                    return report
                created = await action(client, 'schedule.create', {**fields, 'expectedRevision':0,
                    'previewHash':preview['previewHash']}, 'explicit-one-shot')
                schedule = created['schedule']
                print(json.dumps({'phase':'scheduled', 'seconds_until_due':round(due-time.time(),2)}), flush=True)
                # Production background loop owns due admission; never call tick or submit here.
                await until(lambda: marker in assistant_events(observation, sid)
                    and host.service._session(sid)['status'] == 'idle', timeout=180)
                saved = await action(client, 'schedule.read', {'sessionId':sid,'id':schedule['id']}, 'finished')
                usage = await action(client, 'capacity.read', {'sessionId':sid}, 'usage')
                state = await page.evaluate('window.amplifier.getState()')
                run = saved['runs'][0]
                checks = report['checks']
                checks['exact_scheduled_response'] = assistant_events(observation, sid).strip() == marker
                checks['one_due_run'] = len(saved['runs']) == 1 and run['phase'] == 'completed'
                checks['original_task_identity'] = run['taskId'] == task['task']['id'] and run['sessionId'] == sid
                checks['one_stable_input_identity'] = run['id'] == run['inputId'] and run['inputId'].startswith('schedule:')
                checks['at_most_two_real_model_calls'] = bounded_calls(usage)
                checks['schedule_completed_without_next_due'] = saved['schedule']['status'] == 'completed' and saved['schedule']['nextDue'] is None
                checks['selection_preserved'] = state['selectedSessionId'] == selected
                checks['draft_preserved'] = await composer.input_value() == 'UNSENT-SCHEDULE-DRAFT'
                final_task = await action(client,'task.get',{'sessionId':sid},'task-after')
                checks['task_not_auto_completed'] = final_task['task']['status'] == 'active' and final_task['task']['revision'] == task['task']['revision']
                report['provider_calls'] = usage['usage']['calls']
                report['run_phase'] = run['phase']
                report['source_authorization'] = saved['schedule']['authorization']['origin']
                private_json(folder/'schedule-before-restart.json',saved)
                private_json(folder/'capacity-before-restart.json',usage)
                await page.screenshot(path=str(folder/'scheduled-with-draft.png'))
                hashes = transcript_hashes(folder)
                report['canonical_transcript_count'] = len(hashes)
                print(json.dumps({'phase':'restarting', 'provider_calls':report['provider_calls']}),flush=True)
                await host.close()
                before_events = len(observation.events)
                old_owner = host.service.schedules.store.owner
                await host.start()
                await page.reload()
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                # Observe the actual lease change after restart, without advancing time.
                await until(lambda: host.service.schedules.store.owns(time.time()), timeout=45)
                await asyncio.sleep(4.2)  # two further production poll intervals
                async with SessionClient(host.url,host.app['control_token'],'schedule-fixture') as reopened:
                    after = await action(reopened,'schedule.read',{'sessionId':sid,'id':schedule['id']},'restart-read')
                    after_usage = await action(reopened,'capacity.read',{'sessionId':sid},'restart-usage')
                    after_task = await action(reopened,'task.get',{'sessionId':sid},'restart-task')
                after_state = await page.evaluate('window.amplifier.getState()')
                checks['fresh_scheduler_owns_real_lease'] = host.service.schedules.store.owner != old_owner
                checks['restart_keeps_same_completed_run'] = after['runs'] == saved['runs']
                checks['restart_has_no_new_provider_call'] = same_admissions(usage, after_usage)
                checks['restart_has_no_new_generation'] = not any(e['kind']=='runtime.generation' and e.get('event')=='generation.started' for e in observation.events[before_events:])
                checks['restart_retains_task'] = after_task['task']['id'] == task['task']['id'] and after_task['task']['status']=='active'
                checks['restart_retains_browser_selection'] = after_state['selectedSessionId']==selected
                checks['restart_retains_browser_draft'] = await composer.input_value()=='UNSENT-SCHEDULE-DRAFT'
                checks['canonical_transcript_unchanged'] = bool(hashes) and transcript_hashes(folder)==hashes
                checks['no_browser_errors'] = not errors
                private_json(folder/'schedule-after-restart.json',after)
                private_json(folder/'capacity-after-restart.json',after_usage)
                await browser.close()
                browser=None
                report['passed']=all(checks.values())
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:1500]
    finally:
        if browser: await browser.close()
        await host.close()
        private_json(folder/'events.json',observation.events)
        private_json(folder/'report.json',report)
        report['credential_cleanup']=redact_generated_credentials(folder,credential_values(provider))
        private_json(folder/'report.json',report)
        print(json.dumps(report),flush=True)
    return report


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--provider',required=True)
    parser.add_argument('--settings',type=Path,default=Path.home()/'.amplifier/settings.yaml')
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--module-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.allow_live and not args.prepare_only: parser.error('--allow-live is required to activate the synthetic schedule')
    result=asyncio.run(run(args))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
