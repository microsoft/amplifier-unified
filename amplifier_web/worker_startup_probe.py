"""Mount and resume the actual worker with its installed dependencies, offline.

Run with the candidate worker Python and a new disposable directory. No user
session is opened and the fixture provider refuses all model requests.
The default context is context-simple; --context context-managed selects that
module from the candidate interpreter's prepared sources instead.
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--context', choices=('context-simple', 'context-managed'),
                        default='context-simple')
    parser.add_argument('--installed-package', type=Path, metavar='AMPLIFIER_WEB',
                        help='Load only the application packages from this installed wheel.')
    parser.add_argument('--tool-registration', choices=('retained', 'native'),
                        help='Start and reopen with a tool using the earlier direct-registration contract.')
    args = parser.parse_args()
    if args.installed_package:
        package = args.installed_package.resolve(strict=True)
        spec = importlib.util.spec_from_file_location('installed_bootstrap', package / 'runtime_bootstrap.py')
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        if Path(loader.bootstrap_app_package().__file__).resolve().parent != package:
            raise RuntimeError('The selected application package was not loaded')
    else:
        sys.path.insert(0, str(SOURCE))
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    if args.tool_registration:
        # Run only the installed loop/context and local fixture modules. Module
        # discovery and source helpers must not inherit a real user's accounts.
        scratch_home = root / 'home'
        scratch_home.mkdir()
        os.environ.clear()
        os.environ.update(HOME=str(scratch_home), PATH=os.defpath,
                          TMPDIR=str(scratch_home), UV_OFFLINE='1', GIT_ALLOW_PROTOCOL='file',
                          GIT_CONFIG_NOSYSTEM='1', AMPLIFIER_RUNTIME_IMMUTABLE='1',
                          PYTHONDONTWRITEBYTECODE='1')
        def deny_network(event, arguments):
            if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
                raise RuntimeError("Network access is disabled during startup qualification")
        sys.addaudithook(deny_network)
    workspace = root / 'workspace'
    workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(root / 'shared'),
                      AMPLIFIER_WEB_HOME=str(root / 'app'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
                      AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
                      AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'captures'))
    shared = root / 'shared'
    shared.mkdir()
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    # Optional host defaults are not this offline startup fixture's inputs.
    # Keep the real loop and ordinary-root capture hook, using the prepared
    # interpreter's installed modules rather than fetching another graph.
    modules = {}
    for name, package in [('loop-live', 'amplifier_module_loop_live'),
                          ('hook-context-intelligence', 'amplifier_module_hook_context_intelligence')]:
        if args.tool_registration and name == 'hook-context-intelligence':
            # This startup contract exercises tool registration, not capture or its
            # remote service. Supply an inert, local hook under the ordinary root.
            hook = root / 'capture-module' / 'amplifier_module_hook_context_intelligence'
            hook.mkdir(parents=True)
            (hook / '__init__.py').write_text('async def mount(coordinator, config=None): pass\n')
            modules[name] = str(hook.parent)
            continue
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin, f'Missing prepared startup dependency: {package}'
        modules[name] = str(Path(spec.origin).parent)
    (shared / 'settings.yaml').write_text(json.dumps({
        'bundle': {'app': []},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
        **({'configurator': {'disabled': {'providers': []}}} if args.tool_registration else {}),
        'sources': {'modules': modules},
    }))
    provider = root / 'provider' / 'amplifier_module_provider_fixture'
    provider.mkdir(parents=True)
    calls = root / 'model-calls'
    (provider / '__init__.py').write_text('''from pathlib import Path
from amplifier_core import ProviderInfo
class FixtureProvider:
    name = 'fixture'
    def get_info(self): return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture'})
    async def list_models(self): return []
    async def complete(self, *args, **kwargs):
        Path(''' + repr(str(calls)) + ''').touch()
        raise AssertionError('No model calls allowed in startup qualification')
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None):
    await coordinator.mount('providers', FixtureProvider(), name='fixture')
''')
    context_module = importlib.import_module(
        'amplifier_module_' + args.context.replace('-', '_')).__file__
    if args.tool_registration:
        legacy = root / 'legacy-module' / 'amplifier_module_tool_legacy'
        legacy.mkdir(parents=True)
        mount_tool = ('coordinator.mount_points["tools"]["legacy"] = LegacyTool()'
                      if args.tool_registration == 'retained'
                      else 'await coordinator.mount("tools", LegacyTool(), name="legacy")')
        (legacy / '__init__.py').write_text('''from pathlib import Path
class LegacyTool:
    name = "legacy"
    description = "Installed registration compatibility"
    input_schema = {"type": "object"}
    async def execute(self, args):
        Path(__file__).with_name("executed").touch()
        raise AssertionError("Startup must never execute a registered tool")
async def mount(coordinator, config=None):
    ''' + mount_tool + '\n')
    bundle = root / 'fixture.yaml'
    bundle.write_text(f'''bundle:
  name: startup-fixture
session:
  orchestrator:
    module: loop-live
  context:
    module: {args.context}
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
''')
    if args.tool_registration:
        with bundle.open('a') as stream:
            stream.write(f"tools:\n  - module: tool-legacy\n    source: {legacy.parent}\n")
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
            if args.tool_registration:
                coordinator = worker.session.coordinator
                assert coordinator.get('tools')['legacy'].name == 'legacy'
                assert coordinator.get_capability('web.configurator') is not None
                capability = coordinator.get_capability('experimental.media.v1')
                if args.tool_registration == 'retained' and capability is not None:
                    assert not capability.enabled
        finally:
            # This fixture invokes the worker directly, without a protocol pipe.
            worker.read = worker.shutdown.wait
            worker.shutdown.set()
            await worker.run()
    ready = [e['report'] for e in events if e.get('type') == 'runtime.ready']
    assert len(ready) == 2
    assert ready[0]['resumed'] is False and ready[1]['resumed'] is True
    assert not any(e.get('type') == 'runtime.error' for e in events)
    assert not calls.exists()
    if args.tool_registration:
        assert not (legacy / 'executed').exists()
    assert not any(e.get('type') in {'input.delivered', 'generation.started'} for e in events)
    print(json.dumps({'workerStarts': 2, 'resumed': True, 'modelCalls': 0,
                      'toolCalls': 0, 'registration': args.tool_registration, 'sharedRegistryUnchanged': True, 'productionHistoryTouched': False}))


if __name__ == '__main__':
    asyncio.run(main())
