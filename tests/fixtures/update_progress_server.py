"""Real HTTP/SSE Updates UI with synthetic transitions, no installation/model work."""
import asyncio
import json
import time
from aiohttp import web
from settings_ui_server import main as settings_app
from pathlib import Path
import tempfile

async def main(home):
    app=await settings_app(home)
    service=app['service']
    manager=service.update_manager
    diagnostics=manager.diagnostics
    diagnostics.begin('ecosystem','a'*32,tier='included',components=['foundation'])
    diagnostics.record('ecosystem-activation','succeeded')
    diagnostics.begin('ecosystem','b'*32,tier='other',components=['amplifier-module-hooks-approval'])
    attempt=diagnostics.state['attemptId']
    service.state['updates'].update(phase='validating',error=None,pendingRelease=None,
        sequence={'stage':'other','install':True},
        probeProgress={'attemptId':attempt,'phase':'prepare','completed':24,'total':37,'startedAt':time.time()})
    async def transition(request):
        stage=(await request.json())['stage']
        if stage=='waiting':
            await manager.publish(phase='staged',pendingRelease='b'*32,
                                  detail='Waiting for active work and calls to finish.')
        elif stage=='installed':
            diagnostics.record('ecosystem-activation','succeeded')
            await manager.publish(phase='installed',pendingRelease=None,error=None,
                                  sequence={'stage':'complete','install':False})
        elif stage=='failed':
            diagnostics.record('ecosystem-prepare','failed',errorType='BundleNotFoundError')
            await manager.publish(phase='error',pendingRelease=None,error='Synthetic validation failure; active generation preserved.')
        elif stage=='compatibility':
            await manager.publish(probeProgress={'attemptId':attempt,'phase':'compatibility','completed':12,'total':37,'startedAt':time.time()})
        else:raise web.HTTPBadRequest()
        return web.json_response({'ok':True,'stage':stage})
    app.router.add_post('/fixture/update-progress',transition)
    service._publish()
    return app

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-update-progress-') as tmp:
        web.run_app(main(Path(tmp)),host='127.0.0.1',port=8957,print=None)
