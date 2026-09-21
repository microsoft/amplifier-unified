"""Isolated real voice-ingress/worker acceptance; local deterministic providers only.

Exercises the public voice HTTP routes and real WebSocket receive/adapter path,
not microphone capture, WebRTC media or remote speech-model behavior. No account
settings or credentials are loaded. The output directory must be new.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def until(predicate, timeout=60):
    async with asyncio.timeout(timeout):
        while not predicate(): await asyncio.sleep(.05)


class LocalVoiceProvider:
    """Wire-compatible control/sideband fixture; never generates real audio."""
    def __init__(self):
        self.connections, self.sockets, self.outputs, self.offers = [], {}, [], []
        self.runner = None

    async def start(self):
        app = web.Application()
        async def connect(request):
            fields = {}
            reader = await request.multipart()
            while (part := await reader.next()) is not None: fields[part.name] = await part.text()
            self.offers.append(fields['sdp'])
            identity = 'fixture-call-' + str(len(self.connections) + 1)
            self.connections.append(identity)
            return web.Response(text='v=0\r\ns=synthetic-answer\r\n', headers={'Location':'/v1/realtime/calls/' + identity})
        async def attach(request):
            socket = web.WebSocketResponse(); await socket.prepare(request)
            identity = request.query['call_id']; self.sockets[identity] = socket
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    event = json.loads(message.data)
                    if event.get('type') == 'conversation.item.create' and event.get('item', {}).get('type') == 'function_call_output':
                        self.outputs.append({'voiceId':identity, **event['item']})
            return socket
        async def hangup(request): return web.json_response({'ok':True})
        app.router.add_post('/v1/realtime/calls', connect)
        app.router.add_get('/v1/realtime', attach)
        app.router.add_post('/v1/realtime/calls/{identity}/hangup', hangup)
        self.runner = web.AppRunner(app); await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        return self

    async def speak(self, identity, text, item_id, delegation_id):
        socket=self.sockets[identity]
        transcript={'type':'conversation.item.input_audio_transcription.completed','event_id':item_id+'-event','item_id':item_id,'transcript':text}
        delegation={'type':'response.function_call_arguments.done','event_id':delegation_id+'-event','call_id':delegation_id,'name':'amplifier_delegate','arguments':json.dumps({'text':text})}
        # Duplicate wire events and a second event with the same call ID exercise
        # both receive-event and delegation identity guards.
        for event in [transcript,transcript,delegation,delegation,{**delegation,'event_id':delegation_id+'-retry'}]:
            await socket.send_json(event)

    async def close(self):
        for socket in self.sockets.values(): await socket.close()
        if self.runner: await self.runner.cleanup()


class LocalTransport:
    """Replace only remote transport, retaining actual VoiceService protocol code."""
    def __init__(self, session, base): self.session,self.base=session,base
    def url(self, url):
        value=urlsplit(url)
        assert value.hostname == 'api.openai.com', 'Unexpected remote transport target'
        return self.base + value.path + ('?'+value.query if value.query else '')
    def request(self, method, url, **kwargs): return self.session.request(method,self.url(url),**kwargs)
    async def ws_connect(self,url,**kwargs): return await self.session.ws_connect(self.url(url),**kwargs)


PROVIDER = '''import json
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,ToolCall,Usage
class Provider:
    name='fixture'
    def __init__(self,config): self.log=Path(LOG);self.count=0
    def get_info(self): return ProviderInfo(id='fixture',display_name='Local voice question fixture',defaults={'model':'fixture'})
    async def list_models(self):return []
    def parse_tool_calls(self,response):return response.tool_calls or []
    async def complete(self,request,**kwargs):
        self.count+=1
        with self.log.open('a') as stream:stream.write('call\\n')
        users=[(i,m) for i,m in enumerate(request.messages) if m.role=='user']
        index,user=users[-1]
        text=user.content if isinstance(user.content,str) else str(user.content)
        text=text.rsplit('Current spoken user request:\\n',1)[-1]
        if 'Answer to “' in text:return ChatResponse(content=[TextBlock(text='VOICE_CHOICE_APPLIED')])
        results=[m for m in request.messages[index+1:] if m.role=='tool']
        if not results:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='read-'+str(self.count),name='app_control',arguments={'operation':'get_state','args':{}})])
        value=json.loads(results[-1].content)
        while 'output' in value and isinstance(value['output'],dict):value=value['output']
        if 'session' not in value:
            if value.get('result',{}).get('status')=='answered':return ChatResponse(content=[TextBlock(text='VOICE_ANSWER_RECORDED')])
            raise ValueError('Unexpected app-control result shape: '+','.join(value))
        session=value['session']
        pending=[q for q in session['questions'] if q['status']=='pending']
        if 'Inspect the pending question' in text:
            assert pending and pending[0]['required']
            return ChatResponse(content=[TextBlock(text='INDEPENDENT_READ_WHILE_PENDING')])
        source=next(m for m in session['recentMessages'] if m.get('voiceItemId')=='spoken-answer' and m.get('inputOrigin')=='voice')
        question=pending[0]
        return ChatResponse(content=[],tool_calls=[ToolCall(id='answer-'+str(self.count),name='app_control',arguments={'operation':'dispatch','args':{'action':'question.answer','id':'voice-choice-answer','args':{'id':question['id'],'expectedRevision':question['revision'],'optionId':'plain','sourceMessageId':source['id']}}})])
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(config),name='fixture')
'''


async def run(folder):
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in ('app','workspace','shared','ownership'):(folder/name).mkdir(mode=0o700)
    os.environ.update(AMPLIFIER_HOME=str(folder/'shared'),AMPLIFIER_WEB_HOME=str(folder/'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(folder/'legacy'),AMPLIFIER_SESSION_STATE_HOME=str(folder/'ownership'))
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient
    import amplifier_web.runtime_worker as worker
    import amplifier_module_context_simple as context
    import amplifier_module_loop_live as loop
    module=folder/'provider'/'amplifier_module_provider_fixture';module.mkdir(parents=True)
    module.joinpath('__init__.py').write_text('LOG = ' + repr(str(folder/'provider-calls')) + '\n' + PROVIDER)
    plan={'bundle':{'name':'voice-question-fixture'},'session':{
        'orchestrator':{'module':'loop-live','source':str(Path(loop.__file__).parent.parent),'config':{'max_iterations':6}},
        'context':{'module':'context-simple','source':str(Path(context.__file__).parent.parent)}},
        'providers':[{'module':'provider-fixture','source':str(module.parent),'config':{'log':str(folder/'provider-calls')}}]}
    bundle=folder/'bundle.md';bundle.write_text('---\n'+yaml.safe_dump(plan)+'---\nAnswer only the synthetic fixture request.\n')
    report={'scenario':'voice-question-ingress-after-reconnect','source':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        'harnessSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':{},'paidCalls':0,'physicalMicrophone':False,'remoteSpeechProvider':False}
    voice=await LocalVoiceProvider().start()
    http=aiohttp.ClientSession(); runner=None; service=None; events=[]; sends=[]
    async def start():
        nonlocal runner,service
        app=await create_app(folder/'app',workspace=folder/'workspace',voice=True,background_updates=False,preload_providers=False)
        service=app['service'];service.runtime.command=[sys.executable,str(Path(worker.__file__).resolve())]
        manager=app['voice_service'];manager.api_key='local-fixture-only';manager.http=LocalTransport(http,voice.url);manager.owns_http=False
        original=service.on_runtime_event
        async def observed(kind,data):
            if kind in {'assistant.message','runtime.generation','runtime.error'}:events.append({'kind':kind,**data})
            await original(kind,data)
        service.on_runtime_event=observed
        send=service.runtime.send
        async def tracked(session,text,input_id,emit):
            sends.append({'sessionId':session['id'],'inputId':input_id})
            return await send(session,text,input_id,emit)
        service.runtime.send=tracked
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']=app['allowed_origins']|{url}
        return app,url
    async def post(app,url,path,data):
        async with http.post(url+path,json=data,headers={'Authorization':'Bearer '+app['control_token']}) as response:
            result=await response.json();assert response.status==200,result;return result
    try:
        app,url=await start()
        async with SessionClient(url,app['control_token'],'voice-question-fixture') as client:
            created=await client.create_session({'title':'Voice answer target','workspace':str(folder/'workspace'),'bundle':str(bundle)},command_id='target')
            sid=created['state']['selectedSessionId']
            q=(await client._json('POST','/api/actions',{'action':'question.create','args':{'sessionId':sid,'prompt':'Which style should I use?','required':True,'dependency':'Write the dependent report','options':[{'id':'plain','label':'Plain language'},{'id':'technical','label':'Technical'}]},'id':'ask'}))['result']
            offer='v=0\r\ns=synthetic-offer\r\n'
            first=await post(app,url,'/api/voice/connect',{'sessionId':sid,'provider':'realtime','sdp':offer})
            await post(app,url,'/api/voice/ready',{'id':first['id']})
            await voice.speak(first['id'],'Inspect the pending question without answering.','independent-read','read-request')
            await until(lambda:any(row['voiceId']==first['id'] for row in voice.outputs))
            report['checks']['independent_real_tool_read_while_pending']=any('INDEPENDENT_READ_WHILE_PENDING' in row['output'] for row in voice.outputs)
            if not report['checks']['independent_real_tool_read_while_pending']: raise RuntimeError('Local voice result: ' + voice.outputs[-1]['output'][:1000])
            report['checks']['question_still_pending']=service.questions.store.get(sid,q['id'])['status']=='pending'
            await post(app,url,'/api/voice/end',{'id':first['id']})
        await runner.cleanup();runner=None
        app,url=await start()  # Entire host/worker restart, not just UI state.
        report['checks']['pending_question_survives_host_restart']=service.questions.store.get(sid,q['id'])['status']=='pending'
        async with SessionClient(url,app['control_token'],'voice-question-fixture') as client:
            other=await client.create_session({'title':'Selected elsewhere','workspace':str(folder/'workspace'),'bundle':str(bundle)},command_id='other')
            selected=other['state']['selectedSessionId']
            await client._json('POST','/api/actions',{'action':'view.update','args':{'patch':{'draft':'VOICE-UNSENT-DRAFT'}},'id':'draft'})
            second=await post(app,url,'/api/voice/connect',{'sessionId':sid,'provider':'realtime','sdp':offer})
            await post(app,url,'/api/voice/ready',{'id':second['id']})
            await voice.speak(second['id'],'Plain language, please.','spoken-answer','answer-request')
            await until(lambda:service.questions.store.get(sid,q['id'])['status']=='answered' and service.questions.store.get(sid,q['id'])['delivery']['status']=='accepted')
            await until(lambda:any(row['voiceId']==second['id'] for row in voice.outputs) and service._session(sid)['status']=='idle')
            answer=service.questions.store.get(sid,q['id']);provenance=answer['answer']['provenance']
            source=next(row for row in service._session(sid)['messages'] if row['id']==provenance['messageId'])
            report['checks']['exact_voice_ingress_provenance']=provenance['voiceId']==second['id'] and provenance['voiceItemId']=='spoken-answer' and provenance['inputOrigin']=='voice' and provenance['text']=='Plain language, please.' and source['voiceItemId']=='spoken-answer'
            report['checks']['agent_cites_actual_user_message']=provenance['origin']=='agent' and provenance['via']=='call'
            report['checks']['answer_saved_and_delivered']=answer['revision']==2 and answer['answer']['optionId']=='plain' and answer['delivery']['status']=='accepted'
            report['checks']['single_delegation_after_duplicate_wire_events']=sum(row['inputId']=='voice:'+second['id']+':answer-request' for row in sends)==1
            report['checks']['single_answer_input']=sum(row['inputId']==answer['delivery']['inputId'] for row in sends)==1
            report['checks']['matching_result_returned_on_real_sideband']=len([row for row in voice.outputs if row['voiceId']==second['id']])==1
            report['checks']['answer_reaches_actual_worker']=any(row.get('kind')=='assistant.message' and row.get('text')=='VOICE_CHOICE_APPLIED' for row in events)
            state=(await client._json('GET','/api/state'))
            report['checks']['selection_preserved']=state['selectedSessionId']==selected
            report['checks']['draft_preserved']=state['view']['draft']=='VOICE-UNSENT-DRAFT'
            report['checks']['not_permission_approval']=not service._session(sid)['approvals'] and not answer.get('permission')
            report['checks']['sdp_bytes_preserved']=voice.offers==[offer,offer]
            await post(app,url,'/api/voice/end',{'id':second['id']})
            # Retry the actual app_control command with its original provenance;
            # app command receipt must return the saved answer without delivery.
            before=len(sends)
            retry=await service.app_bridge('dispatch',{'action':'question.answer','id':'voice-choice-answer','args':{'id':q['id'],'expectedRevision':1,'optionId':'plain','sourceMessageId':source['id']}},sid)
            report['checks']['duplicate_answer_receipt_no_replay']=retry['duplicate'] and len(sends)==before
            report['sourceMessageId']=source['id'];report['questionId']=q['id'];report['voiceId']=second['id'];report['answerInputId']=answer['delivery']['inputId']
        await runner.cleanup();runner=None
        app,url=await start()
        report['checks']['answered_restart_no_replay']=service.questions.store.get(sid,q['id'])['delivery']==answer['delivery'] and len(sends)==before
        report['localProviderCalls']=len((folder/'provider-calls').read_text().splitlines())
        report['voiceConnections']=len(voice.connections)
        report['passed']=all(report['checks'].values())
    except Exception as exc:
        report['passed']=False;report['error']=type(exc).__name__+': '+str(exc)[:1000]
        report['runtimeErrors']=[row.get('error') for row in events if row['kind']=='runtime.error']
    finally:
        if runner:await runner.cleanup()
        elif service and not service.closed:await service.close()
        await voice.close();await http.close()
        (folder/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
    return report


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=asyncio.run(run(args.output.resolve()))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
