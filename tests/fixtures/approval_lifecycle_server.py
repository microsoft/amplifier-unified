"""Authenticated production HTTP/UI over cold saved synthetic approval records.

No providers, configuration writes, external effects, or model calls. Counts prove
whether a passive review attempted to start runtime work, not provider cooperation.
The same fixture can import the isolated installed wheel via PYTHONPATH.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from aiohttp import web
import amplifier_web
from amplifier_web.server import create_app

TOKEN = 'fixture-approval-control-token'


class Runtime:
    def __init__(self):
        self.starts, self.inputs, self.controls, self.decisions = [], [], [], []

    async def start(self, session, emit):
        self.starts.append(session['id'])
        raise AssertionError('Passive review started a worker')

    async def send(self, *args, **kwargs):
        self.inputs.append(True)
        raise AssertionError('Passive review sent input')

    async def control(self, *args, **kwargs):
        self.controls.append(True)
        raise AssertionError('Passive review warmed runtime controls')

    async def approval(self, sid, identity, decision, **kwargs):
        self.decisions.append({'id':identity, 'decision':decision, 'kwargs':kwargs})
        return {'accepted':True}

    async def close(self):
        pass


async def main(home):
    workspace = home / 'workspace'
    workspace.mkdir()
    os.environ['AMPLIFIER_HOME'] = str(home / 'native')
    native = home / 'native'
    native.mkdir()
    (native / 'settings.yaml').write_text(json.dumps({'config':{'providers':[
        {'module':'provider-fixture','id':'fixture','config':{'default_model':'fixture-model'}}]}}))
    app = await create_app(home / 'app', workspace=str(workspace), runtime=Runtime(),
                           voice=False, background_updates=False)
    service = app['service']
    session = service._new_session({'title':'Permission recovery fixture'})
    session.update(status='idle', interruptionRevision=1)
    service.state['sessions'] = [session]
    service.state['selectedSessionId'] = session['id']
    directory = service.data_dir / 'sessions' / session['id']
    directory.mkdir(parents=True,exist_ok=True)
    (directory / 'effective-configuration.json').write_text(json.dumps({'providers':[],'tools':[]}))
    for identity, revision in [('old',0),('current',1)]:
        grant = {'id':'capability:'+identity, 'sessionId':session['id'], 'workspace':session['workspace'],
                 'interruptionRevision':revision, 'revision':1, 'status':'pending', 'createdAt':time.time(),
                 'effects':['file_access'], 'scopes':['session'], 'reason':identity+' file scope',
                 'nativeWait':False}
        service.configuration_authority.save(grant)
        session['approvals'].append({'id':grant['id'],'status':'pending','prompt':grant['reason']})
    # Keep a legacy pending card on disk: hydration must reconcile the cold owner.
    service._save()
    await service.close()
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False)
    app['control_token'] = TOKEN
    service = app['service']
    sid = session['id']
    await service.dispatch('session.select', {'id':sid})

    async def fixture(request):
        if request.headers.get('Authorization') != 'Bearer '+TOKEN:
            raise web.HTTPUnauthorized()
        if request.method == 'POST':
            body = await request.json()
            if body['change'] == 'later':
                grant = {'id':'capability:later', 'sessionId':sid, 'workspace':session['workspace'],
                         'interruptionRevision':1, 'revision':1, 'status':'pending', 'createdAt':time.time(),
                         'effects':['file_access'], 'scopes':['session'], 'reason':'Later independent permission',
                         'nativeWait':False}
                service.configuration_authority.save(grant)
                service._session(sid)['approvals'].append({'id':grant['id'],'status':'pending','prompt':grant['reason']})
                service._publish_changes(sessions={sid})
                return web.json_response({'created':grant['id']})
            grant = service.configuration_authority.record('capability:current')
            if body['change'] == 'expire':
                grant['expiresAt'] = time.time()-1
            elif body['change'] == 'scope':
                grant['reason'] = 'Changed scope requiring another review'
            else:
                raise web.HTTPBadRequest()
            service.configuration_authority.save(grant)
            service.db.commit()  # Deliberately no pushed card: exercise stale UI preflight.
        current = service._session(sid)
        from amplifier_web.attention import session_item_at, session_item_count
        from amplifier_web.navigation_summary import activity
        return web.json_response({'sessionId':sid, 'approvals':current['approvals'],
            'attention':[item for index in range(session_item_count(current))
                         if (item:=session_item_at(current,{},index)) and item['id'].startswith('approval:')],
            'navigation':activity(current),
            'starts':runtime.starts,'inputs':runtime.inputs,'controls':runtime.controls,'decisions':runtime.decisions,
            'source':str(Path(amplifier_web.__file__).resolve()),
            'authoritySha256':hashlib.sha256(Path(amplifier_web.__file__).with_name('configuration_authority.py').read_bytes()).hexdigest()})

    app.router.add_get('/fixture', fixture)
    app.router.add_post('/fixture', fixture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url':url}),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    output = Path(os.environ['AMPLIFIER_TEST_OUTPUT_DIR'])
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(main(Path(tempfile.mkdtemp(prefix='approval-fixture-',dir=output))))