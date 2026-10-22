"""Real Worker controls/bridge + Core file mounts; synthetic boot/lease only.

No recovery implementation lives here. Proposed placement operations fall through
unchanged to Worker/RuntimeControls. Fixture-only commands inject faults or observe
actual mounted tools; they are not claimed as public host APIs.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from configuration_effect_worker import FixtureWorker
from amplifier_core import ModuleCoordinator
from amplifier_module_tool_filesystem import mount as mount_files
from amplifier_module_tool_apply_patch import mount as mount_patch
from amplifier_web.file_source_guard import install_file_source_guard
from amplifier_web.runtime_controls import RuntimeControls, override_path
from amplifier_web.runtime_worker import configure_protocol, publish


def record(event):
    path = Path(os.environ['PATCH_PLACEMENT_TRACE'])
    with path.open('a') as stream:
        stream.write(json.dumps(event) + '\n')


class PlacementWorker(FixtureWorker):
    async def _command_serial(self, data):
        if data['op'] == 'start':
            config = data['session']
            sid = config['id']
            record({'stage': 'start', 'nativeId': sid})
            plan = json.loads(override_path(sid).read_text())
            session = SimpleNamespace(session_id=sid, parent_id=None, config=plan)
            coordinator = ModuleCoordinator(session=session)
            session.coordinator = coordinator
            execution = config.get('workingDirectory') or config['workspace']
            coordinator.register_capability('session.working_dir', execution)
            coordinator.register_capability('web.history_workspace', config['workspace'])
            for row in plan['tools']:
                if row['module'] == 'tool-filesystem':
                    await mount_files(coordinator, copy.deepcopy(row['config']))
                elif row['module'] == 'tool-apply-patch':
                    await mount_patch(coordinator, copy.deepcopy(row['config']))
            # A controlled bad remount must not be accepted as mounted success.
            marker = Path(os.environ['PATCH_PLACEMENT_FAULT'])
            if marker.exists() and marker.read_text() == 'wrong-mounted-base':
                coordinator.get('tools')['apply_patch']._engine.working_dir = config['workspace']
                record({'stage': 'injected-wrong-mounted-base', 'pid': os.getpid()})
            await install_file_source_guard(coordinator)
            self.session = session
            self.runtime = SimpleNamespace(generation=None, queued_inputs=0)
            self.execution = asyncio.create_task(self.shutdown.wait())
            self.controls = RuntimeControls(session, self.runtime)
            self.activation_gate = SimpleNamespace(reset=lambda token: None)
            publish({'type': 'runtime.ready', 'report': {},
                     'workerIdentity': {'pid': os.getpid(), 'sessionId': sid}})
        elif data['op'] == 'fixture.tool':
            tools = self.session.coordinator.get('tools')
            result = await tools[data['name']].execute(data['arguments'])
            publish({'op': 'reply', 'id': data['id'], 'result': {
                'success': result.success, 'output': result.output, 'error': result.error}})
        elif data['op'] == 'fixture.observe':
            tools = self.session.coordinator.get('tools')
            patch = getattr(tools['apply_patch'], 'tool', tools['apply_patch'])
            read = getattr(tools['read_file'], 'tool', tools['read_file'])
            publish({'op': 'reply', 'id': data['id'], 'result': {
                'pid': os.getpid(),
                'patchDirectory': patch._engine.working_dir,
                'readDirectory': read.working_dir,
                'engine': type(patch._engine).__name__,
                'guard': bool(self.session.coordinator.get_capability('web.file_source_guard'))}})
        elif data['op'] == 'fixture.pin':
            # Simulate an independent mounted-config edit after public read/CAS.
            target = next(row for row in self.session.coordinator.config['tools']
                          if row['module'] == 'tool-apply-patch')
            target['config']['working_dir'] = data['value']
            publish({'op': 'reply', 'id': data['id'], 'result': {'changed': True}})
        else:
            if data['op'] == 'control':
                record({'stage': 'control', 'operation': data['operation']})
                if data['operation'] in {'configuration.inspect', 'configuration.providers'}:
                    raise AssertionError('Narrow placement recovery invoked a broad projection')
            await super()._command_serial(data)


async def main():
    worker = PlacementWorker()
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            data = json.loads(line)
            if data['op'] == 'stop':
                record({'stage': 'stop'})
                worker.stop_revision += 1
                break
            task = asyncio.create_task(worker.command(data))
            worker.tasks.add(task)
            task.add_done_callback(worker.tasks.discard)
    finally:
        worker.shutdown.set()
        if worker.execution:
            await worker.execution
        for task in tuple(worker.tasks):
            task.cancel()
        await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
        if worker.controls:
            await worker.controls.close()
        if worker.session:
            await worker.session.coordinator.cleanup()


if __name__ == '__main__':
    configure_protocol()
    asyncio.run(main())
