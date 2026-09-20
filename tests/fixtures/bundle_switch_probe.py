"""Actual Worker, Foundation and loop-live transitions; local synthetic provider only."""
import asyncio
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

async def main():
    root=Path(sys.argv[1]).resolve();workspace=root/'workspace';workspace.mkdir(parents=True)
    provider=root/'provider/amplifier_module_provider_fixture';provider.mkdir(parents=True)
    provider.joinpath('__init__.py').write_text('''from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class Provider:
    name='fixture'
    def get_info(self): return ProviderInfo(id='fixture',display_name='Fixture',defaults={'model':'fixture'})
    async def list_models(self): return []
    async def complete(self,request,**kw): return ChatResponse(content=[TextBlock(text='Local fixture response')])
    def parse_tool_calls(self,response): return []
async def mount(coordinator,config=None):
    if (config or {}).get('fail'): raise ValueError('Intentional candidate mount failure')
    await coordinator.mount('providers',Provider(),name='fixture')
''')
    from amplifier_module_context_simple import __file__ as context_module
    import yaml
    bundles={}
    for name,budget,fail in [('first',10000,False),('second',20000,False),('bad',30000,True)]:
        plan={'bundle':{'name':name},'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple','source':str(Path(context_module).parent.parent),'config':{'max_tokens':budget}}},'providers':[{'module':'provider-fixture','source':str(provider.parent),'config':{'fail':fail}}]}
        path=root/(name+'.md');path.write_text('---\n'+yaml.safe_dump(plan)+'---\n'+name+' root instructions.\n');bundles[name]=str(path)
    os.environ.update(AMPLIFIER_HOME=str(root/'shared'),AMPLIFIER_WEB_HOME=str(root/'app'),AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'),AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'))
    from amplifier_web.runtime_worker import Worker
    import amplifier_web.runtime_worker as module
    from amplifier_web.host.storage import SessionStore
    events=[];module.publish=events.append;worker=Worker()
    async def control(operation,args=None):
        identity='request-'+str(len(events))
        await worker.command({'op':'control','id':identity,'operation':operation,'arguments':args or {}})
        reply=next(event for event in reversed(events) if event.get('op')=='reply' and event.get('id')==identity)
        if reply.get('error'): raise RuntimeError(reply['error'])
        return reply['result']
    async def settled():
        for _ in range(300):
            if worker.parked:return
            await asyncio.sleep(.02)
        raise AssertionError('worker did not park')
    try:
        await worker.start({'id':'switch-fixture','workspace':str(workspace),'bundle':bundles['first']},raise_errors=True)
        await worker.command({'op':'send','id':'send','input_id':'original-input','text':'Remember the fixture history.'})
        await settled()
        await control('provider.select',{'instance':'fixture','model':'fixture','effort':'high'})
        await control('budget.set',{'contextTokens':12345})
        store=SessionStore.for_app(root/'app',workspace)
        original=store.load('switch-fixture')[0]
        checked=await control('bundle.preview',{'bundle':bundles['second']})
        assert checked['modelCompatible']
        result=await control('bundle.switch',{'bundle':bundles['second'],'previewId':checked['previewId']})
        assert result['providers']['effective']['model']=='fixture'
        assert result['providers']['effective']['effort']=='high'
        assert worker.session.coordinator.get('context').max_tokens==20000
        assert store.load('switch-fixture')[0]==original
        assert store.load('switch-fixture')[1]['bundle_name']==bundles['second']
        assert 'second root instructions' in worker.controls.prepared.bundle.instruction
        assert any(event.get('type')=='runtime.ready' and event['report'].get('root_bundle')==bundles['second'] for event in events)
        before=store.load('switch-fixture')
        checked=await control('bundle.preview',{'bundle':bundles['bad']})
        try:
            await control('bundle.switch',{'bundle':bundles['bad'],'previewId':checked['previewId']})
            raise AssertionError('broken candidate accepted')
        except RuntimeError as exc: assert 'mount' in str(exc).lower() or 'provider' in str(exc).lower()
        assert store.load('switch-fixture')[0]==before[0]
        assert store.load('switch-fixture')[1]['bundle_name']==bundles['second']
        assert worker.session.coordinator.get('context').max_tokens==20000
        assert not worker.shutdown.is_set()
        await worker.command({'op':'send','id':'after','input_id':'after-switch','text':'Continue after the failed switch.'})
        await settled()
        assert sum(event.get('type')=='assistant.message' for event in events)==2
        print(json.dumps({'real_switch':True,'history_preserved':True,'model_effort_preserved':True,'old_budget_reset':True,'failed_mount_rolled_back':True,'continued_after_failure':True,'replayed_work':False}))
    finally:
        worker.shutdown.set();await worker.run()

asyncio.run(main())
