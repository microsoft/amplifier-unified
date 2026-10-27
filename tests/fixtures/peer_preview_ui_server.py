"""Actual browser/host peer preview fixture. Synthetic text; no model starts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile

ROOT = Path(sys.argv[1]).resolve()
INSTALLED = os.environ.get('AMPLIFIER_TEST_INSTALLED_STATIC') == '1'
if not INSTALLED:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from aiohttp import web
from amplifier_web import server
from amplifier_web.server import create_app
from amplifier_web.workspace_canvas import workspace_command
from test_collaborative_workspaces import generation, agent_action
from fixtures.peer_message_content import TEXT, LONG, CRLF


class NoModel:
    def __init__(self): self.effects = []
    async def start(self, *args, **kwargs):
        self.effects.append('start'); raise AssertionError('No source model start')
    async def prewarm(self, *args, **kwargs):
        self.effects.append('prewarm'); raise AssertionError('No source warm')
    async def send(self, *args, **kwargs):
        self.effects.append('send'); raise AssertionError('No source input')
    async def collaboration_input(self, *args, **kwargs):
        self.effects.append('collaboration'); raise AssertionError('No source turn')
    async def close(self): pass


async def main():
    attempts = []
    renderer_validations = []
    spawn = asyncio.create_subprocess_exec
    async def query_only(*args, **kwargs):
        if args[:3] == (sys.executable, '-m', 'amplifier_web.shell_query_worker'):
            return await spawn(*args, **kwargs)
        # The synthetic editor uses the same isolated native-renderer validator
        # as production. This is not a source-chat worker or provider execution.
        if len(args) == 5 and args[:2] == (
            shutil.which('node'), str(Path(server.__file__).with_name('shell_validator.mjs'))
        ):
            renderer_validations.append(str(args[1]))
            return await spawn(*args, **kwargs)
        attempts.append([str(x)[:100] for x in args[:3]])
        raise AssertionError('Unexpected worker/provider subprocess')
    from amplifier_web.setup import SetupManager
    import amplifier_web.draft_defaults as defaults
    SetupManager.provider_rows = lambda self, workspace: []
    async def fixture_routing(self, workspace):
        pass
    SetupManager.ensure_routing_catalog = fixture_routing
    async def fixture_defaults(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return {'bundle':bundle or 'work', 'effective':{}, 'providers':[]}
    defaults.resolve_defaults = fixture_defaults
    asyncio.create_subprocess_exec = query_only
    if INSTALLED and Path(server.__file__).resolve().is_relative_to(ROOT):
        raise RuntimeError('Installed trial imported source checkout')
    with tempfile.TemporaryDirectory(prefix='peer-preview-fixture-', dir=ROOT) as directory:
        temp = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(temp/'native'), AMPLIFIER_WEB_HOME=str(temp/'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(temp/'session-state'))
        runtime = NoModel()
        app = await create_app(temp/'app', workspace=temp, runtime=runtime, voice=False,
                               background_updates=False, preload_providers=False)
        app['control_token'] = 'fixture-peer-preview'
        service = app['service']
        if service.history.task:
            service.history.task.cancel(); await asyncio.gather(service.history.task, return_exceptions=True)
            service.history.task = None
        await service.event_log_view.close()
        # Independent existing roots in two registered synthetic workspaces.
        # Set placement before the real app bridge commits the peer receipts.
        review_workspace = temp / 'readiness-review'
        review_workspace.mkdir()
        # Use the registration owner for fixture setup without scheduling a new
        # background discovery pass over this deliberately synthetic history.
        workspace_command(service.state, 'workspace.add', {'path':str(review_workspace), 'name':'Readiness review'})
        a = service._new_session({'title':'Navigation design', 'workspace':str(review_workspace)})
        # The main journey proves same-title disambiguation. Dedicated header
        # and focus journeys retain their existing long-title source by default.
        second_title = ('Navigation design' if os.environ.get('PEER_PREVIEW_DUPLICATE_TITLES') == '1'
                        else 'Accessibility review — keyboard, touch and narrow screen behavior')
        b, recipient = [service._new_session({'title':x, 'workspace':str(temp)}) for x in (
            second_title, 'Release planning')]
        recipient.update(historyLoaded=True, deferRuntimeUntilInteraction=True, draft='Unsent synthetic draft α')
        service.state['sessions'] = [a,b,recipient]
        service.state['selectedSessionId'] = recipient['id']
        service.state['selectedWorkspaceId'] = next(
            row['id'] for row in service.state['workspaces']
            if row.get('path') == recipient['workspace']
        )
        service.state['view'].update(navPinned=False, navExpanded=False, draft=recipient['draft'])
        service._message(recipient,'user','Keep all original formatting and preview without leaving this chat.')
        for source in (a,b):
            await generation(service,source,'g-'+source['id'],['human-'+source['id']])
        older = service._message(a,'assistant',TEXT)
        for i in range(77): service._message(a,'assistant','Earlier synthetic answer %d' % i)
        source_text = service._message(a,'assistant',TEXT)
        source_long = service._message(a,'assistant',LONG)
        source_b_text = service._message(b,'assistant',CRLF)
        service._publish()
        async def send(source, identity, text):
            return await agent_action(service,source,'coordination.send',{'sessionId':recipient['id'],'mode':'notify','text':text},identity)
        sent = [await send(a,'peer-format-one',TEXT),await send(b,'peer-format-two',CRLF),await send(a,'peer-format-three',LONG)]
        service._publish()
        async def facts(request):
            return web.json_response({'sourceA':a['id'],'sourceB':b['id'],'receiving':recipient['id'],
                'originals':[x['messageId'] for x in sent], 'older':older['id'],'sourceText':source_text['id'],'sourceLong':source_long['id'],
                'requests':[x['requestId'] for x in sent],
                'sourceWorkspace':str(review_workspace),'recipientWorkspace':recipient['workspace'],
                'sourceWorkspaceRegistered':any(row['path']==str(review_workspace) for row in service.state['workspaces']),
                'sourceWorkspaceLabel':review_workspace.name,
                'sourceBTitle':b['title'],'sourceBWorkspace':b['workspace'],
                'sourceBWorkspaceLabel':Path(b['workspace']).name,'sourceBText':source_b_text['id'],
                'independentRoots':all(row.get('sessionKind','root')=='root' and not row.get('collaboration') for row in (a,b,recipient)),
                'sourceIds':[message['id'] for message in a['messages']],
                'text':TEXT,'long':LONG,'crlf':CRLF,'textSha256':hashlib.sha256(TEXT.encode()).hexdigest(),
                'effects':runtime.effects,'subprocesses':attempts,'installed':INSTALLED,
                'rendererValidations':renderer_validations,
                'receiverCold':not dict.__contains__(service._session(recipient['id'],hydrate=False),'messages'),
                'backend':str(Path(server.__file__).resolve())})
        app.router.add_get('/api/fixture/peerFacts',facts)
        async def change(request):
            args = await request.json()
            if args['kind']=='arrive': sent.append(await send(a,'peer-format-four',TEXT))
            elif args['kind']=='source-arrive': service._message(a,'assistant','## New captured note\n\nManual refresh only.')
            elif args['kind']=='rename': a['title']='Renamed navigation design'
            elif args['kind']=='archive': service.state.setdefault('conversationOrganization',{}).setdefault('archived',{})[a['id']]=True
            elif args['kind']=='revoke': a['workspace']='/unavailable-synthetic'
            elif args['kind']=='cold-receiver':
                from amplifier_web.cold_display import ColdRecord
                from amplifier_web.resource_files import put
                current = service._session(recipient['id'],hydrate=False)
                reference = put(service.db,current['messages'])
                cold = ColdRecord({**current,'_coldFields':{'messages':reference}},service.db)
                dict.pop(cold,'messages')
                service.state['sessions'] = [cold if row['id']==current['id'] else row for row in service.state['sessions']]
            service._publish()
            return web.json_response({'changed':True})
        app.router.add_post('/api/fixture/change',change)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']=app['allowed_origins']|{url}
        print(json.dumps({'url':url}),flush=True)
        stop=asyncio.Event()
        for sig in (signal.SIGINT,signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(sig,stop.set)
        try: await stop.wait()
        finally: await runner.cleanup()

if __name__ == '__main__': asyncio.run(main())
