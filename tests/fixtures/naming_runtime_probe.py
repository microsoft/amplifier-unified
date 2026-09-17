"""Exercise the actual Foundation naming implementation with a local provider."""
import asyncio,json,sys,tempfile
from pathlib import Path
from types import SimpleNamespace

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
sys.path.insert(0,str(Path(sys.argv[1])))
from amplifier_core import HookResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage
from amplifier_web.host.naming import LiveSessionNaming
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import persist
from amplifier_web.execution_events import ExecutionEvents

class Hooks:
    def __init__(self):self.handlers={}
    def register(self,event,callback,**kwargs):self.handlers.setdefault(event,[]).append(callback)
    async def emit(self,event,data):
        for callback in self.handlers.get(event,[]):await callback(event,data)
        return HookResult()

async def run():
    calls=[];events=[]
    with tempfile.TemporaryDirectory() as tmp:
        home=Path(tmp)
        class Provider:
            name='fixture';priority=1
            def get_info(self):return SimpleNamespace(id='fixture',defaults={'model':'naming-fixture'})
            async def complete(self,request,**kwargs):
                calls.append(request)
                value={'action':'set','name':'Build an orbit explorer','description':'An interactive solar system with camera controls.'}
                return ChatResponse(content=[TextBlock(text=json.dumps(value))],usage=Usage(input_tokens=50,output_tokens=15,total_tokens=65))
        provider=Provider();telemetry=ExecutionEvents('fixture',events.append);telemetry.instrument_provider('fixture',provider)
        class Context:
            async def get_messages(self):return [{'role':'user','content':'Create an interactive solar system'},{'role':'assistant','content':'I built orbit controls'},{'role':'user','content':'Add camera rotation'}]
        context=Context();hooks=Hooks()
        coordinator=SimpleNamespace(session_id='fixture',config={'hooks':[{'module':'hooks-session-naming','config':{'model_role':None}}]},hooks=hooks,mount_points={'context':context},get=lambda k:{'providers':{'fixture':provider},'context':context}.get(k),get_capability=lambda k:None,register_cleanup=lambda f:None)
        def publish(e):
            events.append(e)
            if e['type']=='session.naming':persist(home,{'id':'fixture','title':e['name'],'titleSource':'generated','description':e['description']})
        SessionStore(home/'sessions').save('fixture',await context.get_messages(),{})
        namer=LiveSessionNaming(coordinator,home,publish)
        assert namer.hook
        for i in range(1,6):
            namer.observe({'type':'input.delivered','input_id':str(i),'source':'user'})
            namer.observe({'type':'generation.finished','input_ids':[str(i)]})
            if namer.pending:await namer.pending
        assert len(calls)==2,len(calls)
        assert calls[0].max_output_tokens==256
        metadata=SessionStore(home/'sessions').load('fixture')[1]
        assert metadata['name']=='Build an orbit explorer'
        rows=[e['event'] for e in events if e.get('type')=='execution.event' and e['event'].get('phase')=='completed']
        assert len(rows)==2 and all(r['label']=='Session naming' and r['usage']['totalTokens']==65 for r in rows),rows
        assert [r['turnId'] for r in rows]==['2','5']
        await namer.close()
    print('Real Foundation hook verified: first name after turn 2, description at turn 5, configured provider, bounded request, durable metadata, attributed usage; no network calls.')
asyncio.run(run())
