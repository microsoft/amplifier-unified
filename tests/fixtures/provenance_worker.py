"""Real Core/Foundation mounted worker protocol; no model calls or installs."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_web.loaded_provenance import capture_mount
from amplifier_web.runtime_worker import Worker, configure_protocol, publish


async def main():
    worker = Worker()
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            data = json.loads(line)
            if data['op'] == 'start':
                plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}}
                paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
                         for name in ('loop-live', 'context-simple')}
                prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='fixture', session=plan['session']))
                worker.session = await prepared.create_session(session_id=data['session']['id'])
                # Fixture setup deliberately imports user code. The inspected
                # operation must only observe this already-mounted module.
                path = Path(data['session']['workspace']) / 'dependency/module.py'
                spec = importlib.util.spec_from_file_location('fixture_dependency', path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                await worker.session.coordinator.mount('tools', module.ProvenanceTool(), name='fixture')
                worker.provenance_mount = capture_mount(worker.session)
                publish({'type': 'runtime.ready', 'report': {}})
            elif data['op'] == 'stop':
                break
            else:
                await worker.command(data)
    finally:
        if worker.session:
            await worker.session.cleanup()


if __name__ == '__main__':
    configure_protocol()
    asyncio.run(main())
