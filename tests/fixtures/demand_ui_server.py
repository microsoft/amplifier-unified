"""Synthetic work groups for demand-loading browser acceptance."""
import tempfile
from pathlib import Path
from aiohttp import web
import timeline_ui_server as fixture

async def main(home):
    app=await fixture.main(home)
    service=app['service'];session=service._session()
    session['execution']['nodes'].extend([
        {'id':f'bulk-{i}','kind':'tool','turnId':'voice:third','label':f'Step {i}',
         'phase':'completed','startedAt':13,'endedAt':14,'output':f'Output {i} '+('large result '*5000)}
        for i in range(125)])
    original=session['id']
    await service.dispatch('session.create', {'title':'Second fixture chat'})
    other=service._session();other['messages']=[{'id':'other-message','role':'assistant','text':'Second chat content'}]
    service.state['selectedSessionId']=original
    async def control(request):
        args=await request.json()
        if args.get('op')=='update-step':
            session['execution']['nodes'][-1]['label']='Updated last step'
        service._publish()
        return web.json_response({'first':original,'second':other['id']})
    app.router.add_post('/api/fixture/work',control)
    service._publish()
    return app

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-demand-ui-') as tmp:
        web.run_app(main(Path(tmp)),host='127.0.0.1',port=8958,print=None)
