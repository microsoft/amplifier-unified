from argparse import Namespace
import asyncio
import json
import aiohttp
from aiohttp import web
import pytest
from amplifier_web.auth import data_identity
from amplifier_web.server import create_app
from amplifier_web.headless import _existing_host, run
from test_service import Runtime

async def test_headless_json_is_same_runtime_with_target_session(aiohttp_server,tmp_path,capsys):
    runtime=Runtime()
    app=await create_app(tmp_path, preload_providers=False,workspace=tmp_path,runtime=runtime,voice=False,background_updates=False)
    server=await aiohttp_server(app)
    args=Namespace(port=server.port,data_dir=str(tmp_path),workspace=str(tmp_path),resume=None,command='run',prompt='One shot',bundle='anchors',provider=None,model=None,max_tokens=None,timeout=5,output_format='json')
    assert await run(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['response']=='Test transport result'
    assert runtime.sent[0][0]==result['sessionId']
    assert app['service']._session()['messages'][0]['text']=='One shot'

async def test_headless_uses_verified_configured_https_origin_without_creating_runner(aiohttp_server, tmp_path, capsys, monkeypatch):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca, ssl_context

    configured = setup_local_ca(tmp_path, load_server_config(tmp_path))
    runtime = Runtime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=runtime, voice=False,
                           background_updates=False, server_config=configured)
    server = await aiohttp_server(app, ssl=ssl_context(tmp_path, configured))
    config = {**configured, "public_origins": [f"https://127.0.0.1:{server.port}"]}
    args = Namespace(port=server.port, data_dir=str(tmp_path), workspace=str(tmp_path), resume=None, command="run",
                     prompt="Remote task", bundle="anchors", provider=None, model=None, max_tokens=None, timeout=5,
                     output_format="json")
    monkeypatch.setattr("amplifier_web.headless.web.AppRunner",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote host must not create AppRunner")))
    assert await run(args, config=config) == 0
    assert json.loads(capsys.readouterr().out)["response"] == "Test transport result"
    assert runtime.sent


async def test_explicit_target_not_changed_by_selected_conversation(tmp_path):
    from amplifier_web.service import AppService
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{'title':'Target'})
    target=service._session()['id']
    await service.dispatch('session.create',{'title':'Other'})
    await service.dispatch('conversation.send',{'sessionId':target,'text':'Targeted'})
    await asyncio.gather(*service.tasks)
    assert not service._session()['messages']
    assert service._session(target)['messages'][0]['text']=='Targeted'
    await service.close()

async def test_management_result_correlates_exact_command(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    service=AppService(tmp_path,Runtime(),workspace=tmp_path);service.management=Management(service)
    await service.dispatch('notifications.get',{},command_id='fresh')
    await asyncio.gather(*service.tasks)
    assert service.state['managementResults']['fresh']['phase']=='ready'
    await service.close()


async def test_existing_host_refuses_another_data_directory(aiohttp_server, tmp_path):
    app=web.Application()
    async def health(request):
        return web.json_response({'app':'amplifier-unified','dataIdentity':data_identity(tmp_path/'other')})
    app.router.add_get('/api/health',health)
    server=await aiohttp_server(app)
    async with aiohttp.ClientSession() as client:
        with pytest.raises(ValueError,match='different or older host'):
            await _existing_host(client,[str(server.make_url('')).rstrip('/')],data_identity(tmp_path/'wanted'))


@pytest.mark.parametrize('selected_kind', ['worker', 'root', 'none'])
async def test_headless_continue_selects_only_roots_implicitly(aiohttp_server, tmp_path, capsys, selected_kind):
    runtime = Runtime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=runtime, voice=False,
                           background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Older independent chat'})
    older_root = service._session()['id']
    await service.dispatch('session.create', {'title': 'Recent independent chat'})
    recent_root = service._session()['id']
    await service.dispatch('session.create', {'title': 'Saved subagent'})
    worker = service._session()
    worker.update(sessionKind='worker', parentId=recent_root, nativeParentId=recent_root)
    assert service.state['sessions'][0]['id'] == worker['id']
    service.state['selectedSessionId'] = {'worker': worker['id'], 'root': older_root, 'none': None}[selected_kind]
    server = await aiohttp_server(app)
    args = Namespace(port=server.port, data_dir=str(tmp_path), workspace=str(tmp_path), resume=None,
                     command='continue', prompt='Continue the chat', bundle=None, provider=None, model=None,
                     max_tokens=None, timeout=5, output_format='json')
    assert await run(args) == 0
    result = json.loads(capsys.readouterr().out)
    expected = older_root if selected_kind == 'root' else recent_root
    assert result['sessionId'] == expected
    assert runtime.sent == [(expected, 'Continue the chat', service._session(expected)['messages'][0]['inputId'])]
    assert worker['messages'] == []
