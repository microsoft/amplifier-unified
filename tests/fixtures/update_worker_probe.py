"""Actual Worker processes and saved native work across an isolated app restart.

The provider is scripted and offline. The update's healthy-install receipt is
supplied by this fixture; it does not exercise uv/package replacement or HTTP
health qualification. Those existing updater paths are tested separately.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

PROVIDER = r'''import asyncio,hashlib,json,os,sys
from pathlib import Path
from amplifier_core import ProviderInfo,ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,ToolCall
class FixtureProvider:
    name='fixture'
    def __init__(self,coordinator=None):self.coordinator=coordinator
    def get_info(self):return ProviderInfo(id='fixture',display_name='Fixture',defaults={'model':'fixture'})
    async def list_models(self):return []
    def parse_tool_calls(self,response):return response.tool_calls or []
    async def complete(self,request,**kwargs):
        v=json.loads(Path(SCENARIO).read_text())
        child=bool(self.coordinator.get_capability('live.child'))
        modules={name:{'path':str(Path(module.__file__).resolve()),'sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()} for name in ('amplifier_module_loop_live','amplifier_module_loop_streaming') if (module:=sys.modules.get(name)) is not None}
        with Path(v['calls']).open('a') as f:f.write(json.dumps({'pid':os.getpid(),'child':child,'goal':self.coordinator.session_state.get('goal'),'modules':modules})+'\n')
        if child:
            Path(v['started']).write_text('ready')
            while not Path(v['release']).exists():await asyncio.sleep(.02)
            return ChatResponse(content=[TextBlock(text='retained-child-result')])
        if not request.tools and str(request.messages[-1].content).startswith('GOAL CONDITION:'):
            return ChatResponse(content=[TextBlock(text='NO\nContinue the authorized fixture.')])
        if not any(m.role=='tool' and m.tool_call_id=='existing-child' for m in request.messages):
            return ChatResponse(content=[],tool_calls=[ToolCall(id='existing-child',name='delegate',arguments={'async':True})])
        return ChatResponse(content=[TextBlock(text='The existing work and child result are retained.')])
class FixtureDelegate:
    name='delegate';description='Run a finite fixture child.';input_schema={'type':'object','properties':{}}
    def __init__(self,coordinator):self.coordinator=coordinator
    async def execute(self,args):
        children=self.coordinator.get_capability('live.children')
        return ToolResult(success=True,output=await children.spawn('self','Finish the existing child work.',children.sessions[self.coordinator.session_id]))
async def mount(coordinator,config=None):
    provider=FixtureProvider(coordinator)
    await coordinator.mount('providers',provider,name='fixture')
    await coordinator.mount('tools',FixtureDelegate(coordinator),name='delegate')
'''

async def main():
    root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=False)
    workspace=root/'workspace';workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(root/'shared'),AMPLIFIER_WEB_HOME=str(root/'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'),AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root/'captures'),
        PYTHONPATH=os.pathsep.join(filter(None,[str(SOURCE),os.environ.get('PYTHONPATH')])) )
    (root/'shared').mkdir();(root/'shared/settings.yaml').write_text('bundle:\n  app: []\n')
    provider=root/'provider/amplifier_module_provider_fixture';provider.mkdir(parents=True)
    scenario=root/'scenario.json'
    (provider/'__init__.py').write_text(PROVIDER+'\nSCENARIO='+repr(str(scenario))+'\n')
    import importlib.util
    from amplifier_web.bundles import SNAPSHOT_VERSION
    context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent.parent
    bundle=root/'bundle.yaml'
    bundle.write_text(f'''bundle:
  name: update-fixture
  version: {SNAPSHOT_VERSION}
session:
  orchestrator:
    module: loop-live
    config:
      use_streaming: false
      background_delegate: true
      min_delay_between_calls_ms: 0
  context:
    module: context-simple
    source: {context}
providers:
  - module: provider-fixture
    source: {provider.parent}
''')
    scenario.write_text(json.dumps({'calls':str(root/'calls.jsonl'),'started':str(root/'child-started'),
                                    'release':str(root/'release-child')}))
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.updates import UpdateManager
    from amplifier_web import update_drain
    def create():
        runtime=RuntimeManager(command=[sys.executable,'-m','amplifier_web.runtime_worker'],startup_timeout=90)
        app=AppService(root/'app',runtime,workspace=workspace);app.management=Management(app)
        runtime.app_bridge=app.app_bridge;runtime.home=root/'app'
        app.update_manager=UpdateManager(app)
        return app,runtime
    app,runtime=create()
    try:
        await app.dispatch('session.create',{'bundle':str(bundle)})
        sid=app._session()['id']
        await app.dispatch('task.create',{'sessionId':sid,'expectedRevision':0,
            'objective':'Finish the update fixture','maxTurns':3},command_id='original-task')
        await app.dispatch('conversation.send',{'sessionId':sid,'text':'Finish the authorized fixture'},command_id='original-input',include_state=False)
        async with asyncio.timeout(45):
            while not (root/'child-started').exists():await asyncio.sleep(.03)
        target={'attemptId':'a'*32,'revision':'b'*40,'generation':'c'*32}
        assert not await update_drain.prepare(app.update_manager,target)
        first=json.loads(json.dumps(app.state['updates']['drain']))
        assert first['workers'][sid]['ready'] is False
        (root/'release-child').write_text('finish')
        async with asyncio.timeout(45):
            while not await update_drain.prepare(app.update_manager,target):await asyncio.sleep(.05)
        saved=json.loads(json.dumps(app.state['updates']['drain']))
        assert saved['workers'][sid]['ready']
        assert saved['workers'][sid]['yieldedGoal']['turns_used']==2,saved
        assert runtime.workers[sid]['parked']
        saved['phase']='replacing';app.state['updates']['drain']=saved;app._publish()
        await app.close()
        app,runtime=create()
        # The exact healthy receipt is owned by update_readiness in production;
        # only that control-plane result is simulated in this process probe.
        app.state['updates'].update(phase='installed',applicationResult={**target})
        await update_drain.finish(app.update_manager,'installed')
        async with asyncio.timeout(45):
            while sid not in runtime.workers or not runtime.workers[sid]['parked']:
                if app._session(sid).get('error'):raise AssertionError(app._session(sid)['error'])
                await asyncio.sleep(.05)
        await update_drain.finish(app.update_manager,'installed')
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_web.session_files import sessions_dir
        history=SessionHistoryStore(sessions_dir(workspace)/sid).load_messages()
        originals=[m for m in history if (m.get('metadata') or {}).get('amplifier_input',{}).get('id')=='original-input']
        assert len(originals)==1
        assert any('retained-child-result' in str(m.get('content')) for m in history)
        calls=[json.loads(line) for line in (root/'calls.jsonl').read_text().splitlines()]
        pids=list(dict.fromkeys(row['pid'] for row in calls));assert len(pids)==2,calls
        resumed=next(row for row in calls if row['pid']==pids[-1] and row['goal'])
        assert resumed['goal']['turns_used']==2 and resumed['goal']['cap']==3,resumed
        continuations=app.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId') LIKE 'update-wait:%'").fetchall()
        assert len(continuations)==1 and json.loads(continuations[0][0])['delivery']=='accepted'
        (root/'runtime-provenance.json').write_text(json.dumps({str(pid):next(row['modules'] for row in calls if row['pid']==pid) for pid in pids},indent=2))
        (root/'drain-receipt.json').write_text(json.dumps(saved,indent=2))
        print(json.dumps({'workerProcesses':2,'originalInputCount':1,'continuations':1,
            'childResultRetained':True,'parkedBeforeRestart':True,'resumedUsedTurns':2,'cap':3,
            'healthyReceipt':'fixture-controlled','provider':'scripted-offline'}))
    finally:await app.close()

if __name__=='__main__':asyncio.run(main())
