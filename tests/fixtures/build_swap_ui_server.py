"""Two real packaged interfaces on disposable state; no installer or restart.

Usage: python build_swap_ui_server.py SOURCE OLD_STATIC NEW_STATIC
The control swaps the served asset tree and the host's captured identity together.
It deliberately does not claim to exercise a deployment/install operation.
"""
import asyncio
import json
from pathlib import Path
import sys

from aiohttp import web
import browser_detail_server as fixture
from amplifier_web.installed_build import InstalledBuild

trees = [Path(value).resolve() for value in sys.argv[2:4]]
assert len(trees) == 2 and all((tree / 'index.html').is_file() for tree in trees)
builds = [json.loads((tree / 'build.json').read_text()) for tree in trees]
assert builds[0]['id'] != builds[1]['id'], 'Two distinct real builds are required'
create_app = fixture.create_app


async def create_swappable_app(*args, **kwargs):
    app = await create_app(*args, **kwargs)
    active = 0
    service = app['service']

    def capture(index):
        build = builds[index]
        service.installed_build = InstalledBuild(build['version'], build['version'], build['id'])

    capture(active)

    @web.middleware
    async def packaged_assets(request, handler):
        if request.path == '/api/fixture/install-build' and request.method == 'POST':
            return await install(request)
        if request.path == '/' or not request.path.startswith('/api/'):
            tree = trees[active]
            file = (tree / (request.path.lstrip('/') or 'index.html')).resolve()
            if file.is_relative_to(tree) and file.is_file():
                return web.FileResponse(file, headers={'Cache-Control': 'no-store'})
        return await handler(request)

    async def install(request):
        nonlocal active
        if request.headers.get('Authorization') != 'Bearer fixture-detail-token':
            raise web.HTTPForbidden()
        active = 1
        capture(active)
        service._publish()
        return web.json_response({'installed': builds[active], 'installerExercised': False})

    app.middlewares.insert(0, packaged_assets)
    return app


fixture.create_app = create_swappable_app
asyncio.run(fixture.main())
