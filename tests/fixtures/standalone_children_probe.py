"""Real Rust core + public Foundation lifecycle probe, no model/network calls."""
import asyncio
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
foundation = Path(os.environ.get("WARM_FOUNDATION_PATH", repo.parent / "repos/amplifier-foundation"))
sys.path.insert(0, str(foundation / "modules/tool-delegate"))
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Runtime
from amplifier_web.host.children import install_children, child_plan, StandaloneHostAdapter
from amplifier_web.host.storage import SessionStore
from amplifier_web.host.approvals import Approvals

created = []
decisions = []
activities = []

class FiniteOrchestrator:
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        if coordinator.get_capability('live.child_mode') == 'persistent':
            runtime=coordinator.get_capability('live.runtime')
            await runtime.emit('session.ready',steering='request_boundary')
            last=''
            while True:
                kind,command=await runtime.inbox.get()
                runtime.queued_inputs-=1
                if command.kind=='stop':
                    await runtime.emit('session.closed',status='cancelled' if command.target=='cancel' else 'completed')
                    runtime.closed=True
                    return last
                await context.add_message({'role':'user','content':command.text})
                last='Report: '+command.text
                await context.add_message({'role':'assistant','content':last})
                await runtime.emit('session.idle',text=last)
        assert coordinator.get_capability('model_role_resolver') == 'shared-model-resolver'
        decisions.append(await coordinator.approval_system.request_approval('apply fixture change?', ['allow','deny'], 1, 'deny'))
        await context.add_message({'role':'user','content':prompt})
        result = 'Finished **' + prompt + '**'
        await context.add_message({'role':'assistant','content':result})
        await hooks.emit('orchestrator:complete',{'status':'success','turn_count':1,'metadata':{'fixture':True}})
        return result

@dataclass
class ProbePrepared(PreparedBundle):
    async def create_session(self, **kwargs):
        session = await super().create_session(**kwargs)
        created.append({'id':session.session_id,'parent':kwargs.get('parent_id'), 'resumed':kwargs.get('is_resumed'), 'session':session})
        await session.coordinator.mount('orchestrator',FiniteOrchestrator())
        return session

async def run():
    async def ask(prompt, choices):
        return 'deny'
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'agents':{'worker':{'instruction':'You are the worker.'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent for name in ('loop-live','context-simple')}
    bundle=Bundle(name='fixture',session=plan['session'],agents=plan['agents'])
    prepared=ProbePrepared(plan,BundleModuleResolver(paths),bundle)
    approvals=Approvals(None,ask)
    parent=await prepared.create_session(session_id='parent-session',approval_system=approvals)
    parent.coordinator.register_capability('model_role_resolver','shared-model-resolver')
    parent.coordinator.register_capability('web.activity.install',lambda coordinator: activities.append(coordinator.session_id))
    with tempfile.TemporaryDirectory() as tmp:
        store=SessionStore(Path(tmp)/'sessions')
        runtime=Runtime('parent-session')
        registry=await install_children(parent,prepared,runtime,store,approvals)
        # Recipes explicitly pass False even for ordinary in-process steps.
        result=await registry.spawn(agent_name='worker',instruction='first task',parent_session=parent,agent_configs=plan['agents'],sub_session_id='child-session',parent_messages=[{'role':'user','content':'Inherited context'}],use_subprocess=False)
        assert result['session_id']=='child-session'
        assert result['output']=='Finished **first task**'
        assert created[-1]['id']=='child-session' and created[-1]['parent']=='parent-session'
        rows,metadata=store.load('child-session')
        assert metadata['parent_id']=='parent-session'
        assert rows[0]['content']=='Inherited context'
        assert rows[-1]['content']=='Finished **first task**'
        queued = []
        while not runtime.inbox.empty():
            queued.append(runtime.inbox.get_nowait())
        assert not any(kind == 'child_event' and event.get('event') == 'session.closed' for kind, event in queued), 'Finite result must not trigger duplicate manager turns'
        assert any(kind == 'child_event' and event.get('status') == 'completed' for kind, event in queued)
        assert decisions==['deny']
        assert activities==['child-session']
        resumed=await registry.resume('child-session','follow-up',parent)
        assert resumed['session_id']=='child-session'
        assert created[-1]['resumed'] is True
        assert len(store.load('child-session')[0])==5
        assert registry.snapshot()[0]['status']=='completed'
        assert all('task' not in row and 'runtime' not in row for row in registry.snapshot())
        # The actual community tool-delegate calls the app's exact spawn contract.
        from amplifier_module_tool_delegate import DelegateTool
        delegate=DelegateTool(parent.coordinator,{'features':{'self_delegation':{'enabled':True}}})
        await parent.coordinator.mount('tools',delegate,name='delegate')
        await registry.install(parent,prepared)
        wrapper=parent.coordinator.get('tools')['delegate']
        assert 'persistent' in wrapper.input_schema['properties']
        tool_result=await wrapper.execute({'agent':'worker','instruction':'through community delegate'})
        assert tool_result.success, tool_result.error
        assert any('through community delegate' in row.get('report','') for row in registry.snapshot())
        # Verify persistent ownership/steering on actual child sessions and Runtime.
        task=asyncio.create_task(wrapper.execute({'agent':'worker','instruction':'stay available','persistent':True}))
        async def wait_idle():
            for _ in range(200):
                found=next((row for row in registry.rows.values() if row['persistent'] and row['status']=='idle'),None)
                if found:return found
                await asyncio.sleep(.005)
            raise AssertionError('Persistent worker did not report idle')
        row=await wait_idle()
        await registry.control(row['sessionId'],'steer','new direction')
        for _ in range(200):
            if row['report']=='Report: new direction':break
            await asyncio.sleep(.005)
        assert row['report']=='Report: new direction'
        await registry.control(row['sessionId'],'finish')
        persistent_result=await asyncio.wait_for(task,2)
        assert persistent_result.success
        assert registry.rows[row['sessionId']]['status']=='completed'
        finite_session=next(item['session'] for item in created if item['id']=='child-session')
        finite_runtime,providers,scope=await StandaloneHostAdapter(registry).prepare_execution(None,finite_session.coordinator,{'test':'provider'})
        assert finite_runtime is None and scope=='child-session'
        composed=child_plan({'session':{},'providers':[{'module':'provider-test','instance_id':'one','config':{'model':'a'}},{'module':'provider-test','instance_id':'two','config':{'model':'b'}}],'hooks':[{'module':'hooks-approval'},{'module':'hooks-ui'}]}, {'providers':[{'module':'provider-test','id':'two','config':{'model':'selected'}}]},hook_inheritance={'exclude_hooks':['hooks-approval','hooks-ui']})
        assert [r['instance_id'] for r in composed['providers']]==['one','two']
        assert composed['providers'][1]['config']['model']=='selected'
        assert [h['module'] for h in composed['hooks']]==['hooks-approval']
        count = len(created)
        for options, message in (
            ({'use_subprocess': True}, 'Subprocess child sessions are not supported'),
            ({'use_subprocess': 'false'}, 'use_subprocess must be a boolean'),
            ({'unexpected_option': False}, 'Unsupported child session options: unexpected_option'),
            ({'agent_configs': {'worker': {'spawn_mode': 'subprocess'}}}, 'Subprocess child sessions are not supported'),
        ):
            try:
                await registry.spawn('worker', 'must not execute', parent, **options)
            except ValueError as exc:
                assert message in str(exc), exc
            else:
                raise AssertionError('Unsupported child options were silently ignored')
        parent.coordinator.config['spawn_mode'] = 'subprocess'
        try:
            try:
                await registry.spawn('worker', 'must not execute', parent)
            except ValueError as exc:
                assert 'Subprocess child sessions are not supported' in str(exc), exc
            else:
                raise AssertionError('Inherited subprocess mode was silently ignored')
        finally:
            parent.coordinator.config.pop('spawn_mode')
        assert len(created) == count
    await parent.cleanup()
    assert not any(name.startswith(('amplifier_app_cli','amplifier_loop_live_cli')) for name in sys.modules)
    print(json.dumps({'real_child_lineage':True,'checkpoint_resume':True,'approval_denied':True,'delegate_compatible':True,'persistent_steering':True,'provider_instances':True,'cli_imports':False}))

if __name__=='__main__':
    asyncio.run(run())
