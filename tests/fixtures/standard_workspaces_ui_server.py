"""Deterministic workspace UX with real local Git imports and one failed import.

Only clone transport and routing catalog discovery are substituted. Production
placement, provisioning receipts, retry logic, filesystem and UI remain real.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import empty_host_ui_server as fixture
from amplifier_web.setup import SetupManager
from amplifier_web.workspace_starters import StarterCatalog
from amplifier_web import workspace_provisioning


async def no_remote_catalog(self, workspace):
    return None


SetupManager.ensure_routing_catalog = no_remote_catalog


def prepare(home):
    for name in ('first', 'second'):
        folder = home / ('origin-' + name)
        folder.mkdir()
        (folder / 'source.txt').write_text('Original ' + name + ' source\n')
        for args in [('init', '--initial-branch=trunk'), ('add', 'source.txt'),
                     ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Fixture')]:
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(folder), *args], check=True, capture_output=True)
    StarterCatalog(home / 'app').command('workspace.starters.save', {'starter': {
        'name': 'Retained retry fixture', 'instructions': 'Preserve existing workspace work.',
        'scratch': True, 'repositories': [
            {'url': 'https://fixture.invalid/first', 'directory': 'one'},
            {'url': 'https://fixture.invalid/second', 'directory': 'two'}]}}, 'fixture-starter')
    original = workspace_provisioning._git

    def local_clone(fd, *args):
        if args[0] != 'clone':
            return original(fd, *args)
        url = args[-2]
        if url not in {'https://fixture.invalid/first', 'https://fixture.invalid/second'}:
            raise ValueError('Fixture permits only its two local repositories')
        name = url.rsplit('/', 1)[-1]
        with (home / 'import-attempts.jsonl').open('a') as stream:
            stream.write(json.dumps({'repository': name}) + '\n')
        if name == 'second' and not (home / 'allow-second-import').exists():
            raise ValueError('Synthetic repository unavailable; completed work was retained')
        mapped = [str(home / ('origin-' + name)) if value == url else value for value in args]
        try:
            return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *mapped],
                cwd=f'/proc/self/fd/{fd}', pass_fds=(fd,), stderr=subprocess.DEVNULL).decode().strip()
        except subprocess.CalledProcessError as error:
            raise ValueError('Local fixture import failed') from error

    workspace_provisioning._git = local_clone


# Synthetic retained authority is prepared explicitly; the production review
# projection and all read-only browser actions run unchanged.
from amplifier_web import server
original_create_app = server.create_app

async def create_app(*args, **kwargs):
    from aiohttp import web
    app = await original_create_app(*args, **kwargs)

    async def saved_review(request):
        service = app['service']
        data = await request.json() if request.method == 'POST' else request.query
        session = service._session(data['sessionId'])
        if request.method == 'POST':
            session['approvals'] = [{'id': 'retained-expired', 'status': 'expired',
                                    'resolutionReason': 'host_restarted'}]
            session['worktreeHandoffs'] = [{'id': 'retained-unknown', 'phase': 'unknown', 'updatedAt': 120}]
            service._save()
        return web.json_response({key: session.get(key) for key in ('approvals', 'worktreeHandoffs')})

    app.router.add_post('/fixture/saved-review', saved_review)
    app.router.add_get('/fixture/saved-review', saved_review)
    return app

server.create_app = create_app


if __name__ == '__main__':
    if '--chat-controls' not in sys.argv:
        sys.argv.append('--chat-controls')
    with tempfile.TemporaryDirectory(prefix='amplifier-standard-workspaces-') as directory:
        home = Path(directory)
        prepare(home)
        asyncio.run(fixture.main(home))
