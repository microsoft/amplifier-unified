"""Opt-in live-provider acceptance for the composed parity modules and real UI.

Only new synthetic conversations are used. Credentials, raw history and provider
diagnostics stay in a new private directory outside the repository.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

import yaml

from acceptance import Observation, assistant_text, finished, installed_revisions, private_json, setup
from browser_acceptance import wait_js


async def profile(folder, args):
    from amplifier_foundation import load_bundle
    bundle = await load_bundle(str(args.bundle), strict=True)
    mounted = bundle.to_mount_plan()
    root = args.module_root.resolve() if getattr(args, "module_root", None) else None
    sources = {
        'loop-live': root / 'worktrees/parity-loop-integration',
        'context-managed': root / 'worktrees/parity-context-checkpoints/modules/context-managed',
        'tool-transcript': root / 'worktrees/parity-context-checkpoints/modules/tool-transcript',
        'tool-bash': root / 'worktrees/parity-managed-process',
        'tool-web': root / 'worktrees/parity-truthful-web',
        'tool-exec': root / 'repos/amplifier-module-tool-exec',
    } if root else {}
    for source in getattr(args, 'module_source', []) or []:
        name, separator, path = source.partition('=')
        if not separator or name not in {'loop-live', 'context-managed', 'tool-transcript', 'tool-bash', 'tool-web', 'tool-exec'}:
            raise ValueError('Use --module-source MODULE=/absolute/reviewed/source for an execution module')
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError('The reviewed module source does not exist')
        sources[name] = target
    # Keep the real include graph and source namespaces. Flattening mount plans
    # loses on-demand skill/resource bases and is not full bundle acceptance.
    plan = {'bundle': {'name': 'parity-acceptance', 'version': '0.1.0'},
            'includes': [{'bundle': str(args.bundle.resolve())}], 'tools': [], 'session': {}}
    for row in mounted.get('tools', []):
        if row.get('module') in sources:
            plan['tools'].append({**row, 'source': str(sources[row['module']])})
    for name, row in mounted.get('session', {}).items():
        if isinstance(row, dict) and row.get('module') in sources:
            plan['session'][name] = {**row, 'source': str(sources[row['module']])}
    plan['session'].setdefault('orchestrator', {}).setdefault('config', {})['max_iterations'] = 16
    path = folder / 'profile.md'
    path.write_text('---\n' + yaml.safe_dump(plan, sort_keys=False) + '---\n')
    return path


async def run(args):
    from aiohttp import web
    from playwright.async_api import async_playwright
    import amplifier_web.runtime_worker as worker
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    folder, provider = setup(args)
    bundle = await profile(folder, args)
    report = {'schema_version': 1, 'provider': args.provider,
              'model': provider['config'].get('default_model'), 'installed': installed_revisions(),
              'evidence': 'real provider, composed local module overrides, Unified worker and Chromium',
              'checks': {}, 'passed': False, 'audio': 'not tested'}
    app = await create_app(folder / 'app', workspace=folder / 'workspace', voice=False,
                           background_updates=False, preload_providers=False)
    service = app['service']
    service.runtime.command = [sys.executable, str(Path(worker.__file__).resolve())]
    observation, browser = Observation(), None
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
    try:
        async with SessionClient(url, app['control_token'], 'parity-fixture') as client:
            result = await client.create_session({'title': 'Synthetic parity acceptance',
                'bundle': str(bundle), 'workspace': str(folder / 'workspace')}, command_id='create')
            sid = result['state']['selectedSessionId']
            report['session_id'] = sid
            await service.runtime.start(service._session(sid), service.on_runtime_event)
            print(json.dumps({'phase': 'runtime-ready'}), flush=True)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport={'width': 1280, 'height': 950})

                async def authorize(route):
                    await route.continue_(headers={**route.request.headers, 'Authorization': 'Bearer ' + app['control_token']})

                await context.route(url + '/**', authorize)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(url)
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                await page.evaluate("id => window.amplifier.dispatch('session.select',{id})", sid)
                await page.get_by_role('textbox', name='Message Amplifier').fill('UNSENT-PARITY-DRAFT')
                prompt = (
                    'This is a bounded synthetic tool acceptance task. Do not delegate or create goals/schedules. '
                    'First use tool_exec to call bash three times with printf commands that output 11, 17 and 23. '
                    'Use Promise.all and text() to return only their sum, preserving the real call receipts. '
                    'Then use bash action=start with command "sleep 3; printf PARITY_PROCESS_DONE". '
                    'Read/wait using its opaque process_id until you observe the actual exit and output. '
                    'Finally use app_control to create one required question, prompt "Which sample color?", '
                    'options id=blue,label=Blue and id=green,label=Green, dependency="final sample color". '
                    'Do not invent the answer. State the sum and process evidence, then say PARITY_PHASE_ONE.'
                )
                await client.command(sid, 'conversation.send', {'text': prompt}, command_id='phase-one')
                await finished(observation, service, sid, 'PARITY_PHASE_ONE', timeout=240)
                questions = service.questions.store.all(sid)
                report['checks']['question_created_by_agent'] = len(questions) == 1 and questions[0]['createdBy']['origin'] == 'agent'
                if not report['checks']['question_created_by_agent']:
                    raise RuntimeError('Expected one real agent-created question')
                question = questions[0]
                card = page.locator('[data-question-id="' + question['id'] + '"]')
                await card.get_by_role('radio', name='Green', exact=True).check()
                await card.get_by_role('button', name='Submit answer', exact=True).click()
                await observation.wait(lambda: (service.questions.store.get(sid, question['id']).get('delivery') or {}).get('status') == 'accepted', 30)
                await observation.wait(lambda: service._session(sid)['status'] == 'idle' and any(
                    event.get('kind') == 'assistant.message' and event.get('sessionId') == sid and 'green' in event.get('text', '').lower()
                    for event in observation.events), 180)
                report['checks']['real_ui_answer_delivered'] = service.questions.store.get(sid, question['id'])['answer']['optionId'] == 'green'
                await client.command(sid, 'conversation.send', {'text':
                    'Use web_search to find official Python documentation for asyncio.TaskGroup. '
                    'Use web_fetch on an official docs.python.org result and cite its exact returned URL. '
                    'Keep the answer brief and finish with PARITY_WEB_DONE. If actual web access fails, report that truthfully.'}, command_id='web')
                await finished(observation, service, sid, 'PARITY_WEB_DONE', timeout=180)
                history = await service.runtime.control(sid, 'history.snapshot')
                private_json(folder / 'history.json', history)
                names = [row['name'] for row in history['messages'] if row.get('role')=='tool' and row.get('name')]
                outputs = {}
                for row in history['messages']:
                    if row.get('role')=='tool' and row.get('name'):
                        try:
                            outputs.setdefault(row['name'], []).append(json.loads(row['content']))
                        except (ValueError,TypeError):
                            pass
                report['tool_names'] = names
                report['checks']['programmatic_tool_used'] = 'tool_exec' in names
                report['checks']['programmatic_result_confirmed'] = any(result.get('success') is True and '51' in json.dumps(result.get('output',{}).get('output')) and len(result.get('output',{}).get('calls',[]))==3 and all(call.get('success') is True for call in result['output']['calls']) for result in outputs.get('tool_exec',[]))
                report['checks']['web_tools_used'] = {'web_search', 'web_fetch'} <= set(names)
                report['checks']['web_search_real_success'] = any(result.get('success') is True and result.get('output',{}).get('mock') is False for result in outputs.get('web_search',[]))
                report['checks']['web_fetch_real_success'] = any(result.get('success') is True and result.get('output',{}).get('source_url','').startswith('https://docs.python.org/') for result in outputs.get('web_fetch',[]))
                text = '\n'.join(event.get('text', '') for event in observation.events
                                 if event.get('kind') == 'assistant.message' and event.get('sessionId') == sid)
                report['checks']['correct_sum'] = '51' in text
                report['checks']['source_cited'] = 'https://docs.python.org/' in text
                operations = await service.operations.dispatch('operations.list', {'sessionId': sid}, 'ui')
                private_json(folder / 'operations.json', operations)
                # Check real process observations rather than a model's claim.
                records = service.operations.journal.list(sid)
                report['checks']['managed_process_observed'] = any(row['state'] == 'completed' and row['returncode'] == 0 for row in records)
                report['checks']['managed_output_recorded'] = any('PARITY_PROCESS_DONE' in json.dumps(service.operations.journal.read(sid, row['id'])) for row in records)
                report['checks']['selected_chat_preserved'] = await page.evaluate('id => window.amplifier.getState().selectedSessionId === id', sid)
                report['checks']['draft_preserved'] = await page.get_by_role('textbox', name='Message Amplifier').input_value() == 'UNSENT-PARITY-DRAFT'
                await page.screenshot(path=str(folder / 'desktop.png'), full_page=True)
                await page.reload()
                await wait_js(page, 'window.amplifier?.getState()?.client?.id')
                report['checks']['reconnect_question_answer'] = service.questions.store.get(sid, question['id'])['status'] == 'answered'
                report['checks']['no_browser_errors'] = not errors
                report['usage'] = await service.runtime.control(sid, 'usage.inspect')
                report['passed'] = all(report['checks'].values())
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        (folder / 'error.txt').write_text(str(exc))
        for sid, row in service.runtime.workers.items():
            private_json(folder / (sid + '-worker-diagnostics.json'), row.get('stderr', []))
    finally:
        if browser:
            await browser.close()
        await runner.cleanup()
        private_json(folder / 'report.json', report)
        private_json(folder / 'events.json', observation.events)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'],
                      'error_type': report.get('error_type'), 'report': str(folder / 'report.json')}), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--settings', type=Path, default=Path.home() / '.amplifier/settings.yaml')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--module-root', type=Path, help='Optional local integration checkout layout')
    parser.add_argument('--module-source', action='append', default=[], help='Explicit reviewed MODULE=/absolute/path override; repeatable')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required for paid provider calls')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
