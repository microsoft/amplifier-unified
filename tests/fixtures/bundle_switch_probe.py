"""Real host, worker processes, Core/Foundation and saved history; no model calls.

Two local revisions expose the same package and cached child names. Each package
is a real Core context module delegating storage to installed context-simple.
Only the provider is synthetic; it rejects every model call. No network sources,
existing home, credentials or user transcript are used.
"""
from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def saved_history(store, session_id):
    """Inspect one native checkpoint while the idle worker may park/save.

    history.snapshot awaits its own checkpoint, not the independently scheduled
    idle checkpoint. Use the writer's existing cross-process lock for assertions;
    never retry a rejected read or bypass SessionStore's integrity checks.
    """
    from amplifier_foundation.session.metadata import metadata_lock
    with metadata_lock(store.directory(session_id)):
        return store.load(session_id)


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
    # A prepared development environment supplies the real hook locally. Older
    # native lane environments still resolve its normal ordinary-root source.
    modules = {}
    for name, module in [('loop-live', 'amplifier_module_loop_live'),
                         ('hook-context-intelligence', 'amplifier_module_hook_context_intelligence')]:
        spec = importlib.util.find_spec(module)
        if spec and spec.origin:
            modules[name] = str(Path(spec.origin).parent)
    settings.write_text(json.dumps({'bundle': {'app': []}, 'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
                                    'sources': {'modules': modules}}))
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
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.module_failures import ConfiguredModuleError, REMEDIATION
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.bundle_actions import perform
    worker_command = [os.environ['UNIFIED_RUNTIME_PYTHON'], str(ROOT/'amplifier_web/runtime_worker.py')]
    runtime = RuntimeManager(command=worker_command, startup_timeout=45)
    # Discovery also uses this fixture's already-prepared isolated interpreter.
    # Keep the real probe/subprocess; do not fetch a second dependency graph.
    original_command = RuntimeManager._command
    RuntimeManager._command = lambda self, *a, **kw: list(self.command or worker_command)
    app = AppService(home, runtime=runtime, workspace=workspace)
    app.management = Management(app); runtime.app_bridge = app.app_bridge; runtime.home = home
    source = app._new_session({'bundle': bundles['first']}); app.state['sessions'].append(source)
    sid = source['id']; store = SessionStore.for_app(home, workspace)
    history = [{'role':'user','content':'Keep this saved conversation'}, {'role':'assistant','content':'Saved reply'}]
    store.save(sid, history, {'bundle_name': bundles['first']})
    states = home/'sessions'/sid; states.mkdir(parents=True, exist_ok=True)
    states.joinpath('control-state.json').write_text(json.dumps({'selection': {'instance':'fixture','model':'fixture','effort':'high'}, 'budget': {'contextTokens':1234567}}))
    processes, probes = [], []
    spawn = asyncio.create_subprocess_exec
    async def observed_spawn(*args, **kwargs):
        probe = Path(args[-1]).name == 'draft_defaults_probe.py'
        assert probe or Path(args[-1]).name == 'runtime_worker.py', args
        if not probe:
            assert all(process.returncode is not None for process in processes), 'Previous worker not reaped before replacement spawn'
        process = await spawn(*args, **kwargs)
        (probes if probe else processes).append(process)
        return process
    asyncio.create_subprocess_exec = observed_spawn
    try:
        await app.management.ensure_runtime(source)
        await runtime.control(sid, 'history.snapshot', {})
        history = saved_history(store, sid)[0]
        first_pid = runtime.workers[sid]['process'].pid
        mounts_before_preview = (root/'mounts.jsonl').read_bytes()
        await perform(app.management, 'bundle.preview', {'sessionId':sid, 'bundle':bundles['second']})
        assert runtime.workers[sid]['process'].pid == first_pid
        assert len(processes) == 1 and len(probes) == 1
        assert probes[0].returncode == 0
        assert (root/'mounts.jsonl').read_bytes() == mounts_before_preview
        preview = source['bundlePreview']
        await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['second'],'previewId':preview['previewId']})
        second_pid = runtime.workers[sid]['process'].pid
        assert second_pid != first_pid
        saved, metadata = saved_history(store, sid)
        assert saved == history
        assert metadata['bundle_name'] == bundles['second']
        controls = await runtime.control(sid,'configuration.providers',{})
        assert controls['effective']['model']=='fixture' and controls['effective']['effort']=='high'
        assert json.loads(states.joinpath('control-state.json').read_text())['budget']['contextTokens']==2000000
        await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['first']})
        third_pid = runtime.workers[sid]['process'].pid
        assert len({first_pid,second_pid,third_pid})==3
        before = saved_history(store, sid)
        try:
            await perform(app.management, 'bundle.switch', {'sessionId':sid,'bundle':bundles['bad']})
        except ConfiguredModuleError as exc:
            assert exc.failures == [{'module': 'provider-fixture', 'type': 'provider',
                'instance_id': 'fixture', 'reason_code': 'provider_mount_failed',
                'guidance': REMEDIATION['provider_mount_failed'],
                'diagnostic': {'stage': 'mount', 'category': 'unknown',
                               'errorType': 'Error'}}], exc.failures
        else:
            raise AssertionError('Broken replacement was accepted')
        saved, metadata = saved_history(store, sid)
        assert saved == before[0]
        assert metadata['bundle_name'] == bundles['first']
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
            'preview_probe_without_worker_or_module_mount':True,
            'previous_worker_reaped_before_spawn':True,
            'same_name_cached_child':True,'history_preserved':True,'model_effort_preserved':True,
            'old_budget_reset':True,'failed_mount_diagnostic':'provider_mount_failed',
            'failed_mount_rolled_back':True,'restored_worker_usable':True,
            'model_calls':0,'replayed_work':False,'mounts':mounts}))
    except BaseException:
        # Synthetic fixture diagnostics only; retain the underlying setup failure
        # instead of leaving the public startup/preview summary as the sole clue.
        print(json.dumps({'fixtureFailure': {key: source.get(key) for key in
            ('failure', 'error', 'bundleChange')},
            'workerStderr': [line for row in runtime.workers.values() for line in row.get('stderr', [])]}), flush=True)
        raise
    finally:
        await app.close()
        asyncio.create_subprocess_exec = spawn
        RuntimeManager._command = original_command
        assert all(process.returncode is not None for process in processes + probes)


if __name__ == '__main__':
    asyncio.run(main())
