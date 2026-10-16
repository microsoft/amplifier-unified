"""Synthetic ordinary-entry checks with real Git, service actions and receipts.

Fixture controls seed external state changes, never replace action responses.
No provider, host state, network Git remote, or outbound automation is used.
"""
import asyncio
import hashlib
import json
import signal
import tempfile
from pathlib import Path

from aiohttp import web
import settings_ui_server as fixture
import amplifier_worktrees.git as git_module


async def main(home):
    app = await fixture.main(home)
    service, runtime = app['service'], app['runtime']
    selected = service._session()
    root = Path(selected['workspace'])
    git_module.git(root, 'init', '-b', 'main')
    git_module.git(root, 'config', 'user.name', 'Fixture')
    git_module.git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'file.txt').write_text('Retain these project bytes.\n')
    git_module.git(root, 'add', '.')
    git_module.git(root, 'commit', '-m', 'Synthetic fixture')
    await service.dispatch('session.create', {'title': 'Settled helper', 'workspace': str(root), 'select': False})
    other = next(row for row in service.state['sessions'] if row['id'] != selected['id'])
    await service.dispatch('session.select', {'id': selected['id']})
    long_text = 'Retained exact request ' + 'x' * 6000 + ' END OF ORIGINAL'
    request = await service.dispatch('coordination.send', {
        'sessionId': other['id'], 'senderSessionId': selected['id'],
        'text': long_text, 'mode': 'notify'}, command_id='ordinary-long-message')
    assert request['accepted'] and request['delivery'] == 'notified'
    # Observe real calls; don't stub Git results or the application dispatcher.
    git_calls, runtime_calls = [], []
    original_git = git_module._git

    def observed_git(path, *args, **kwargs):
        git_calls.append({'path': str(path), 'args': list(args)})
        return original_git(path, *args, **kwargs)

    git_module._git = observed_git
    for name in ('start', 'stop', 'control'):
        original = getattr(runtime, name)

        async def observed_runtime(*args, _name=name, _original=original, **kwargs):
            runtime_calls.append(_name)
            return await _original(*args, **kwargs)

        setattr(runtime, name, observed_runtime)
    assignment_id = None

    def fingerprint():
        # Actual project bytes and Git refs/config. Git may refresh its index
        # stat cache during status; it must not change tracked/untracked bytes.
        paths = [p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts]
        paths += [p for p in (root / '.git').rglob('*') if p.is_file() and (
            p.name in {'HEAD', 'config', 'packed-refs'} or 'refs' in p.relative_to(root / '.git').parts)]
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

    async def inspect(request):
        value = service.assignments.load(assignment_id) if assignment_id else None
        return web.json_response({
            'selected': selected['id'], 'other': other['id'], 'workspace': str(root),
            'files': fingerprint(), 'runtimeCalls': runtime_calls, 'gitCalls': git_calls,
            'placement': {row['id']: {k: row.get(k) for k in ('workspace', 'workingDirectory', 'executionRevision', 'runtimeSessionId')} for row in (selected, other)},
            'assignment': value,
            'assignmentUsage': service.assignments.usage(value) if value else None,
            'originalText': long_text,
            'sourceMessage': service.collaboration.resolve_message(other, request_id['messageId']),
        })

    request_id = request

    async def seed_member(request):
        nonlocal assignment_id
        async with service.lock:
            value = service.assignments.current(selected)
            assert value and other.get('assignmentId') is None
            assignment_id = value['id']
            # Seed a previously commissioned, settled member. Commissioning
            # itself is covered by coordination-browser, not claimed here.
            value['members'][other['id']] = {'status': 'bound', 'role': 'commissioned', 'creatorSessionId': selected['id'], 'creationOperationId': 'fixture-prior-commission'}
            value['revision'] += 1
            other['assignmentId'] = assignment_id
            other['status'] = 'idle'
            other['execution'] = {'nodes': [
                {'id': 'known-call', 'kind': 'llm', 'rootSessionId': other['id'], 'sessionId': other['id'], 'assignmentId': assignment_id, 'phase': 'completed', 'endedAt': 2, 'usage': {'totalTokens': 12}},
                {'id': 'unknown-call', 'kind': 'llm', 'rootSessionId': other['id'], 'sessionId': other['id'], 'assignmentId': assignment_id, 'phase': 'completed', 'endedAt': 2, 'usage': {}},
            ]}
            service.assignments.save(value)
            service._publish_changes(sessions={selected['id'], other['id']})
        return web.json_response({'assignmentId': assignment_id, 'revision': value['revision']})

    async def rebound(request):
        # A real in-memory ownership rebind invalidates an already-open picker.
        async with service.lock:
            other['workspace'] = str(home / 'elsewhere')
            service._publish_changes(sessions={other['id']})
        return web.json_response({'ok': True})

    app.router.add_get('/fixture/ordinary-work', inspect)
    app.router.add_post('/fixture/ordinary-work/member', seed_member)
    app.router.add_post('/fixture/ordinary-work/rebound', rebound)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        git_module._git = original_git


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='ordinary-work-') as directory:
        asyncio.run(main(Path(directory)))
