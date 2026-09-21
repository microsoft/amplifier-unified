"""Opt-in live Work acceptance: real approvals, stopping and persistent compute.

Requires the reviewed local parity modules and a configured provider. All app
homes, secrets, raw transcripts and screenshots stay in a new private output
folder. The bundle overlay changes shell stdin policy ONLY for this synthetic
fixture; it never changes saved user settings or production policy.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import yaml

from acceptance import Observation, installed_revisions, private_json, setup
from acceptance_cleanup import credential_values, redact_generated_credentials
from browser_acceptance import until, wait_js


async def profile(folder, args):
    # Keep includes/namespaces intact (flattening loses Work skill resolution).
    root = args.module_root.resolve()
    sources = {
        'loop-live': root / 'worktrees/parity-loop-integration',
        'context-managed': root / 'worktrees/parity-context-checkpoints/modules/context-managed',
        'tool-transcript': root / 'worktrees/parity-context-checkpoints/modules/tool-transcript',
        'tool-bash': root / 'worktrees/parity-managed-process',
        'tool-web': root / 'worktrees/parity-truthful-web',
        'tool-exec': root / 'repos/amplifier-module-tool-exec',
    }
    for path in sources.values():
        if not path.is_dir():
            raise ValueError('A reviewed local module source is missing')
    plan = {
        'bundle': {'name': 'live-controls-acceptance', 'version': '0.1.0'},
        'includes': [{'bundle': str(args.bundle.resolve())}],
        'session': {
            'orchestrator': {'module': 'loop-live', 'source': str(sources['loop-live']),
                             'config': {'max_iterations': 16, 'programmatic_dispatch': True}},
            'context': {'module': 'context-managed', 'source': str(sources['context-managed'])},
        },
        'tools': [{'module': name, 'source': str(source), **({'config': {
            'managed_processes': True, 'managed_stdin': True,
            'safety_profile': 'unrestricted', 'require_approval': True,
        }} if name == 'tool-bash' else {})} for name, source in sources.items() if name.startswith('tool-')],
        'hooks': [{'module': 'hooks-approval', 'source': str(root / 'repos/amplifier-module-hooks-approval'),
                   'config': {'rules': [], 'default_action': 'deny',
                              'tools': {'compute': {'require_approval': True}}, 'audit': {'enabled': False}}}],
    }
    target = folder / 'profile.md'
    target.write_text('---\n' + yaml.safe_dump(plan, sort_keys=False) + '---\n')
    return target


def assistant_events(observation, sid):
    return '\n'.join(str(e.get('text', '')) for e in observation.events
                     if e['kind'] == 'assistant.message' and e.get('sessionId') == sid)


async def finished(observation, service, sid, marker):
    # Native history adoption can clear the in-memory web message projection.
    await until(lambda: marker in assistant_events(observation, sid)
                and service._session(sid)['status'] == 'idle', timeout=240)


def tool_outputs(history, name):
    rows = []
    for row in history.get('messages', []):
        if row.get('role') == 'tool' and row.get('name') == name:
            try:
                rows.append(json.loads(row['content']))
            except (ValueError, TypeError):
                pass
    return rows


def output_text(record):
    return ''.join(chunk.get('text', '') for chunk in record.get('chunks', [])) + str(record.get('evidence', {}).get('result') or '')


class ApprovalClicks:
    """Click the actual visible production buttons in this isolated fixture."""
    def __init__(self, page, service, sid, folder):
        self.page, self.service, self.sid, self.folder = page, service, sid, folder
        self.phase, self.decision, self.clicks = 'setup', 'Allow', []
        self.seen = set()
        self.interaction = asyncio.Lock()

    async def run(self):
        while True:
            pending = [row for row in self.service._session(self.sid).get('approvals', [])
                       if row.get('status', 'pending') == 'pending']
            if pending:
                row = pending[0]
                if self.decision == 'Allow' and str(row.get('prompt', '')).split(':', 1)[0] not in {'bash', 'compute'}:
                    raise RuntimeError('Unexpected tool permission in the bounded fixture')
                if len(self.clicks) >= 350:
                    raise RuntimeError('Synthetic approval bound reached')
                if row['id'] not in self.seen:
                    async with self.interaction:
                        if not any(c['phase'] == self.phase for c in self.clicks):
                            await self.page.screenshot(path=str(self.folder / (self.phase + '-approval.png')))
                        await self.page.locator('[data-approval-id="' + row['id'] + '"]').get_by_role(
                            'button', name=self.decision, exact=True).first.click(timeout=20000)
                    self.seen.add(row['id'])
                    self.clicks.append({'phase': self.phase, 'decision': self.decision,
                                        'id': row['id'], 'title': row.get('title')})
                    if len(self.clicks) == 1:
                        await self.page.screenshot(path=str(self.folder / 'approval-answered.png'))
            await asyncio.sleep(.08)


async def run(args):
    from aiohttp import web
    from playwright.async_api import async_playwright
    import amplifier_web.runtime_worker as worker
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    folder, provider = setup(args)
    bundle = await profile(folder, args)
    report = {'schema_version': 1, 'provider_instance': args.provider,
              'model': provider['config'].get('default_model'),
              'effort': provider['config'].get('reasoning_effort'),
              'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
              'installed': installed_revisions(), 'checks': {}, 'passed': False,
              'evidence': 'configured provider, real worker, production Chromium UI, isolated synthetic workspace',
              'policy_override': 'synthetic profile only: unrestricted managed stdin; every Bash/compute call uses real hooks-approval',
              'scenario': 'budget-only' if args.budget_only else 'ui-only' if args.ui_only else 'computation-only' if args.compute_only else 'all', 'image_qa': 'not tested', 'physical_audio': 'not tested',
              'spreadsheet_recalculation': 'not tested; no spreadsheet calculation engine assumed'}
    observation, browser, pump = Observation(), None, None
    app = await create_app(folder / 'app', workspace=folder / 'workspace', voice=False,
                           background_updates=False, preload_providers=False)
    service = app['service']
    service.runtime.command = [sys.executable, str(Path(worker.__file__).resolve())]
    original = service.on_runtime_event

    async def observed(kind, data):
        observation.add(kind, data)
        await original(kind, data)

    service.on_runtime_event = observed
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    clicks = None
    try:
        async with SessionClient(url, app['control_token'], 'live-controls-fixture') as client:
            result = await client.create_session({'title': 'Synthetic live controls acceptance',
                'bundle': str(bundle), 'workspace': str(folder / 'workspace')}, command_id='create')
            sid = result['state']['selectedSessionId']
            await service.runtime.start(service._session(sid), service.on_runtime_event)
            print(json.dumps({'phase': 'runtime-ready', 'provider': args.provider, 'model': report['model'], 'effort': report['effort']}), flush=True)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                report['chromium_version'] = browser.version
                context = await browser.new_context(viewport={'width': 1440, 'height': 1050})
                async def authorize(route):
                    await route.continue_(headers={**route.request.headers, 'Authorization': 'Bearer ' + app['control_token']})
                await context.route(url + '/**', authorize)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(url)
                private_json(folder / 'browser-access.json', {'url': url, 'token': app['control_token']})
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                await page.evaluate("id => window.amplifier.dispatch('session.select',{id})", sid)
                await page.get_by_role('textbox', name='Message Amplifier').fill('UNSENT-CONTROLS-DRAFT')
                clicks = ApprovalClicks(page, service, sid, folder)
                pump = asyncio.create_task(clicks.run())

                async def shared(action, arguments, identity):
                    return await client._json('POST', '/api/actions', {'action': action, 'args': arguments, 'id': identity})

                async def send(phase, text, marker, decision='Allow'):
                    clicks.phase, clicks.decision = phase, decision
                    print(json.dumps({'phase': phase}), flush=True)
                    await client.command(sid, 'conversation.send', {'text': text}, command_id=phase)
                    complete = asyncio.create_task(finished(observation, service, sid, marker))
                    done, _ = await asyncio.wait([complete, pump], return_when=asyncio.FIRST_COMPLETED)
                    if pump in done:
                        complete.cancel()
                        await pump
                    await complete
                    private_json(folder / (phase + '-history.json'), await service.runtime.control(sid, 'history.snapshot'))

                if not (args.compute_only or args.ui_only or args.budget_only):
                    await send('denial', 'Bounded synthetic acceptance. Do not delegate or create tasks/schedules. '
                        'Use tool_exec exactly once to await tools.bash({command:"printf DENIED_EFFECT > denied.txt"}) and text the returned receipt. '
                        'The user will deny it. If denied do not retry, use another tool, or write the file. '
                        'Describe the actual result and finish DENIAL_FINISHED.', 'DENIAL_FINISHED', 'Deny')
                    denied = json.loads((folder / 'denial-history.json').read_text())
                    denied_receipts = tool_outputs(denied, 'tool_exec')
                    report['checks']['browser_denied_nested_tool'] = any(c['phase'] == 'denial' and c['decision'] == 'Deny' for c in clicks.clicks)
                    report['checks']['denied_effect_absent'] = not (folder / 'workspace/denied.txt').exists()
                    report['checks']['nested_denial_receipt'] = any(any(call.get('success') is False for call in row.get('output', {}).get('calls', [])) for row in denied_receipts)
                    report['checks']['denied_not_retried'] = len(denied_receipts) == 1

                    await send('allow', 'Now use tool_exec exactly once to await tools.bash({command:"printf ALLOWED_EFFECT > allowed.txt; printf ALLOWED_OUTPUT_51"}) and text the real receipt. '
                        'The user will approve. Do not claim success before the result. Finish ALLOW_FINISHED.', 'ALLOW_FINISHED')
                    allowed = json.loads((folder / 'allow-history.json').read_text())
                    report['checks']['browser_allowed_nested_tool'] = any(c['phase'] == 'allow' and c['decision'] == 'Allow' for c in clicks.clicks)
                    report['checks']['allowed_effect_observed'] = (folder / 'workspace/allowed.txt').read_text() == 'ALLOWED_EFFECT'
                    report['checks']['nested_allowed_output_receipt'] = any('ALLOWED_OUTPUT_51' in json.dumps(row) and any(call.get('success') is True for call in row.get('output', {}).get('calls', [])) for row in tool_outputs(allowed, 'tool_exec'))

                    clicks.phase, clicks.decision = 'stop', 'Allow'
                    print(json.dumps({'phase': 'stop'}), flush=True)
                    await client.command(sid, 'conversation.send', {'text': 'Use bash action=start command="printf STOP_STARTED; sleep 25; printf LATE_EFFECT > stop-late.txt". '
                        'Use bash action=wait on the exact returned process_id until it exits. Do not start another process or retry it. Finish STOP_FINISHED only after actual completion.'}, command_id='stop')
                    def live_process():
                        return next((r for r in service.operations.journal.list(sid) if r['kind'] == 'process' and r['state'] == 'running' and 'STOP_STARTED' in json.dumps(service.operations.journal.read(sid, r['id']))), None)
                    await until(live_process, 180)
                    process = live_process()
                    await page.get_by_role('button', name='Stop response', exact=True).click()
                    await until(lambda: service._session(sid)['status'] in {'idle', 'stopped'}, 45)
                    report['checks']['browser_stop_admitted'] = service._session(sid).get('interruptionRevision', 0) > 0
                    after_stop = service.operations.journal.status(sid, process['id'])
                    report['stop_response_process_state'] = after_stop['state']
                    operations = page.get_by_role('region', name='Operations', exact=True)
                    await operations.get_by_role('button', name='Operations', exact=True).click()
                    await operations.get_by_role('button', name='Refresh operations', exact=True).click()
                    running = operations.get_by_role('button', name='Command · Running', exact=True)
                    if await running.count():
                        await running.first.click()
                        await operations.get_by_role('button', name='Stop command', exact=True).click()
                    await until(lambda: service.operations.journal.status(sid, process['id'])['state'] not in {'running', 'cancel_requested'}, 45)
                    terminal = service.operations.journal.read(sid, process['id'])
                    report['checks']['process_termination_observed'] = terminal['state'] == 'cancelled' and terminal['returncode'] is not None
                    report['stop_final_state'], report['stop_returncode'] = terminal['state'], terminal.get('returncode')
                    await operations.get_by_role('button', name='Operations', exact=True).click()

                if not args.budget_only:
                    clicks.phase = 'computation'
                    print(json.dumps({'phase': 'computation'}), flush=True)
                    panel = page.get_by_role('region', name='Computation', exact=True)
                    await panel.get_by_role('button', name='Computation', exact=True).click()
                    kernels = {}
                    for language, first, second, expected in (
                        ('python', 'base = 17\nprint("PY_FIRST", base)', 'total = base + 25\nprint("PY_PERSISTED", total)', 'PY_PERSISTED 42'),
                        ('node', 'globalThis.base = 19; console.log("NODE_FIRST", base)', 'globalThis.total = base + 23; console.log("NODE_PERSISTED", total)', 'NODE_PERSISTED 42'),
                    ):
                        async with clicks.interaction:
                            await panel.get_by_label('Computation language').select_option(language)
                            await panel.get_by_role('button', name='Create runtime', exact=True).click()
                        def kernel():
                            rows = service.operations.journal.list(sid)
                            return next((r for r in rows if r['kind'] == 'kernel' and r.get('metadata', {}).get('language') == language and r.get('metadata', {}).get('state') == 'idle'), None)
                        await until(kernel, 90)
                        kernels[language] = kernel()['metadata']
                        for index, code in enumerate((first, second)):
                            print(json.dumps({'phase': language + '-cell-' + str(index + 1)}), flush=True)
                            async with clicks.interaction:
                                await panel.get_by_role('textbox', name='Cell code').fill(code)
                                await panel.get_by_role('button', name='Run cell', exact=True).click()
                            await until(lambda: any(r['kind'] == 'kernel-cell' and r.get('metadata', {}).get('code') == code and r['state'] == 'completed' for r in service.operations.journal.list(sid)), 90)
                            from playwright.async_api import expect
                            await expect(panel.get_by_role('button', name='Run cell', exact=True)).to_be_enabled(timeout=30000)
                            await panel.screenshot(path=str(folder / (language + '-cell-' + str(index + 1) + '.png')))
                            private_json(folder / 'latest-cell.json', {'expectedCode': code, 'records': service.operations.journal.list(sid)})
                        rows = service.operations.journal.list(sid)
                        report['checks'][language + '_variables_persist'] = any(expected in output_text(service.operations.journal.read(sid, r['id'])) for r in rows if r['kind'] == 'kernel-cell')
                        await panel.screenshot(path=str(folder / (language + '-computation.png')))

                    if not args.ui_only:
                        python = kernels['python']
                        await send('agent-computation', f'Use app_control to run a new Python cell in existing kernelId={python["id"]}, generation={python["generation"]}. '
                            'Use the existing base variable; do not assign base again. Recalculate total=base+26 and write calculation.html with a complete HTML document containing <h1>Live calculation</h1><output id="result">43</output>, '
                            'but derive its displayed number from total, not a hardcoded answer. Print AGENT_PERSISTED and total. '
                            'Wait for actual cell completion with operations.read/wait. Then show calculation.html in the canvas via canvas.show kind=html path=calculation.html title=Live calculation. '
                            'Finish COMPUTATION_FINISHED only when the actual result is 43.', 'COMPUTATION_FINISHED')
                        report['checks']['agent_reused_ui_kernel'] = any('AGENT_PERSISTED 43' in output_text(service.operations.journal.read(sid, r['id'])) for r in service.operations.journal.list(sid) if r['kind'] == 'kernel-cell')
                        artifact = folder / 'workspace/calculation.html'
                        report['checks']['calculated_artifact_written'] = artifact.exists() and '>43</output>' in artifact.read_text()
                        # The initiating HTTP client owns its canvas selection. Explicitly
                        # open the saved artifact in this independent browser client.
                        opener = page.get_by_role('button', name='Open canvas', exact=True)
                        if await opener.count():
                            await opener.click()
                        library_button = page.get_by_role('button', name='Saved artifacts (1)', exact=True)
                        if not await library_button.is_visible():
                            await page.get_by_role('button', name='Canvas controls', exact=True).click()
                        await library_button.click()
                        await page.get_by_role('region', name='Saved canvas artifacts', exact=True).get_by_role('button').filter(has_text='Live calculation').click()
                        await wait_js(page, '!window.amplifier.getState().view?.canvasDraft?.library')
                        # A visible renderer in the production canvas, not hidden DOM or file existence alone.
                        async with asyncio.timeout(45):
                            while True:
                                frames = [frame for frame in page.frames if frame != page.main_frame]
                                matches = [await frame.locator('#result').count() and await (await frame.frame_element()).is_visible() and await frame.locator('#result').is_visible() and await frame.locator('#result').inner_text() == '43' for frame in frames]
                                if any(matches):
                                    break
                                await asyncio.sleep(.2)
                        report['checks']['calculated_artifact_rendered'] = True
                        await page.screenshot(path=str(folder / 'artifact-desktop.png'), full_page=True)
                    # Close through the same approved path while the click pump is alive.
                    for data in kernels.values():
                        await shared('kernels.close', {'sessionId': sid, 'kernelId': data['id'], 'generation': data['generation']}, 'close-' + data['language'])

                if args.budget_only:
                    await send('budget-baseline', 'Reply CAPACITY_READY without tools or delegation.', 'CAPACITY_READY')
                if not args.ui_only:
                    usage = (await shared('capacity.read', {'sessionId': sid}, 'capacity-read'))['result']
                    private_json(folder / 'capacity.json', usage)
                    report['usage'] = {key: usage.get(key) for key in ('budget', 'usage', 'admission')}
                    report['checks']['actual_usage_recorded'] = usage['usage']['calls'] > 0 and usage['usage']['metrics']['totalTokens']['value'] > 0
                    report['checks']['admission_receipts_bound'] = all(r.get('admittedAt') is not None and r.get('budgetRevision') is not None for r in usage['usage']['receipts'])
                    revision = usage['budget']['revision']
                    await shared('capacity.set', {'sessionId': sid, 'expectedRevision': revision, 'enabled': True, 'maxTotalTokens': 1}, 'limit')
                    await client.command(sid, 'conversation.send', {'text': 'Reply BUDGET_SHOULD_NOT_RUN.'}, command_id='budget-probe')
                    await until(lambda: service._session(sid)['status'] in {'idle', 'stopped', 'error', 'interrupted'} and any(e['kind'] == 'runtime.error' for e in observation.events), 60)
                    after = (await shared('capacity.read', {'sessionId': sid}, 'capacity-after'))['result']
                    report['checks']['budget_blocks_future_model_call'] = after['usage']['calls'] == usage['usage']['calls'] and not after['admission']['allowed']
                if not (args.compute_only or args.ui_only or args.budget_only):
                    report['checks']['no_late_stop_effect'] = not (folder / 'workspace/stop-late.txt').exists()
                    report['checks']['denied_effect_still_absent'] = not (folder / 'workspace/denied.txt').exists()
                report['checks']['draft_preserved'] = await page.get_by_role('textbox', name='Message Amplifier').input_value() == 'UNSENT-CONTROLS-DRAFT'
                report['checks']['selected_chat_preserved'] = await page.evaluate('id => window.amplifier.getState().selectedSessionId === id', sid)
                report['checks']['no_browser_errors'] = not errors
                operation_ids = {row['id'] for row in service.operations.journal.list(sid)}
                await page.reload()
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                report['checks']['reconnect_no_replay'] = operation_ids == {row['id'] for row in service.operations.journal.list(sid)} and not (folder / 'workspace/denied.txt').exists() and not (folder / 'workspace/stop-late.txt').exists()
                report['passed'] = all(report['checks'].values())
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        (folder / 'error.txt').write_text(str(exc))
        import traceback
        (folder / 'traceback.txt').write_text(traceback.format_exc())
        if browser:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(folder / 'failure.png'), full_page=True)
                (folder / 'failure-dom.txt').write_text(await page.locator('body').inner_text())
    finally:
        if clicks:
            private_json(folder / 'pending-approvals.json', service._session(clicks.sid).get('approvals', []))
            if pump and pump.done() and not pump.cancelled() and pump.exception():
                (folder / 'approval-pump-error.txt').write_text(str(pump.exception()))
            report['approval_counts'] = {phase: sum(c['phase'] == phase for c in clicks.clicks) for phase in sorted({c['phase'] for c in clicks.clicks})}
            private_json(folder / 'approval-clicks.json', clicks.clicks)
        for sid, row in service.runtime.workers.items():
            private_json(folder / ('worker-' + sid + '.json'), row.get('stderr', []))
            with contextlib.suppress(Exception):
                private_json(folder / ('history-' + sid + '.json'), await service.runtime.control(sid, 'history.snapshot'))
        if pump:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
        if browser:
            await browser.close()
        await runner.cleanup()
        private_json(folder / 'events.json', observation.events)
        private_json(folder / 'report.json', report)
        (folder / 'browser-access.json').unlink(missing_ok=True)
        report['credential_cleanup'] = redact_generated_credentials(folder, credential_values(provider))
        private_json(folder / 'report.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'error_type': report.get('error_type'), 'report': str(folder / 'report.json')}), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--compute-only', action='store_true', help='Narrow diagnostic; does not validate denial or Stop')
    modes.add_argument('--ui-only', action='store_true', help='Python/Node UI cells only; no provider calls')
    modes.add_argument('--budget-only', action='store_true', help='One real provider baseline, then budget denial; no shell or code cells')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--settings', type=Path, default=Path.home() / '.amplifier/settings.yaml')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--module-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required for paid provider calls and synthetic UI approval clicks')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
