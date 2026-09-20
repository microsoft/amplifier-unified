"""Reopen a finished synthetic run's artifact in Chromium with execution disabled.

No credentials, provider calls, code cells or historical inputs are resumed.
This supplements live acceptance when a screenshot raced the canvas selection.
"""
import argparse
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path

from acceptance import private_json
from browser_acceptance import wait_js


def transcripts(folder):
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('transcript.jsonl') if '.venv' not in path.parts}


async def run(args):
    from aiohttp import web
    from playwright.async_api import async_playwright, expect
    from amplifier_web.server import create_app
    source, target = args.run.resolve(), args.output.resolve()
    if not (source / 'report.json').exists():
        raise ValueError('The source must be a finished synthetic acceptance run')
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.environ.update(AMPLIFIER_HOME=str(source / 'shared'), AMPLIFIER_WEB_HOME=str(source / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(source / 'ownership'))
    original = transcripts(source)
    app = await create_app(source / 'app', workspace=source / 'workspace', runtime=False,
                           voice=False, preload_providers=False, background_updates=False)
    report = {'schema_version': 1, 'source_run': source.name, 'provider_calls': 0,
              'execution': 'disabled', 'checks': {}, 'passed': False}
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    browser = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1440, 'height': 1050})
            async def authorize(route):
                await route.continue_(headers={**route.request.headers, 'Authorization':'Bearer '+app['control_token']})
            await context.route(url+'/**', authorize)
            page = await context.new_page()
            await page.goto(url)
            await wait_js(page, 'window.amplifier?.getState()?.client?.id')
            sessions = app['service'].state['sessions']
            session = next(row for row in sessions if row.get('workspace') and Path(row['workspace']).resolve() == (source / 'workspace').resolve())
            await page.evaluate("id => window.amplifier.dispatch('session.select',{id})", session['id'])
            await wait_js(page, '(window.amplifier.getState().canvasArtifacts || []).some(row => row.title === \"Live calculation\")')
            records = await page.evaluate('window.amplifier.getState().canvasArtifacts')
            artifact = next(row for row in records if row['title'] == 'Live calculation')
            await page.evaluate("async value => {await window.amplifier.dispatch('session.select',{id:value.sessionId}); await window.amplifier.dispatch('canvas.select',{id:value.id}); await window.amplifier.dispatch('view.update',{patch:{canvasDraft:{library:false}}});}", artifact)
            await wait_js(page, '!window.amplifier.getState().view?.canvasDraft?.library')
            async with asyncio.timeout(30):
                while True:
                    matches = []
                    for frame in page.frames:
                        if frame != page.main_frame and await frame.locator('#result').count():
                            owner = await frame.frame_element()
                            if await owner.is_visible() and await frame.locator('#result').is_visible():
                                matches.append(frame)
                    if matches:
                        frame = matches[0]
                        break
                    await asyncio.sleep(.1)
            await expect(frame.locator('#result')).to_have_text('43')
            report['checks']['visible_recalculated_value'] = True
            report['checks']['real_saved_html_view'] = artifact['kind'] == 'html'
            await page.screenshot(path=str(target / 'visible-artifact.png'), full_page=True)
            report['checks']['execution_disabled'] = app['service'].runtime is False
            report['checks']['transcript_unchanged'] = original == transcripts(source)
            report['passed'] = all(report['checks'].values())
            await browser.close()
            browser = None
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        (target / 'error.txt').write_text(str(exc))
    finally:
        if browser:
            with contextlib.suppress(Exception):
                await browser.close()
        await runner.cleanup()
        private_json(target / 'report.json', report)
    print(json.dumps(report), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
