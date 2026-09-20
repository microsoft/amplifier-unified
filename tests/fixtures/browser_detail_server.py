"""Disposable heavy conversation browser fixture. No model providers or user data."""
import asyncio, copy, json, os, signal, sys, tempfile, time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from aiohttp import web
from amplifier_web.server import create_app

class Runtime:
    def __init__(self): self.starts=0; self.sends=0
    async def start(self, session, emit):
        self.starts+=1
        await emit('runtime.status', {'sessionId':session['id'], 'status':'ready'})
    async def send(self, session, text, input_id, emit):
        self.sends+=1
        await emit('assistant.message', {'sessionId':session['id'], 'text':'Synthetic reply.', 'inputId':input_id})
        await emit('runtime.status', {'sessionId':session['id'], 'status':'idle'})
    async def control(self, *args): return {}
    async def stop(self, *args): pass
    async def close(self): pass

async def main():
    with tempfile.TemporaryDirectory(prefix='unified-issue-repro-') as directory:
        temp=Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(temp/'native'), AMPLIFIER_WEB_HOME=str(temp/'app'),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(temp/'session-state'))
        alpha=temp/'Alpha'; beta=temp/'Beta'; alpha.mkdir(); beta.mkdir()
        app=await create_app(temp/'app', workspace=alpha, runtime=Runtime(), voice=False,
            background_updates=False, preload_providers=False)
        app['control_token']='fixture-detail-token'
        service=app['service']; await service.history.close()
        await service.dispatch('session.create', {'title':'Alpha conversation','workspace':str(alpha)})
        aid=service.state['selectedSessionId']
        await service.dispatch('session.create', {'title':'Beta conversation','workspace':str(beta)})
        bid=service.state['selectedSessionId']
        await service.dispatch('session.select', {'id':aid})
        for i, row in enumerate(service.state['sessions']):
            row.update(status='idle', historyLoaded=True, deferRuntimeUntilInteraction=True,
                messages=[{'id':f'answer-{i}', 'role':'assistant','text':'A saved response.', 'via':'chat', 'createdAt':time.time()}])
        service.state['view'].update(navPinned=True, navExpanded=True)
        service.state.setdefault('setup',{}).update(providers=[],providersLoadedAt=time.time(),providersWorkspace=str(alpha))
        service._publish()
        baseline=copy.deepcopy(service.state)
        async def control(request):
            args=await request.json(); op=args.get('op')
            if op=='native-history':
                from amplifier_web.session_files import project_slug
                native=temp/'native'/'projects'/project_slug(alpha)/'sessions'/'paging-fixture'
                native.mkdir(parents=True)
                (native/'metadata.json').write_text(json.dumps({'session_id':'paging-fixture',
                    'working_dir':str(alpha),'name':'Live saved history','bundle':'bundle:anchors'}))
                (native/'transcript.jsonl').write_text(''.join(json.dumps({'role':'user' if i%2==0 else 'assistant',
                    'content':f'Saved message {i}\n\n'+('A retained paragraph. '*25)})+'\n' for i in range(305)))
                await service.history.refresh()
                row=next(row for row in service.state['sessions'] if row.get('nativeIdentity')=='paging-fixture')
                await service.history.load(row['id'])
                row.update(status='ready',preparation={'status':'active'})
                service.state.update(selectedSessionId=row['id'],selectedWorkspaceId=row['workspaceId'])
            elif op=='live-response':
                row=service._session(args['id'])
                await service.on_runtime_event('runtime.status',{'sessionId':row['id'],'status':'working'})
                await service.on_runtime_event('assistant.message',{'sessionId':row['id'],'text':'A new live response.'})
                await service.on_runtime_event('assistant.delta',{'sessionId':row['id'],'text':'Still writing...'})
            elif op=='reset':
                revision=service.state['revision']
                service.state.clear();service.state.update(copy.deepcopy(baseline));service.state['revision']=revision
            elif op=='patch':
                service.state['view'].update(args.get('view',{}))
                for sid,patch in args.get('sessions',{}).items(): service._session(sid).update(patch)
            elif op=='heavy':
                for sid,count in [(aid,args.get('messages',520)),(bid,args.get('otherMessages',549))]:
                    row=service._session(sid)
                    row.update(status='working' if args.get('active',False) else 'idle',messages=[{
                        'id':f'{sid}-m-{i}','role':'user' if i%2==0 else 'assistant','via':'chat','createdAt':1700000000+i,
                        'text':f'## Retained message {i}\n\n'+('A paragraph with **formatting** and [a link](https://example.com).\n\n'*100)[:args.get('chars',4000)]
                    } for i in range(count)])
                    row['execution']={'turns':[{'id':'turn','phase':'completed','startedAt':1700000000,'anchorMessageId':row['messages'][0]['id']}],
                        'nodes':[{'id':f'node-{i}','turnId':'turn','kind':'tool','phase':'completed','label':'Synthetic tool result',
                        'summary':'x'*args.get('nodeBytes',10000)} for i in range(args.get('nodes',0))]}
                for index, count in enumerate(args.get('extraMessageCounts', [])):
                    row=copy.deepcopy(service._session(aid))
                    identity=f'extra-{index}'
                    row.update(id=identity,title=f'Additional conversation {index}',status='idle',nativeIdentity=None,runtimeSessionId=None,
                        messages=[{'id':f'{identity}-m-{i}','role':'assistant','text':'Retained text. '*350,'createdAt':1700000000+i} for i in range(count)],execution={'turns':[],'nodes':[]})
                    service.state['sessions'].append(row)
            elif op=='inspection':
                from amplifier_web.execution import ensure_turn
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.runtime import normalize_event
                row=service._session(aid);now=time.time()
                row.update(status='working',messages=[{'id':'question','role':'user','text':'Inspect this work','createdAt':now-5},
                    {'id':'long-answer','role':'assistant','text':'# Complete response\n\n'+('A visible paragraph with **formatting**.\n\n'*450)+'COMPLETE-RESPONSE-END','createdAt':now}],execution={'turns':[],'nodes':[]})
                ensure_turn(row,'inspect');row['execution']['turns'][0].update(startedAt=now-3,anchorMessageId='question')
                emitted=[];events=ExecutionEvents(aid,emitted.append);events.turn_id='inspect'
                events.hook(aid,'tool:pre',{'tool_call_id':'inspect-tool','tool_name':'fixture.read','tool_input':{'action':'inspect fixture','path':'/fixture/public.txt','authorization':'Bearer never-publish-this','text':'input '*150}})
                events.hook(aid,'tool:post',{'tool_call_id':'inspect-tool','tool_result':{'success':True,'output':'Result line. '*2000+'RESULT-END','url':'https://example.com/artifact'}})
                for event in emitted:
                    kind,data=normalize_event(event,aid);await service.on_runtime_event(kind,data)
                await service.on_runtime_event('execution.event',{'id':'live-model','kind':'llm','sessionId':aid,'rootSessionId':aid,'turnId':'inspect','label':'Model call','phase':'running','startedAt':now-2})
            elif op=='inspection-finish':
                now=time.time()
                await service.on_runtime_event('execution.event',{'id':'live-model','kind':'llm','sessionId':aid,'rootSessionId':aid,'turnId':'inspect','label':'Model call','phase':'completed','endedAt':now,'usage':{'inputTokens':100,'outputTokens':20,'totalTokens':120,'costUsd':.004,'costType':'reported'}})
                await service.on_runtime_event('runtime.status',{'sessionId':aid,'status':'idle'})
            elif op=='many':
                template=copy.deepcopy(service._session(aid)); now=time.time()
                service.state['sessions'].extend([{**copy.deepcopy(template),'id':f'library-{i}','title':f'Library conversation {i:03}',
                    'createdAt':now-i,'recentActivityAt':now-i,'messages':[],'nativeIdentity':None,'runtimeSessionId':None}
                    for i in range(args.get('count',205))])
            service._publish()
            return web.json_response({'alpha':aid,'beta':bid,'revision':service.state['revision'], 'state':service.get_state(),
                'runtimeStarts':service.runtime.starts,'runtimeSends':service.runtime.sends,
                'retainedSessions':len(service.state['sessions']),
                'retainedMessages':sum(len(row.get('messages',[])) for row in service.state['sessions']),
                'canonicalBytes':len(json.dumps(service.state['sessions']).encode())})
        app.router.add_post('/api/fixture/control',control)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        print(json.dumps({'port':port,'alpha':aid,'beta':bid}),flush=True)
        stopped=asyncio.Event()
        for sig in [signal.SIGTERM,signal.SIGINT]:asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
        await stopped.wait();await runner.cleanup()

asyncio.run(main())
