"""Actual installed CLI + Unified canonical runtime adapter, synthetic work only.

Run inside an owned DTU with CLI_PYTHON and CONSUMER_PYTHON pointing at separate
owned environments. CLI uses installed Core/Foundation/loop-live/context-simple;
only the provider and two local tools are scripted. No network/model calls.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main():
    root = Path(sys.argv[1]); root.mkdir(parents=True)
    workspace = root/'workspace'; workspace.mkdir()
    registry = root/'status'
    os.environ.update(AMPLIFIER_STATUS_DIR=str(registry), AMPLIFIER_HOME=str(root/'shared'),
                      AMPLIFIER_WEB_HOME=str(root/'app'), AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'))
    settings = root/'shared/settings.yaml'; settings.parent.mkdir()
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    settings.write_text(json.dumps({'bundle': {'app': []}, 'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}}))
    provider = root/'provider/amplifier_module_provider_statusfixture'; provider.mkdir(parents=True)
    provider.joinpath('__init__.py').write_text('''from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'statusfixture'
    def __init__(self): self.calls = 0
    def get_info(self): return ProviderInfo(id=self.name,display_name='Status fixture',capabilities=['tools'],defaults={'model':'offline'})
    async def list_models(self): return []
    def parse_tool_calls(self,response): return response.tool_calls or []
    async def complete(self,request,**kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='plan-call',name='todo',arguments={'action':'update','todos':[{'content':'PRIVATE plan label','status':'completed'},{'content':'PRIVATE current step','status':'in_progress'}]})],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
        if self.calls == 2:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='hold-call',name='status_hold',arguments={})],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
        return ChatResponse(content=[TextBlock(text='Fixture complete')],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
async def mount(coordinator,config=None):
    await coordinator.mount('providers',Provider(),name='statusfixture')
''')
    tools = root/'tools/amplifier_module_tool_statusfixture'; tools.mkdir(parents=True)
    tools.joinpath('__init__.py').write_text('''import asyncio, json, time, os
from pathlib import Path
from amplifier_core import ToolResult
__amplifier_module_type__ = 'tool'
class Tool:
    description = 'Local status acceptance fixture'
    input_schema = {'type':'object','additionalProperties':True}
    def __init__(self,name): self.name=name
    async def execute(self,arguments):
        if self.name == 'status_hold':
            (Path('''+repr(str(root))+''')/('tool-started-'+str(os.getpid())+'.json')).write_text(json.dumps({'at':time.time()}))
            await asyncio.sleep(6)
            return ToolResult(success=True,output={'done':True})
        return ToolResult(success=True,output={'status':'updated','count':2})
async def mount(coordinator,config=None):
    for name in ['todo','status_hold']:
        await coordinator.mount('tools',Tool(name),name=name)
''')
    installed = await asyncio.create_subprocess_exec(os.environ['CLI_PYTHON'], '-I', '-c',
        'import amplifier_status, pathlib; print(pathlib.Path(amplifier_status.__file__).parent)',
        stdout=asyncio.subprocess.PIPE)
    installed_path, _ = await installed.communicate()
    assert installed.returncode == 0
    bundle = root/'bundle.yaml'
    bundle.write_text(json.dumps({'bundle': {'name': 'status-proof'},
        'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-statusfixture', 'source': str(provider.parent)}],
        'tools': [{'module': 'tool-statusfixture', 'source': str(tools.parent)}],
        'hooks': [{'module': 'hooks-status-emitter', 'source': installed_path.decode().strip(), 'config': {'directory': str(registry), 'bundle': 'status-proof'}}]}))
    from amplifier_web.service import AppService
    from amplifier_web.status_protocol import discover
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.management import Management
    runtime = RuntimeManager(command=[sys.executable, str(ROOT/'amplifier_web/runtime_worker.py')], startup_timeout=60)
    app = AppService(root/'app', runtime=runtime, workspace=workspace)
    app.management = Management(app)
    runtime.app_bridge = app.app_bridge
    runtime.home = root/'app'
    host_bundle = root/'host-bundle.yaml'
    host_plan = json.loads(bundle.read_text()); host_plan['hooks'] = []
    host_bundle.write_text(json.dumps(host_plan))
    session = app._new_session({'title': 'Private status acceptance', 'bundle': str(host_bundle), 'selection': {'instance':'statusfixture','model':'offline'}})
    app.state['sessions'].append(session)
    app.projections.invalidate(state=app._state)
    sid = session['id']
    cli = None
    observed = None
    try:
        await app.management.ensure_runtime(session)
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SSL_CERT_FILE') if key in os.environ}
        env['HOME'] = str(root/'cli-base-home')
        env['UV_OFFLINE'] = 'true'
        env['AMPLIFIER_STATUS_DIR'] = str(registry)
        env['AMPLIFIER_HOME'] = str(root/'cli-home')
        env['AMPLIFIER_SESSION_STATE_HOME'] = str(root/'cli-ownership')
        Path(env['AMPLIFIER_HOME']).mkdir()
        Path(env['AMPLIFIER_HOME'],'settings.yaml').write_text(json.dumps({'bundle': {'app': []}}))
        # Public CLI entry point, installed in an environment without Unified.
        registration = await asyncio.create_subprocess_exec(os.environ['CLI_PYTHON'], '-m', 'amplifier_app_cli.main', 'bundle', 'add', bundle.as_uri(), '--name', 'status-proof', cwd=workspace, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(registration.communicate(), 30)
        assert registration.returncode == 0, (output+errors).decode()
        command = [os.environ['CLI_PYTHON'], '-m', 'amplifier_app_cli.main', 'run', '--bundle', 'status-proof', '--mode', 'single', '--output-format', 'json', 'PRIVATE prompt never emitted']
        with (root/'cli.log').open('wb') as log:
            cli = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=env, stdout=log, stderr=log)
            deadline = time.monotonic()+60
            host_started = False
            while time.monotonic() < deadline:
                if cli.returncode is not None:
                    break
                rows = discover(registry)['sessions']
                running = [row for row in rows if row['producer']['host'] == 'cli' and any(tool['name'] == 'status_hold' for tool in row['now']['tools'])]
                if running and not host_started:
                    await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'PRIVATE host prompt'})
                    host_started = True
                host_running = any(row['producer']['host']=='unified' and any(t['name']=='status_hold' for t in row['now']['tools']) for row in rows)
                if running and host_running:
                    # External installed consumer imports no source or host code.
                    process = await asyncio.create_subprocess_exec(os.environ['CONSUMER_PYTHON'], '-I', '-m', 'amplifier_status.cli', '--directory', str(registry), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                    assert process.returncode == 0, stderr.decode()
                    observed = json.loads(stdout)
                    break
                await asyncio.sleep(.03)
            await asyncio.wait_for(cli.wait(), 60)
        assert cli.returncode == 0, (root/'cli.log').read_text()[-6000:]
        assert observed is not None, (root/'cli.log').read_text()[-6000:]
        assert observed['complete'] and not observed['invalid'], observed
        by_host = {row['producer']['host']: row for row in observed['sessions']}
        assert set(by_host) == {'cli', 'unified'}, observed
        assert by_host['cli']['plan']['counts'] == {'pending': 0, 'in_progress': 1, 'completed': 1}
        assert by_host['unified']['plan']['counts']['in_progress'] == 1
        assert by_host['cli']['now']['status'] == by_host['unified']['now']['status'] == 'tool'
        lag = by_host['cli']['observedAt'] - json.loads((root/('tool-started-'+str(by_host['cli']['producer']['pid'])+'.json')).read_text())['at']
        assert 0 <= lag < 1, lag
        assert 'PRIVATE' not in json.dumps(observed)
        ended = [r for r in discover(registry)['sessions'] if r['producer']['host'] == 'cli']
        assert len(ended) == 1 and ended[0]['freshness'] == 'ended', ended
        result = {'passed': True, 'cliToolObservationSeconds': lag, 'consumer': observed,
                  'cliEnded': True, 'networkModelCalls': 0,
                  'limits': ['CLI is real installed entry point with scripted provider/local tools.',
                             'Unified uses a real worker/Core/Foundation with the same scripted provider and local tools.',
                             'No runtime/TUI/recipe-specific adapter qualification.']}
        (root/'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps({key: value for key, value in result.items() if key != 'consumer'}))
    finally:
        if cli and cli.returncode is None:
            cli.terminate()
            await asyncio.wait_for(cli.wait(), 5)
        await app.close()


if __name__ == '__main__':
    asyncio.run(main())
