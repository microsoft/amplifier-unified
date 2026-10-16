"""Persistent owned-DTU fixture: synthetic account/runtime, no live credentials."""
import asyncio
import json
import os
from pathlib import Path
import sys
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main():
    home = Path(os.environ['TEMPO_PAIRING_FIXTURE_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    os.environ.update(AMPLIFIER_HOME=str(home / 'amplifier'), AMPLIFIER_WEB_HOME=str(home / 'app'))
    from amplifier_web.server import create_app
    import amplifier_web.auth as auth
    # Only this explicitly launched test process substitutes PAM.
    auth.authenticate_pam = lambda username, password: username == 'fixture' and password == 'synthetic-only'
    app = await create_app(home / 'app', workspace=home, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.environ.get('TEMPO_PAIRING_FIXTURE_PORT', '8995'))
    url = f'http://127.0.0.1:{port}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    await web.TCPSite(runner, '127.0.0.1', port).start()
    print(json.dumps({'url': url, 'runtime': 'synthetic'}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
