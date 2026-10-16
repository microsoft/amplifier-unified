"""Persistent owned-DTU fixture: synthetic account/runtime, no live credentials."""
import asyncio
import json
import os
from pathlib import Path
import sys
import signal
import tempfile
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    home = Path(home)
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
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'runtime': 'synthetic'}), flush=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        for signum in signals:
            loop.remove_signal_handler(signum)


if __name__ == '__main__':
    if home := os.environ.get('TEMPO_PAIRING_FIXTURE_HOME'):
        asyncio.run(main(home))
    else:
        with tempfile.TemporaryDirectory(prefix='tempo-pairing-') as home:
            asyncio.run(main(home))
