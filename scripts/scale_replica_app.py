#!/usr/bin/env python3
"""Private scale audit launcher. Never use against a production data directory.

The product's display/storage/query code remains intact. Runtime execution and
outbound/background automation are deliberately unavailable in this audit lane.
The containing service must have its own root AND network namespace.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal

READ_ACTIONS = frozenset({
    'view.update', 'shell.view.update', 'shell.report', 'session.select', 'workspace.select',
    'session.history', 'session.plans', 'session.inspect', 'session.titlePreview',
    'configuration.defaults', 'providers.list', 'routing.list',
    'schedule.list', 'schedule.read', 'observation.list', 'observation.read',
    'worktree.list', 'worktree.read', 'notifications.status', 'diagnostics.status', 'updates.status',
})


def reading_action(action, args):
    if action == 'shell.command':
        # Admit only one declared read/navigation capability. The real shell
        # handler still validates client, generation and module ownership, then
        # calls the guarded dispatch again for the inner action.
        return isinstance(args, dict) and args.get('action') in READ_ACTIONS
    return action in READ_ACTIONS


def verify_isolation(marker):
    proof = json.loads(Path(marker).read_text())
    if proof.get('purpose') != 'private-scale-replica-20261015':
        raise RuntimeError('Missing replica root marker')
    # A requested PrivateNetwork property is insufficient in LXC: an inherited
    # global drop-in can override it. Inspect the actual current namespace.
    interfaces = {line.split(':', 1)[0].strip()
                  for line in Path('/proc/net/dev').read_text().splitlines()[2:]}
    if interfaces != {'lo'}:
        raise RuntimeError('Replica requires loopback-only private networking')
    if os.environ.get('AMPLIFIER_WEB_HOME') != proof.get('appHome'):
        raise RuntimeError('Replica data ownership does not match root marker')
    return proof


class InertRuntime:
    workers = {}
    async def start(self, *args, **kwargs):
        raise ValueError('Runtime execution is disabled in the private reading replica.')
    send = start
    control = start
    async def stop(self, *args, **kwargs):
        pass
    async def close(self):
        pass


def disable_automation():
    from amplifier_web.schedules import Schedules
    from amplifier_web.collaboration import Collaboration
    from amplifier_web.observations import Observations
    from amplifier_web.notifications import Notifications
    from amplifier_web.smart_tools import SmartToolsManager
    # Install before AppService construction/startup. Retain original historical
    # records; never submit/recover their queued work or mark it completed.
    Schedules.start = lambda self: None
    Collaboration.start = lambda self: None
    Observations.wake = lambda self: None
    async def quiet(*args, **kwargs):
        return None
    async def refuse(*args, **kwargs):
        raise ValueError('External tool execution is disabled in the private reading replica.')
    Collaboration.drain = quiet
    Notifications.send = quiet
    SmartToolsManager.connect = refuse


async def main(args):
    proof = verify_isolation(args.marker)
    disable_automation()
    from aiohttp import web
    from amplifier_web.server import create_app
    app = await create_app(proof['appHome'], runtime=InertRuntime(), voice=False,
                           background_updates=False, preload_providers=False,
                           server_config={'bind': ['127.0.0.1'], 'port': args.port})
    allowed_posts = frozenset({'/api/actions', '/api/view', '/api/shell/resources',
                              '/api/conversation/work-sync', '/api/clients/attach', '/login'})
    @web.middleware
    async def reading_routes(request, handler):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.path not in allowed_posts:
            raise web.HTTPForbidden(text='Mutation is disabled in the private reading replica.')
        return await handler(request)
    app.middlewares.insert(0, reading_routes)
    # All client view/demand transports are real. Historical content is private,
    # with normal fresh-token authentication; there is no auth bypass.
    service = app['service']
    original_dispatch = service.dispatch
    async def dispatch(action, *values, **options):
        args = values[0] if values else options.get('args')
        if not reading_action(action, args):
            raise ValueError('This private replica permits reading and navigation only.')
        return await original_dispatch(action, *values, **options)
    service.dispatch = dispatch
    # Clear copied update candidates in the writable clone only: ordinary app
    # startup must not confirm or activate a historical managed deployment.
    updates = service.state.get('updates', {})
    for field in ('pendingApp', 'pendingRestart', 'pendingReplacement'):
        updates.pop(field, None)
    updates.update(auto=False, phase='idle', error=None)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', args.port)
    await site.start()
    socket = Path(args.socket)
    socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if socket.exists():
        socket.unlink()
    unix = web.UnixSite(runner, str(socket))
    await unix.start()
    socket.chmod(0o600)
    Path(args.ready).write_text(json.dumps({
        'pid': os.getpid(), 'port': args.port, 'runtimeExecution': False,
        'automation': False, 'networkInterfaces': ['lo'],
        'readingOnly': True, 'sourceRevision': proof['sourceRevision'],
    }) + '\n')
    stopped = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--marker', default='/replica-owner.json')
    parser.add_argument('--port', type=int, default=8942)
    parser.add_argument('--socket', default='/run/replica/web.sock')
    parser.add_argument('--ready', default='/run/replica/ready.json')
    asyncio.run(main(parser.parse_args()))
