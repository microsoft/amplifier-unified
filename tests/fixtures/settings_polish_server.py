"""Local visual audit fixture. Never loaded by production; synthetic data only."""
import asyncio
import settings_refinements_server
import settings_collections_server as collections
from aiohttp import web
original=collections.main
async def main(home):
    app=await original(home)
    async def scenario(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
        service=app['service'];name=(await request.json())['name']
        updates=service.state['updates'];updates.update(phase='checked',detail='',error=None,pendingApp=None,pendingRelease=None,pendingRestart=None,pendingReplacement=None,diagnostics={},sequence={})
        if name=='checking':updates.update(phase='checking',detail='Checking included components…',sequence={'stage':'included','install':True})
        if name=='waiting':updates.update(phase='staged',pendingRelease={'id':'fixture'},detail='Waiting for your work to finish.')
        if name=='error':updates.update(phase='error',error='The update could not finish. Your installed version is unchanged.',diagnostics={'attemptId':'fixture','lastFailure':{'phase':'ecosystem-runtime-preflight','status':'failed','attemptId':'fixture','reason':'protected-runtime-source','package':'fixture-module'}})
        if name=='release':
            service.attention.reviewed.clear() if hasattr(service.attention,'reviewed') else None
        service._publish()
        return web.json_response({'ok':True})
    app.router.add_post('/fixture/scenario',scenario)
    return app
collections.main=main
if __name__=='__main__':asyncio.run(collections.serve())
