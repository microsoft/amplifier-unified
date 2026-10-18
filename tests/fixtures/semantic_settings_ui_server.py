"""Real semantic Settings bridge and UI with synthetic credentials; no provider calls."""
import asyncio
import hashlib
import json
import os

from aiohttp import web
import settings_ui_server as fixture

original_main = fixture.main


async def main(home):
    os.environ['OPENAI_API_KEY'] = 'synthetic-openai-browser-fixture-key'
    os.environ.pop('ANTHROPIC_API_KEY', None)
    app = await original_main(home)
    service = app['service']
    calls = []
    original_command = service.management.command

    async def command(action, args, command_id=None, authority=None):
        calls.append({'action': action, 'configurationTarget': args.get('configurationTarget'),
                      'module': args.get('module'), 'commandId': command_id})
        return await original_command(action, args, command_id, authority)

    service.management.command = command
    settings_path = home / 'native/settings.yaml'
    original_settings = hashlib.sha256(settings_path.read_bytes()).hexdigest()

    async def agent(request):
        data = await request.json()
        result = await service.app_bridge('dispatch', {
            'responseMode': 'receipt', 'action': data['action'],
            'args': {**data.get('args', {}), 'clientId': data['clientId']},
            **({'id': data['id']} if data.get('id') else {}),
        }, service._session()['id'])
        return web.json_response(result)

    async def inspect(request):
        return web.json_response({'calls': calls,
            'savedSettingsUnchanged': original_settings == hashlib.sha256(settings_path.read_bytes()).hexdigest()})

    app.router.add_post('/fixture/semantic-settings-agent', agent)
    app.router.add_get('/fixture/semantic-settings', inspect)
    return app


fixture.main = main
if __name__ == '__main__':
    asyncio.run(fixture.serve())
