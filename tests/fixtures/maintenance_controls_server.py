"""Built-client controls over the real host owner and HTTP action receipts.

Native safe-boundary execution has separate runtime tests. This protocol fixture
does not claim to stop providers, restore a native frame, or restart an app.
"""
import asyncio
import copy
import json
import os
import signal
import tempfile
from pathlib import Path

from aiohttp import web
from settings_ui_server import Runtime, main as settings_app


class MaintenanceRuntime(Runtime):
    def __init__(self, sessions):
        super().__init__()
        self.inventory = [{'sessionId': row['id'], 'runtimeId': row['id'],
                           'status': 'waiting', 'generationId': 'generation-' + row['id'],
                           'inputIds': ['accepted-' + row['id']]} for row in sorted(sessions, key=lambda row: row['id'])]
        self.calls = []
        self.effects = []
        self.gate = asyncio.Event()
        self.unsupported = True

    def set_maintenance_observer(self, callback): self.observe = callback
    def set_maintenance_authorizer(self, callback): self.authorize = callback
    def set_maintenance_resume_guard(self, callback): self.guard = callback
    def set_maintenance_admission(self, callback): self.admit = callback
    def set_maintenance_loader(self, callback): self.load = callback
    def set_maintenance_releaser(self, callback): self.release = callback
    def maintenance_snapshot(self): return copy.deepcopy(self.inventory)

    async def maintenance_pause(self, epoch):
        self.calls.append({'operation': 'pause', 'epoch': epoch})
        await self.gate.wait()
        self.inventory = [{**row, 'epoch': epoch, 'status': (
            'unsupported' if self.unsupported and index == 26 else 'suspended'),
            'checkpointId': epoch + ':' + row['sessionId']}
            for index, row in enumerate(self.inventory)]
        return copy.deepcopy(self.inventory)

    async def maintenance_resume(self, epoch, session_ids):
        self.calls.append({'operation': 'resume', 'epoch': epoch, 'sessionIds': session_ids})
        assert await self.release(epoch, [row for row in self.inventory if row['sessionId'] in session_ids])
        result = []
        for sid in session_ids:
            proof = next(row for row in self.inventory if row['sessionId'] == sid)
            if await self.authorize(sid, epoch, proof) and await self.guard(sid, epoch, proof):
                self.effects.append({'sessionId': sid, 'epoch': epoch})
                result.append({**proof, 'status': 'resumed'})
        return result

    async def maintenance_stop(self, epoch):
        self.calls.append({'operation': 'stop', 'epoch': epoch})
        self.gate.set()
        return [{**row, 'epoch': epoch, 'status': 'stopped'} for row in self.inventory]

    async def close(self):
        self.gate.set()


async def serve():
    import amplifier_web
    with tempfile.TemporaryDirectory(prefix='unified-maintenance-controls-') as temporary:
        app = await settings_app(Path(temporary))
        service = app['service']
        # Only synthetic hosts and accepted-input identities; never live history.
        for index in range(26):
            service.state['sessions'].append(service._new_session({'title': f'Owned test work {index + 2}'}))
        sessions = service.state['sessions']
        for row in sessions:
            row['status'] = 'ready'
        runtime = MaintenanceRuntime(sessions)
        service.runtime = runtime
        service.maintenance.bind_runtime()
        service.state['updates'].update(phase='idle', pendingApp=None, pendingRestart=None,
                                       forceRestartTarget=None, items=[], available=0)
        service._publish()
        commands = []
        original = service.maintenance.dispatch

        async def dispatch(action, args, origin, command_id, admission_check=None):
            result = await original(action, args, origin, command_id, admission_check)
            commands.append({'action': action, 'args': args, 'id': command_id,
                             'duplicate': bool(result.get('duplicate'))})
            return result
        service.maintenance.dispatch = dispatch

        async def control(request):
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            if request.method == 'POST':
                operation = (await request.json()).get('operation')
                if operation == 'settle':
                    runtime.gate.set()
                elif operation == 'settle-unsupported':
                    row = next(row for row in runtime.inventory if row['status'] == 'unsupported')
                    row['status'] = 'stopped'
                    assert await runtime.observe(copy.deepcopy(row))
                elif operation == 'prepare-supported':
                    runtime.unsupported = False
                    runtime.gate.clear()
                elif operation == 'change-confirmation':
                    async with service.lock:
                        service.maintenance._commit()
                elif operation in {'auxiliary-start', 'auxiliary-settle'}:
                    async with service.lock:
                        service.state['voice']['status'] = 'connected' if operation == 'auxiliary-start' else 'ended'
                        service._publish_changes(globals={'voice'})
                else:
                    raise web.HTTPBadRequest()
            return web.json_response({'maintenance': service.maintenance.snapshot(),
                                      'calls': runtime.calls, 'effects': runtime.effects,
                                      'commands': commands})
        app.router.add_get('/fixture/maintenance', control)
        app.router.add_post('/fixture/maintenance', control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] |= {url}
        stopped = asyncio.Event()
        if os.name != 'nt':
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        print(json.dumps({'url': url, 'package': amplifier_web.__file__}), flush=True)
        try:
            await stopped.wait()
        finally:
            runtime.gate.set()
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
