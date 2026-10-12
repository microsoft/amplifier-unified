"""Synthetic global defaults and two conflicting workspace overrides."""
import asyncio
from pathlib import Path
import settings_collections_server as fixture
from aiohttp import web
from amplifier_web.preferences import SettingsStore

original = fixture.main


async def main(home):
    app = await original(home)
    service = app['service']
    store = SettingsStore(home)
    other = home/'other-workspace'; other.mkdir()
    service.state['workspaces'].append({'id': 'other-workspace', 'name': 'Other workspace', 'path': str(other)})
    for workspace, model in [(home/'workspace', 'selected-only-model'), (other, 'other-only-model')]:
        store.update(workspace, 'local', lambda settings, model=model: settings.update(config={'providers': [
            {'id': 'one', 'module': 'provider-openai', 'config': {'default_model': model, 'api_key': '${FIXTURE_KEY}'}}]}))
    async def files(request):
        return web.json_response({'global': store.read(home, 'global'),
                                  'selected': store.read(home/'workspace', 'local'),
                                  'other': store.read(other, 'local')})
    app.router.add_get('/fixture/configuration-files', files)
    return app


fixture.main = main
if __name__ == '__main__':
    asyncio.run(fixture.serve())
