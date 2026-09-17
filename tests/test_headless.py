from argparse import Namespace
import asyncio
import json
from amplifier_web.server import create_app
from amplifier_web.headless import run
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
