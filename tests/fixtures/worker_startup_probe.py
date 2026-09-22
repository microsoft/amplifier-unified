"""Mount and resume the actual worker with its installed dependencies, offline.

Run with the candidate worker Python and a new disposable directory. No user
session is opened and the fixture provider refuses all model requests.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))


async def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / 'workspace'
    workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(root / 'shared'),
                      AMPLIFIER_WEB_HOME=str(root / 'app'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
                      AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
                      AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'captures'))
    shared = root / 'shared'
    shared.mkdir()
    (shared / 'settings.yaml').write_text('bundle:\n  app: []\n')
    provider = root / 'provider' / 'amplifier_module_provider_fixture'
    provider.mkdir(parents=True)
    (provider / '__init__.py').write_text('''from amplifier_core import ProviderInfo
class FixtureProvider:
    name = 'fixture'
    def get_info(self): return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture'})
    async def list_models(self): return []
    async def complete(self, *args, **kwargs): raise AssertionError('No model calls allowed in startup qualification')
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None):
    await coordinator.mount('providers', FixtureProvider(), name='fixture')
''')
    from amplifier_module_context_simple import __file__ as context_module
    bundle = root / 'fixture.yaml'
    bundle.write_text(f'''bundle:
  name: startup-fixture
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
''')
    import amplifier_web.runtime_worker as module
    from amplifier_foundation import BundleRegistry
    registry = BundleRegistry(home=root / 'app' / 'foundation')
    registry.register({'fixture': str(bundle)})
    registry.save()
    registry_path = root / 'app' / 'foundation' / 'registry.json'
    before = registry_path.read_bytes()
    events = []
    module.publish = events.append
    config = {'id': 'startup-fixture', 'workspace': str(workspace), 'bundle': str(bundle)}
    for _ in range(2):
        worker = module.Worker()
        try:
            await worker.start(config, raise_errors=True)
            await asyncio.sleep(0.1)
            assert worker.session is not None
            assert registry_path.read_bytes() == before
        finally:
            # This fixture invokes the worker directly, without a protocol pipe.
            worker.read = worker.shutdown.wait
            worker.shutdown.set()
            await worker.run()
    assert sum(e.get('type') == 'runtime.ready' for e in events) == 2
    assert not any(e.get('type') in {'input.delivered', 'generation.started'} for e in events)
    print(json.dumps({'workerStarts': 2, 'resumed': True, 'modelCalls': 0,
                      'sharedRegistryUnchanged': True, 'productionHistoryTouched': False}))


asyncio.run(main())
