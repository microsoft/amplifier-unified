import sys
import tempfile
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture

async def main(home):
    app = await fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {'http://127.0.0.1:8967'}
    service = app['service']
    server = await service.smart_tools.execute('smartTools.configure', {
        'id':'counter','name':'Independent counter','command':sys.executable,
        'args':[str(Path(__file__).with_name('canvas_mcp_server.py')),sys.argv[1]]})
    await service.smart_tools.execute('smartTools.connect', {'id':'counter'})
    return app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-mcp-ui-') as tmp:
        web.run_app(main(Path(tmp)), host='127.0.0.1', port=8967, print=None)
