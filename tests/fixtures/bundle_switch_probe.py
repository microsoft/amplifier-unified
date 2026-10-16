"""Real host, worker processes, Core/Foundation and saved history; no model calls.

Two local revisions expose the same package and cached child names. Each package
is a real Core context module delegating storage to installed context-simple.
Only the provider is synthetic; it rejects every model call. No network sources,
existing home, credentials or user transcript are used.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main():
    root = Path(sys.argv[1]).resolve()
    workspace = root/'workspace'; workspace.mkdir(parents=True)
    home = root/'app'
    os.environ.update(AMPLIFIER_HOME=str(root/'shared'), AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'))
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    settings = root/'shared/settings.yaml'; settings.parent.mkdir()
    # Exercise revision isolation using only the local modules defined below.
    # Ordinary defaults have their own installed-runtime acceptance coverage.
    settings.write_text(json.dumps({'bundle': {'app': []}, 'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}}))
    provider = root/'provider/amplifier_module_provider_fixture'; provider.mkdir(parents=True)
    calls = root/'model-calls'
    provider.joinpath('__init__.py').write_text('''from pathlib import Path
from amplifier_core import ProviderInfo
__amplifier_module_type__='provider'
class Provider:
    name='fixture'
    def get_info(self): return ProviderInfo(id='fixture',display_name='Fixture',defaults={'model':'fixture'})
    async def list_models(self): return []
    async def complete(self,request,**kw):
        Path('''+repr(str(calls))+''').touch()
        raise AssertionError('Bundle switching must not call a model')
    def parse_tool_calls(self,response): return []
async def mount(coordinator,config=None):
    if (config or {}).get('fail'): raise ValueError('Intentional candidate mount failure')
    await coordinator.mount('providers',Provider(),name='fixture')
''')
    bundles = {}
    for name, budget in [('first',1000000),('second',2000000),('bad',3000000)]:
        package = root/name/'amplifier_module_context_switchfixture'; package.mkdir(parents=True)
        (package/'_text_estimate.py').write_text('REVISION='+repr(name)+'\n')
        (package/'__init__.py').write_text('''import json, os
from pathlib import Path
from ._text_estimate import REVISION
from amplifier_module_context_simple import mount as simple_mount
__amplifier_module_type__='context'
async def mount(coordinator, config=None):
    result=await simple_mount(coordinator, config)
    with Path('''+repr(str(root/'mounts.jsonl'))+''').open('a') as stream:
        stream.write(json.dumps({'revision':REVISION,'pid':os.getpid(),'origin':__file__})+'\\n')
    return result
''')
        plan = {'bundle': {'name': name}, 'session': {'orchestrator': {'module': 'loop-live'},
            'context': {'module': 'context-switchfixture', 'source': str(package.parent), 'config': {'max_tokens': budget}}},
            'providers': [{'module': 'provider-fixture', 'source': str(provider.parent), 'config': {'fail': name=='bad'}}]}
        bundle = root/(name+'.yaml'); bundle.write_text(json.dumps(plan)); bundles[name] = str(bundle)
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.bundle_actions import perform
    runtime = RuntimeManager(command=[os.environ['UNIFIED_RUNTIME_PYTHON'], str(ROOT/'amplifier_web/runtime_worker.py')], startup_timeout=45)
    app = AppService(home, runtime=runtime, workspace=workspace)
    app.management = Management(app); runtime.app_bridge = app.app_bridge; runtime.home = home
    source = app._new_session({'bundle': bundles['first']}); app.state['sessions'].append(source)
    sid = source['id']; store = SessionStore.for_app(home, workspace)
    history = [{'role':'user','content':'Keep this saved conversation'}, {'role':'assistant','content':'Saved reply'}]
    store.save(sid, history, {'bundle_name': bundles['first']})
    states = home/'sessions'/sid; states.mkdir(parents=True, exist_ok=True)
    states.joinpath('control-state.json').write_text(json.dumps({'selection': {'instance':'fixture','model':'fixture','effort':'high'}, 'budget': {'contextTokens':1234567}}))
    processes = []
    spawn = asyncio.create_subprocess_exec
    async def observed_spawn(*args, **kwargs):
        assert all(process.returncode is not None for process in processes), 'Previous worker not reaped before replacement spawn'
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process
    asyncio.create_subprocess_exec = observed_spawn
    try:
        await app.management.ensure_runtime(source)
        await runtime.control(sid, 'history.snapshot', {})
        history = store.load(sid)[0]
        first_pid = runtime.workers[sid]['process'].pid
        await perform(app.management, 'bundle.preview', {'sessionId':sid, 'bundle':bundles['second']})
        assert runtime.workers[sid]['process'].pid == first_pid
        preview = source['bundlePreview']
        await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['second'],'previewId':preview['previewId']})
        second_pid = runtime.workers[sid]['process'].pid
        assert second_pid != first_pid
        assert store.load(sid)[0] == history
        assert store.load(sid)[1]['bundle_name'] == bundles['second']
        controls = await runtime.control(sid,'configuration.providers',{})
        assert controls['effective']['model']=='fixture' and controls['effective']['effort']=='high'
        assert json.loads(states.joinpath('control-state.json').read_text())['budget']['contextTokens']==2000000
        await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['first']})
        third_pid = runtime.workers[sid]['process'].pid
        assert len({first_pid,second_pid,third_pid})==3
        before = store.load(sid)
        try:
            await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['bad']})
        except RuntimeStartupError:
            pass
        else:
            raise AssertionError('Broken replacement was accepted')
        assert store.load(sid)[0] == before[0]
        assert store.load(sid)[1]['bundle_name'] == bundles['first']
        assert source['bundle']==bundles['first'] and source['bundleChange']['phase']=='error'
        restored_pid = runtime.workers[sid]['process'].pid
        assert restored_pid not in {first_pid,second_pid,third_pid}
        assert not (states/'bundle-transition.json').exists()
        assert not calls.exists()
        mounts = [json.loads(line) for line in (root/'mounts.jsonl').read_text().splitlines()]
        by_pid = {}
        for row in mounts:
            if row['pid'] in by_pid: assert by_pid[row['pid']] == row['revision'], mounts
            by_pid[row['pid']] = row['revision']
        assert list(by_pid.values())==['first','second','first','bad','first'], mounts
        await runtime.control(sid,'configuration.inspect',{})
        print(json.dumps({'reviewed_and_direct_switch':True,'fresh_worker_per_revision':True,
            'previous_worker_reaped_before_spawn':True,
            'same_name_cached_child':True,'history_preserved':True,'model_effort_preserved':True,
            'old_budget_reset':True,'failed_mount_rolled_back':True,'restored_worker_usable':True,
            'model_calls':0,'replayed_work':False,'mounts':mounts}))
    finally:
        await app.close()
        asyncio.create_subprocess_exec = spawn
        assert all(process.returncode is not None for process in processes)


asyncio.run(main())
