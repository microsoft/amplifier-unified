"""Actual host resources/SSE with synthetic question transitions; no provider."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

installed_mode = os.environ.get('AMPLIFIER_TEST_INSTALLED') == '1'
# Under -I only the staged fixture helpers, never the application source, are
# added. The default source/Vite journey retains its existing import path.
sys.path.insert(0, str(Path(__file__).resolve().parent if installed_mode
                       else Path(__file__).resolve().parents[2]))


def installed_inputs():
    """Bind the installed distribution/payload before create_app, and at cleanup."""
    import importlib.metadata
    import importlib.util
    import re

    assert installed_mode and sys.flags.isolated and sys.dont_write_bytecode
    assert 'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ
    assert not (Path(__file__).resolve().parents[2] / 'amplifier_web').exists(), \
        'Stage the maintained test harness outside the product checkout'
    identity_paths = [Path(os.environ[key]) for key in
                      ('AMPLIFIER_TEST_INSTALLED_IDENTITY', 'AMPLIFIER_TEST_WHEEL_IDENTITY')
                      if os.environ.get(key)]
    assert identity_paths and all(path.is_absolute() for path in identity_paths)
    expected = json.loads(identity_paths[0].read_text())
    assert all(json.loads(path.read_text()) == expected for path in identity_paths), \
        'Installed/wheel identity aliases must describe the same wheel'
    assert re.fullmatch(r'[0-9a-f]{64}', expected['wheelSha256'])
    package = Path(os.environ['AMPLIFIER_TEST_PACKAGE_ROOT'])
    assert package.is_absolute()
    package = package.resolve(strict=True)
    prefix = Path(sys.prefix).resolve()
    dist = importlib.metadata.distribution('amplifier-unified')
    base = Path(dist.locate_file('')).resolve()
    assert base.is_relative_to(prefix) and package.is_relative_to(prefix)
    assert package == Path(dist.locate_file('amplifier_web')).resolve()
    for entry in sys.path:
        if entry and (Path(entry) / 'amplifier_web').exists():
            assert (Path(entry) / 'amplifier_web').resolve() == package, \
                'Application source/another installation must not be on sys.path'
    assert dist.read_text('WHEEL')
    assert not json.loads(dist.read_text('direct_url.json') or '{}').get('dir_info', {}).get('editable')
    files = expected['files']
    owned = {str(path) for path in dist.files or ()}
    assert files and set(files) <= owned
    # Installation-generated scripts/RECORD/bytecode are not original wheel
    # payload. All distribution-owned package data and modules must be covered,
    # not merely the three imports or the browser entry used by this journey.
    payload = {name for name in owned if len(Path(name).parts) > 1
               and Path(name).parts[0].isidentifier()
               and '__pycache__' not in Path(name).parts and not name.endswith('.pyc')}
    assert payload <= set(files), 'Identity must cover every installed wheel package file'
    for name, digest in files.items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert re.fullmatch(r'[0-9a-f]{64}', digest), name
        path = base / relative
        assert not path.is_symlink() and path.resolve().is_relative_to(base), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    spec = importlib.util.find_spec('amplifier_web')
    assert spec and Path(spec.origin).resolve() == package / '__init__.py'
    assert all(Path(path).resolve() == package for path in spec.submodule_search_locations)
    roots = {Path(name).parts[0] for name in payload if name.endswith('.py')}
    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name.split('.')[0] not in roots:
            continue
        if getattr(module, '__file__', None):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(base), (name, path)
            relative = str(path.relative_to(base))
            assert relative in files, (name, relative)
            origins[name] = {'file': relative, 'sha256': files[relative]}
        for path in getattr(module, '__path__', ()):
            assert Path(path).resolve().is_relative_to(base), (name, path)
    static = {str(path.relative_to(package / 'static')): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (package / 'static').rglob('*') if path.is_file()}
    assert static and all(files.get('amplifier_web/static/' + name) == digest
                          for name, digest in static.items())
    component = json.loads((package / 'static/client-component.json').read_text())
    assert component['kind'] == 'unified-web-static-artifact' and component['schema'] == 1
    assert {'index.html', 'build.json'} <= set(component['assets'])
    for name, asset in component['assets'].items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert static.get(name) == asset['sha256'], name
        assert (package / 'static' / relative).stat().st_size == asset['bytes'], name
    assert json.loads((package / 'static/build.json').read_text()) == component['build']
    canonical_hash = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return {'verified': True, 'mode': 'installed-production-assets', 'isolated': True,
            'noPythonPath': True, 'wheelSha256': expected['wheelSha256'],
            'identitySha256': canonical_hash(expected), 'version': dist.version,
            'fileCount': len(files), 'payloadFileCount': len(payload),
            'prefix': str(prefix), 'packageRoot': str(package),
            'moduleCount': len(origins), 'moduleOriginsSha256': canonical_hash(origins),
            'modules': {name: value for name, value in origins.items() if name in
                        {'amplifier_web', 'amplifier_web.server', 'amplifier_web.shell_query_owner'}},
            'staticFileCount': len(static), 'staticSha256': canonical_hash(static),
            'componentSha256': static['client-component.json'],
            'indexSha256': static['index.html'], 'source': component['source'],
            'build': component['build']}


async def main(home):
    from contextlib import nullcontext
    if installed_mode:
        before = installed_inputs()
        assert before['moduleCount'] == 0, 'No application import may precede installed input checks'
    from configured_browser import synthetic_discovery
    # Replace external discovery only, through full cleanup. No model/provider or
    # native runtime is needed to exercise real installed host/actions/SSE.
    with synthetic_discovery() if installed_mode else nullcontext():
        await serve(home)


async def serve(home):
    from aiohttp import web
    from configured_browser import seed_connection
    from empty_host_ui_server import Runtime
    from amplifier_web.server import create_app
    from amplifier_web.shell_query_owner import PendingQuery

    seed_connection(home / 'native')
    folder = home / 'workspace'
    folder.mkdir()
    runtime = Runtime()
    installed_identity = installed_inputs() if installed_mode else None
    app = await create_app(home / 'app', workspace=str(folder), runtime=runtime,
        voice=False, background_updates=False, background_work=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    runtime.service = service
    rows = []
    for index in range(26):
        result = await service.dispatch('session.create', {'workspace': str(folder),
            'title': 'Question owner' if index == 12 else f'Reading row {index:02}', 'bundle': 'anchors'})
        row = service._session(result['result']['sessionId'])
        row.update(historyManaged=False, historyLoaded=True, deferRuntimeUntilInteraction=True,
                   messages=[{'id': row['id'] + '-saved', 'role': 'user', 'text': 'Synthetic saved history'}])
        rows.append(row)
    owner, peer = rows[12], rows[13]
    for row in rows:
        await service.dispatch('session.pin', {'id': row['id'], 'pinned': True})
    await service.dispatch('session.select', {'id': owner['id']})
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'navPinned': True,
        'navChatScope': 'all', 'navRecentLimit': 40, 'panel': None}})
    histories = {row['id']: hashlib.sha256(json.dumps(row['messages'], sort_keys=True).encode()).hexdigest()
                 for row in rows}
    held = False
    for name in ('chats', 'pinned_navigation'):
        original = getattr(service.projections, name)
        def projection(*args, _original=original, **kwargs):
            if held:
                raise PendingQuery()
            return _original(*args, **kwargs)
        setattr(service.projections, name, projection)

    def configure(mode):
        nonlocal held
        held = mode != 'fresh'
        owner.update(status='working', approvals=[], error=None,
            questions=[{'id': 'fixture-question', 'revision': 1, 'prompt': 'Choose the fixture route',
                        'required': True, 'status': 'pending', 'options': [], 'allowFreeText': True,
                        'dependency': 'Choose a route', 'createdAt': 1, 'updatedAt': 1,
                        'artifacts': [], 'questionIds': []}])
        if mode not in {'pending', 'initial'}:
            owner['questions'][0].update(status='answered', answer={'text': 'Use the fixture route', 'questionRevision': 1}, delivery={'status':
                mode if mode in {'unknown', 'rejected'} else 'accepted'})
        if mode == 'multiple':
            owner['questions'].append({'id': 'fixture-second', 'revision': 1,
                'prompt': 'Another choice', 'required': True, 'status': 'pending', 'options': [],
                'allowFreeText': True, 'dependency': 'Choose another route', 'createdAt': 1,
                'updatedAt': 1, 'artifacts': [], 'questionIds': []})
        service._publish_changes(sessions={owner['id']})

    def auth(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def change(request):
        auth(request)
        payload = await request.json()
        mode = payload.get('mode')
        if mode not in {'hold', 'answered', 'pending', 'unknown', 'rejected', 'multiple', 'fresh'}:
            raise web.HTTPBadRequest()
        async with service.lock:
            configure('pending' if mode == 'hold' else mode)
        return web.json_response({'mode': mode, 'held': held})

    async def info(request):
        auth(request)
        return web.json_response({'owner': owner['id'], 'peer': peer['id'], 'held': held,
            'mode': 'installed-production-assets' if installed_mode else 'source-vite',
            'installedIdentity': installed_identity,
            'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent,
            'questions': owner['questions'], 'status': owner['status'],
            'historyUnchanged': all(hashlib.sha256(json.dumps(row['messages'], sort_keys=True).encode()).hexdigest()
                                    == histories[row['id']] for row in rows)})

    configure('initial')
    held = False
    app.router.add_post('/api/fixture/activity', change)
    app.router.add_get('/api/fixture/activity', info)
    runner = web.AppRunner(app)
    await runner.setup()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        app['allowed_origins'] |= {url}
        print(json.dumps({'url': url, 'owner': owner['id'], 'peer': peer['id'],
                          'fixture': 'retained-activity', 'pid': os.getpid(), 'base': url}), flush=True)
        await stop.wait()
    finally:
        try:
            await runner.cleanup()
            after = installed_inputs() if installed_mode else None
            if installed_mode:
                assert after['identitySha256'] == installed_identity['identitySha256']
                assert after['staticSha256'] == installed_identity['staticSha256']
            print(json.dumps({'retainedActivityCleanup': {'complete': True,
                              'installedIdentity': after}}), flush=True)
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-retained-activity-') as directory:
        home = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(home / 'native'), AMPLIFIER_WEB_HOME=str(home / 'app'),
            AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / 'legacy'))
        if installed_mode:
            (home / 'user').mkdir()
            os.environ.update(HOME=str(home / 'user'), XDG_CACHE_HOME=str(home / 'cache'),
                              XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'))
        asyncio.run(main(home))
