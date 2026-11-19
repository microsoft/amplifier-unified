"""Real Rust core + public Foundation lifecycle probe, no model/network calls."""
import asyncio
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
foundation = Path(os.environ.get("WARM_FOUNDATION_PATH", repo.parent / "repos/amplifier-foundation"))
sys.path.insert(0, str(foundation / "modules/tool-delegate"))
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Runtime
from amplifier_web.host.children import (
    install_children, child_plan, StandaloneHostAdapter, Children, PersistentDelegate,
    _PERSISTENT, _PREFERENCES_SOURCE, _CALL_ROLE,
)
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

async def check_delegate_context_cleanup():
    """Exercise the public wrapper with deterministic success/failure boundaries."""
    from amplifier_core import ToolResult
    pin = [{'provider': 'fixture', 'model': 'pinned-model'}]
    coordinator = SimpleNamespace(
        session_id='wrapper-parent', config={'agents': {'pinned': {'provider_preferences': pin}}},
        get_capability=lambda name: name == 'web.default_routing_policy')
    registry = Children(None, SimpleNamespace(load=lambda _: ([], {
        'parent_id': coordinator.session_id, 'preferences_source': 'explicit',
        'provider_preferences': pin})), None)
    seen = []
    class Delegate:
        provider_selection_enabled = True
        input_schema = {'properties': {}}
        async def execute(self, supplied):
            seen.append((supplied, _PERSISTENT.get(), _PREFERENCES_SOURCE.get(), _CALL_ROLE.get()))
            # Force a suspension inside the wrapper's context.
            await asyncio.sleep(0)
            if supplied['instruction'] == 'cancel':
                raise asyncio.CancelledError
            if supplied['instruction'] == 'error':
                raise RuntimeError('fixture delegate failure')
            if supplied['instruction'] == 'resource':
                raise OSError('fixture resource unavailable')
            if supplied['instruction'] == 'failed-result':
                return ToolResult(success=False, error={'message': 'fixture rejection'})
            return ToolResult(success=True, output='returned')
    wrapper = PersistentDelegate(Delegate(), registry, coordinator)
    variables = (_PERSISTENT, _PREFERENCES_SOURCE, _CALL_ROLE)
    outer = (True, 'outer-source', ('outer-coordinator', 'outer-role'))
    tokens = [variable.set(value) for variable, value in zip(variables, outer)]
    try:
        for outcome, error in (('returned', None), ('cancel', asyncio.CancelledError),
                               ('error', RuntimeError), ('resource', OSError),
                               ('failed-result', None)):
            arguments = {'agent': 'pinned', 'instruction': outcome, 'persistent': True, 'model_role': 'coding'}
            try:
                result = await wrapper.execute(arguments)
            except BaseException as exc:
                assert error is not None and isinstance(exc, error), exc
            else:
                assert error is None
                assert result.success == (outcome != 'failed-result')
            assert tuple(variable.get() for variable in variables) == outer
            assert seen[-1][0] == {'agent': 'pinned', 'instruction': outcome,
                                  'model_role': 'coding', 'provider_preferences': pin}
            assert seen[-1][1:] == (True, 'explicit', (coordinator, 'coding'))
            assert arguments['persistent'] is True and 'provider_preferences' not in arguments
        # The default stays finite, and resume preserves the saved explicit pin.
        await wrapper.execute({'session_id': 'saved-child', 'instruction': 'returned', 'model_role': 'coding'})
        assert seen[-1][0]['provider_preferences'] == pin
        assert seen[-1][1:] == (False, 'explicit', (coordinator, 'coding'))
        await wrapper.execute({'agent': 'self', 'instruction': 'returned', 'model_role': 'fast', 'persistent': False})
        assert seen[-1][1:] == (False, 'role', (coordinator, 'fast'))
        before = len(seen)
        invalid = await wrapper.execute({'persistent': 'true'})
        assert not invalid.success and len(seen) == before
        assert tuple(variable.get() for variable in variables) == outer
    finally:
        for variable, token in reversed(list(zip(variables, tokens))):
            variable.reset(token)

async def check_five_workers_and_parent_stop(parent, registry, wrapper):
    """Real Core/Foundation children; real Live owner and Worker Stop, no inference."""
    from amplifier_module_loop_live.host import HostAdapter
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_web.runtime_worker import Worker
    root_runtime = Runtime(parent.session_id)
    ready = asyncio.Event()
    tasks = []
    admission_errors = []
    initial = set(registry.rows)
    def active_rows():
        return [row for identity, row in registry.rows.items() if identity not in initial]
    async def wait_idle_count(count):
        for _ in range(400):
            rows = active_rows()
            if len(rows) == count and all(row['status'] == 'idle' for row in rows):
                return
            if any(task.done() for task in tasks):
                admission_errors.append(f'Only {len(rows)} persistent children admitted: '
                                        f'{[task.result() for task in tasks if task.done()]}')
                raise AssertionError(admission_errors[-1])
            await asyncio.sleep(.005)
        raise AssertionError(f'{count} concurrent persistent children did not become idle')
    class OwnerHost(HostAdapter):
        async def prepare_execution(self, loop, coordinator, providers):
            return root_runtime, providers, None
    class OwnerLoop(BundleLiveOrchestrator):
        def _select_provider(self, providers):
            return SimpleNamespace(native_bundle_live=False)
        async def _execute_guarded_goal(self, *args, **kwargs):
            for index in range(4):
                tasks.append(asyncio.create_task(wrapper.execute({
                    'agent': 'worker', 'instruction': f'overlapping-{index}', 'persistent': True})))
            await wait_idle_count(4)
            assert all(not task.done() for task in tasks)
            tasks.append(asyncio.create_task(wrapper.execute({
                'agent': 'worker', 'instruction': 'overlapping-4', 'persistent': True})))
            await wait_idle_count(5)
            assert all(not task.done() for task in tasks), 'All five execute calls must overlap'
            assert all(row['sessionId'] in registry.sessions and row.get('runtime') for row in active_rows())
            ready.set()
            return await asyncio.gather(*tasks)
    # Keep tool jobs out of this test: it targets concurrent execute admission,
    # not Live's independently configurable background-job admission.
    await parent.coordinator.mount('orchestrator', OwnerLoop({}))
    parent.coordinator.register_capability('live.host', OwnerHost())
    owner = asyncio.create_task(parent.execute('start five eligible persistent executes'))
    worker = Worker()
    worker.session, worker.runtime = parent, root_runtime
    try:
        ready_wait = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait({owner, ready_wait}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if owner in done:
                try:
                    await owner
                except Exception:
                    if admission_errors:
                        raise AssertionError(admission_errors[-1])
                    raise
            assert ready_wait in done and ready.is_set(), f'Fifth admission failed; owner done={owner.done()}'
        finally:
            ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)
        await worker.command({'op': 'stop'})
        await asyncio.wait_for(owner, 5)
        # Core's execute awaiter can settle before the cancelled Python child
        # awaiters finish their checkpoint/cleanup. Observe that settlement too.
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        assert worker.stop_revision == 1 and root_runtime.stop_requested and root_runtime.closed
        assert len(active_rows()) == 5 and all(task.cancelled() for task in tasks)
        for row in active_rows():
            assert row['status'] == 'cancelled'
            assert not any(key in row for key in ('task', 'runtime', '_owner'))
            assert registry.store.load(row['sessionId'])[1]['status'] == 'cancelled'
        assert set(registry.sessions) == {parent.session_id} and not registry.admissions
        assert not _PERSISTENT.get() and _PREFERENCES_SOURCE.get() is None and _CALL_ROLE.get() is None
    finally:
        for task in [owner, *tasks]:
            if not task.done():
                task.cancel()
        await asyncio.gather(owner, *tasks, return_exceptions=True)

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
    parent.coordinator.register_capability('web.activity.install',lambda coordinator: activities.append(
        (coordinator.session_id, coordinator.get_capability('web.worker_run'))))
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
        assert activities==[('child-session', registry.rows['child-session']['runId'])]
        resumed=await registry.resume('child-session','follow-up',parent)
        assert resumed['session_id']=='child-session'
        assert created[-1]['resumed'] is True
        assert activities[-1]==('child-session', registry.rows['child-session']['runId'])
        assert activities[-1][1]!=activities[0][1]
        assert len(store.load('child-session')[0])==5
        assert registry.snapshot()[0]['status']=='completed'
        assert all('task' not in row and 'runtime' not in row for row in registry.snapshot())
        # Admission after real Core initialization must not checkpoint the
        # empty context over a saved child when its provider cannot be restored.
        failed_id = 'unavailable-selection-child'
        saved_rows, saved_metadata = store.load('child-session')
        store.save(failed_id, saved_rows, {**saved_metadata,
            'effective_selection': {'instance': 'missing-account', 'model': 'saved-model'}})
        original_files = {name: (store.directory(failed_id) / name).read_bytes()
                          for name in ('transcript.jsonl', 'metadata.json')}
        executions = len(decisions)
        try:
            await registry.resume(failed_id, 'must not execute', parent)
        except ValueError as exc:
            assert 'inherited provider instance is unavailable' in str(exc), exc
        else:
            raise AssertionError('Unavailable inherited account was admitted')
        assert created[-1]['id'] == failed_id and created[-1]['resumed'] is True
        assert len(decisions) == executions, 'Rejected resume must not execute a turn'
        assert registry.rows[failed_id]['status'] == 'error'
        assert {name: (store.directory(failed_id) / name).read_bytes() for name in original_files} == original_files
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
        first_report=row['reportId']
        run_id=row['runId']
        accepted=await registry.control(row['sessionId'],'message','explicit follow-up',input_id='followup-receipt')
        assert accepted['inputId']=='followup-receipt' and accepted['completed'] is False
        for _ in range(200):
            if row['report']=='Report: explicit follow-up':break
            await asyncio.sleep(.005)
        assert row['report']=='Report: explicit follow-up'
        assert row['reportId']!=first_report and row['runId']==run_id
        await registry.control(row['sessionId'],'steer','new direction')
        for _ in range(200):
            if row['report']=='Report: new direction':break
            await asyncio.sleep(.005)
        assert row['report']=='Report: new direction'
        await registry.control(row['sessionId'],'finish')
        persistent_result=await asyncio.wait_for(task,2)
        assert persistent_result.success
        assert row['reports']==3, 'Finishing must not redeliver the last report'
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
        # Input expansion also applies to actual finite, resumed and persistent
        # child sessions, using their workspace rather than this process cwd.
        parent.coordinator.register_capability('session.working_dir', tmp)
        note = Path(tmp) / 'note.md'
        note.write_text('CHILD-MENTION-CONTEXT')
        mentioned = await registry.spawn('worker', 'Read @note.md', parent, sub_session_id='mention-child')
        assert 'CHILD-MENTION-CONTEXT' in mentioned['output']
        note.write_text('REVISED-CHILD-CONTEXT')
        resumed_mention = await registry.resume('mention-child', 'Read @note.md', parent)
        assert 'REVISED-CHILD-CONTEXT' in resumed_mention['output']
        assert 'CHILD-MENTION-CONTEXT' not in resumed_mention['output']
        mentioned_task = asyncio.create_task(wrapper.execute({'agent': 'worker', 'instruction': 'Read @note.md', 'persistent': True}))
        mentioned_row = await wait_idle()
        assert 'REVISED-CHILD-CONTEXT' in mentioned_row['report']
        await registry.control(mentioned_row['sessionId'], 'finish')
        assert (await asyncio.wait_for(mentioned_task, 2)).success
        await check_five_workers_and_parent_stop(parent, registry, wrapper)
        await check_delegate_context_cleanup()
    await parent.cleanup()
    assert not any(name.startswith(('amplifier_app_cli','amplifier_loop_live_cli')) for name in sys.modules)
    print(json.dumps({'real_child_lineage':True,'checkpoint_resume':True,'failed_resume_preserved':True,'approval_denied':True,'delegate_compatible':True,'persistent_steering':True,'provider_instances':True,'runtime_mentions':True,'five_concurrent_persistent':True,'parent_stop_cancelled_five':True,'delegate_context_cleanup':True,'cli_imports':False}))

if __name__=='__main__':
    asyncio.run(run())
