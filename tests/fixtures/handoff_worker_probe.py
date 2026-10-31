"""Actual worker processes, native history and checkout continuation; scripted provider.

Run with the owned test runtime Python and a new private directory. This never
uses provider credentials, user history, or shared services.
Pass --child for real finite child preparation and late return.
Pass --goal to exercise goal continuation/budget, or --goal-cap-one to verify
that a goal exhausting its last turn during handoff is not restarted.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))


async def main():
    child_work = '--child-work' in sys.argv[2:]
    with_child = '--child' in sys.argv[2:] or child_work
    canonical = '--canonical' in sys.argv[2:]
    cap_one = '--goal-cap-one' in sys.argv[2:]
    with_goal = '--goal' in sys.argv[2:] or cap_one
    goal_cap = 1 if cap_one else 3 if with_child else 2
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / 'workspace'
    workspace.mkdir()
    for args in [('init', '-b', 'main'), ('config', 'user.name', 'Fixture'),
                 ('config', 'user.email', 'fixture@example.invalid')]:
        subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
    (workspace / 'original.txt').write_text('preserved original\n')
    subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(workspace), 'commit', '-m', 'fixture'], check=True, capture_output=True)
    os.environ.update(AMPLIFIER_HOME=str(root / 'shared'), AMPLIFIER_WEB_HOME=str(root / 'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'captures'), PYTHONPATH=os.pathsep.join(filter(None, [str(SOURCE), os.environ.get('PYTHONPATH')])))
    (root / 'shared').mkdir()
    (root / 'shared' / 'settings.yaml').write_text('bundle:\n  app: []\n')
    provider = root / 'provider' / 'amplifier_module_provider_fixture'
    provider.mkdir(parents=True)
    scenario = root / 'scenario.json'
    (provider / '__init__.py').write_text('''import asyncio, json, os
from pathlib import Path
from amplifier_core import ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
class FixtureProvider:
    name = 'fixture'
    def __init__(self, coordinator=None): self.coordinator = coordinator
    def get_info(self): return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model':'fixture'})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        values=json.loads(Path(SCENARIO).read_text())
        text='\\n'.join(str(m.content) for m in request.messages)
        with Path(values['calls']).open('a') as stream:
            stream.write(json.dumps({'pid':os.getpid(),'cwd':os.getcwd(),
                'goal':self.coordinator.session_state.get('goal'), 'child':bool(self.coordinator.get_capability('live.child'))})+'\\n')
        if values['withChild'] and self.coordinator.get_capability('live.child'):
            Path(values['childStarted']).write_text('ready')
            registry = self.coordinator.get_capability('live.children')
            if not any(m.role=='tool' and m.tool_call_id=='child-read' for m in request.messages):
                await registry.root.wait_for(lambda event: event['type'] == 'generation.finished', 20)
                if values['childWork']:
                    return ChatResponse(content=[], tool_calls=[ToolCall(id='child-read', name='fixture_read', arguments={})])
            return ChatResponse(content=[TextBlock(text='child-result-retained')])
        if not request.tools and str(request.messages[-1].content).startswith('GOAL CONDITION:'):
            return ChatResponse(content=[TextBlock(text='NO\\nThe fixture is still checking the saved turn budget.')])
        if 'Host-attributed peer task input.' in text and 'checkout move completed' in text:
            assert os.getcwd()==values['target'], (os.getcwd(), values['target'])
            assert 'original-request' in text
            return ChatResponse(content=[TextBlock(text='continued-in-requested-checkout')])
        if any(m.role=='tool' and m.tool_call_id=='move-tool' for m in request.messages):
            result=next(m for m in request.messages if m.role=='tool' and m.tool_call_id=='move-tool')
            assert 'pending' in str(result.content), result
            return ChatResponse(content=[TextBlock(text='move-pending-turn-finished')])
        if values['withChild']:
            if not any(m.role=='tool' and m.tool_call_id=='child-tool' for m in request.messages):
                return ChatResponse(content=[], tool_calls=[ToolCall(id='child-tool', name='delegate', arguments={'async':True})])
            async with asyncio.timeout(20):
                while not Path(values['childStarted']).exists():
                    await asyncio.sleep(.01)
        return ChatResponse(content=[], tool_calls=[ToolCall(id='move-tool', name='app_control',
            arguments={'operation':'dispatch','args':{'action':'worktree.handoff','id':'native-move',
                'args':{'sessionId':values['sid'],'id':values['checkout'],'expectedExecutionRevision':0}}})])
class FixtureDelegate:
    name = 'delegate'
    description = 'A finite native child whose result arrives after the originating turn.'
    input_schema = {'type':'object','properties':{}}
    def __init__(self, coordinator=None): self.coordinator = coordinator
    async def execute(self, arguments):
        registry = self.coordinator.get_capability('live.children')
        result = await registry.spawn('self', 'Finish this admitted child.', registry.sessions[self.coordinator.session_id])
        return ToolResult(success=True, output=result)
class FixtureRead:
    name = 'fixture_read'
    description = 'Read the owned fixture after the parent turn finishes.'
    input_schema = {'type':'object','properties':{}}
    async def execute(self, arguments):
        values=json.loads(Path(SCENARIO).read_text())
        result = Path('original.txt').read_text()
        Path(values['childRead']).write_text(result)
        return ToolResult(success=True, output=result)
async def mount(coordinator, config=None):
    await coordinator.mount('providers',FixtureProvider(coordinator),name='fixture')
    await coordinator.mount('tools',FixtureDelegate(coordinator),name='delegate')
    await coordinator.mount('tools',FixtureRead(),name='fixture_read')
'''.replace('SCENARIO', repr(str(scenario))))
    from amplifier_module_context_simple import __file__ as context_module
    from amplifier_web.bundles import SNAPSHOT_VERSION
    bundle = root / 'fixture.yaml'
    bundle.write_text(f'''bundle:
  name: handoff-fixture
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
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
''')
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService
    runtime = RuntimeManager(command=[sys.executable, '-m', 'amplifier_web.runtime_worker'], startup_timeout=90)
    app = AppService(root / 'app', runtime, workspace=workspace)
    from amplifier_web.management import Management
    app.management = Management(app)
    runtime.app_bridge = app.app_bridge
    runtime.home = root / 'app'
    try:
        await app.dispatch('session.create', {'bundle': str(bundle)})
        session = app._session()
        sid = session['id']
        if canonical:
            import uuid
            session['runtimeSessionId'] = str(uuid.uuid4())
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        checkout = (await app.dispatch('worktree.create', {'sessionId': sid,
            'sourceRevision': inspected['repository']['sourceRevision']}, command_id='native-checkout'))['result']
        scenario.write_text(json.dumps({'sid': sid, 'checkout': checkout['id'], 'target': checkout['path'],
                                      'calls': str(root / 'calls.jsonl'), 'withChild': with_child, 'childStarted': str(root / 'child-started'),
                                      'childWork': child_work, 'childRead': str(root / 'child-read')}))
        if with_goal:
            await app.dispatch('task.create', {'sessionId': sid, 'expectedRevision': 0,
                'objective': 'Complete the native checkout handoff fixture.', 'maxTurns': goal_cap}, command_id='native-task')
        await app.dispatch('view.update', {'patch': {'draft': 'preserve-unsent-draft'}})
        await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'original-request: Move and continue this work.',
            'preserveDraft': True}, command_id='native-original', include_state=False)
        async with asyncio.timeout(90):
            while not any('continued-in-requested-checkout' in m.get('text', '') for m in session['messages']):
                if session.get('error'):
                    raise AssertionError(session['error'])
                if cap_one and any(r['phase'] == 'applied' for r in app.worktrees.receipts(sid)):
                    break
                await asyncio.sleep(.05)
        await asyncio.gather(*list(app.worktrees.jobs))
        record = app.worktrees.receipts(sid)[0]
        assert record['phase'] == 'applied', record
        assert record['release']['quiesced'] and not record['inputsReplayed']
        assert record['origin'] == 'agent' and record['sourceInputIds'] == ['native-original']
        if not cap_one:
            original = app.collaboration.receipt(record['continuation']['requestId'])
            assert original['origin'] == 'agent' and original['sourceInputIds'] == ['native-original']
            assert original['sourceGenerationId'] == record['sourceGenerationId']
        assert session['workspace'] == str(workspace) and session['workingDirectory'] == checkout['path']
        assert app.state['view']['draft'] == 'preserve-unsent-draft'
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_web.session_files import sessions_dir
        async with asyncio.timeout(10):
            while sid in runtime.workers and not runtime.workers[sid].get('parked'):
                await asyncio.sleep(.05)
        messages = SessionHistoryStore(sessions_dir(workspace) / (session.get('runtimeSessionId') or sid)).load_messages()
        originals = [m for m in messages if (m.get('metadata') or {}).get('amplifier_input', {}).get('id') == 'native-original']
        assert len(originals) == 1, messages
        assert any(m.get('tool_call_id') == 'move-tool' for m in messages), messages
        if with_child:
            assert record['finishingTurn']['nativeDrain']['jobs'], record
            assert any('child-result-retained' in str(m.get('content')) for m in messages), messages
        calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
        if child_work:
            assert (root / 'child-read').read_text() == 'preserved original\n'
            assert len([row for row in calls if row['child']]) == 2, calls
        process_count = 1 if cap_one else 2
        assert len({r['pid'] for r in calls}) == process_count, calls
        if not cap_one:
            assert calls[-1]['cwd'] == checkout['path']
        if with_goal and not cap_one:
            resumed = next(row for row in calls if row['cwd'] == checkout['path'])['goal']
            assert resumed['cap'] == goal_cap and resumed['turns_used'] == (2 if with_child else 1), resumed
            assert max(row['goal']['turns_used'] for row in calls if row['goal']) == goal_cap, calls
        if cap_one:
            assert record['continuation']['status'] == 'suppressed', record
            assert max(row['goal']['turns_used'] for row in calls if row['goal']) == 1, calls
            assert not any('continued-in-requested-checkout' in m.get('text', '') for m in session['messages'])
        print(json.dumps({'phase': record['phase'], 'workerProcesses': process_count, 'originalInputs': 1,
            'toolResultPersisted': True, 'continuationAccepted': not cap_one, 'draftPreserved': True,
            'historyHomePreserved': True, 'goalBudgetPreserved': with_goal,
            'childResultRetained': with_child, 'childToolAndSecondModelAfterParent': child_work, 'canonicalAlias': canonical, 'provider': 'scripted-offline'}))
    finally:
        await app.close()


asyncio.run(main())
