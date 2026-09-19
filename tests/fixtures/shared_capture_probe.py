"""Real Core/Foundation/CI-hook seam. Synthetic data; no models or network calls."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

repo = Path(__file__).resolve().parents[2]
ci = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(repo), str(ci)]

from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_foundation.session.store import load_transcript
from amplifier_foundation.session.shared_state import SharedSessionStore
from context_intelligence import CaptureLocator, read_native_transcript
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_files import capture_dir, project_slug
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage


class Provider:
    name = 'fixture'
    def get_info(self):
        return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture-model'})
    def parse_tool_calls(self, response):
        return []
    async def complete(self, request, **kwargs):
        return ChatResponse(content=[TextBlock(text='Fixture complete')], usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12))


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve()
        workspace = home / 'workspace.v2_test'
        workspace.mkdir()
        os.environ['AMPLIFIER_HOME'] = str(home / 'amplifier')
        os.environ['AMPLIFIER_SESSION_STATE_HOME'] = str(home / 'shared')
        os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'] = str(home / 'amplifier/projects')
        paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
                 for name in ('loop-live', 'context-simple')}
        paths['hook-context-intelligence'] = ci / 'modules/hook-context-intelligence/amplifier_module_hook_context_intelligence'
        sys.path.insert(0, str(paths['hook-context-intelligence'].parent))
        plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}}, 'context': {'module': 'context-simple'}},
                'project_slug': project_slug(workspace),
                'hooks': [{'module': 'hook-context-intelligence', 'config': {
                    'destinations': {}, 'base_path': str(home / 'amplifier/projects')}}]}
        bundle = Bundle(name='capture-fixture', session=plan['session'], hooks=plan['hooks'])
        prepared = PreparedBundle(plan, BundleModuleResolver(paths), bundle)
        held = SharedSessionStore(workspace, 'fixture-root').acquire(app='storage-probe')
        session = await prepared.create_session(session_id='fixture-root', session_cwd=workspace)
        owner = None
        try:
            assert session.coordinator.get_capability('context_intelligence.hook_config_resolver')
            from amplifier_web.host.prompt_events import install
            install(session.coordinator)
            await session.coordinator.mount('providers', Provider(), name='fixture')
            runtime = Runtime('fixture-root')
            session.coordinator.register_capability('live.runtime', runtime)
            owner = asyncio.create_task(session.execute(''))
            await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=8)
            for n in range(2):
                await runtime.submit(Input('user', 'Hello from the shared storage probe', id=str(n)))
                await runtime.wait_for(lambda e: e['type'] == 'generation.finished' and str(n) in e.get('input_ids', []), timeout=8)
            await runtime.submit(Input('stop'))
            await asyncio.wait_for(owner, 5)
            rows = await session.coordinator.get('context').get_messages()
            held.write(rows, bundle='capture-fixture', metadata={'working_dir': str(workspace)})
            store = SessionStore.for_app(home / 'app', workspace)
            store.save('fixture-root', rows, {'working_dir': str(workspace)})
            assert load_transcript(store.directory('fixture-root')) == rows
            page = read_native_transcript(CaptureLocator.from_session_dir(capture_dir(workspace, 'fixture-root')))
            assert [m.content for m in page.messages] == ['Hello from the shared storage probe', 'Fixture complete'] * 2, page.as_dict()
            events = [json.loads(line) for line in (capture_dir(workspace, 'fixture-root') / 'events.jsonl').read_text().splitlines()]
            assert sum(e['event'] == 'prompt:complete' for e in events) == 2
            assert sum(e['event'] == 'prompt:submit' for e in events) == 2
            assert not (home / 'app/sessions/fixture-root/checkpoint.json').exists()
            print(json.dumps({'passed': True, 'realHook': True, 'realCore': True, 'realLoopLive': True, 'nativeMessages': len(page.messages), 'events': len(events), 'privateCheckpoint': False}))
        finally:
            if owner and not owner.done():
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
            await session.cleanup()
            held.release()


asyncio.run(run())
