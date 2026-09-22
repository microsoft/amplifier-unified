"""Optional native upstream integration. No credentials or real Graph requests."""
import asyncio
import importlib.util
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/work_profile/connected_documents.py'
spec = importlib.util.spec_from_file_location('connected_profile', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
SOURCE = os.environ.get('AMPLIFIER_M365_TEST_SOURCE')


def test_profile_only_prints_native_overrides(tmp_path):
    source = tmp_path / 'm365'
    (source/'behaviors').mkdir(parents=True)
    (source/'behaviors/m365-documents.yaml').write_text('tools: []')
    state=tmp_path/'state'
    result=helper.profile(source,base_bundle='work-base',state_dir=state)
    assert result['includes'][1]['bundle'].endswith('m365-documents.yaml')
    assert result['tools']==[{'module':'tool-m365-documents','config':{
        'connected_documents':True,'auth_mode':'device_code','state_dir':str(state)}}]
    assert not state.exists() and 'smartTools' not in json.dumps(result)


@pytest.mark.skipif(not SOURCE,reason='Select the optional upstream contribution checkout')
async def test_native_behavior_composition(tmp_path,monkeypatch):
    from amplifier_foundation import load_bundle
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'home'))
    base=tmp_path/'base.yaml'
    base.write_text('bundle:\n  name: fixture-base\nproviders:\n  - module: provider-fixture\n')
    output=tmp_path/'profile.yaml'
    output.write_text(json.dumps(helper.profile(SOURCE,base_bundle=base,state_dir=tmp_path/'state',
        auth_config=tmp_path/'auth.yaml',live_url='https://localhost:8765',
        live_token_file=tmp_path/'host.token',live_ca_file=tmp_path/'ca.pem')))
    bundle=await load_bundle(str(output),strict=True)
    plan=bundle.to_mount_plan()
    tools={t['module']:t for t in plan['tools']}
    assert set(tools)=={'tool-m365-auth','tool-m365-documents','tool-m365-office-bridge'}
    assert list(tools).index('tool-m365-auth')<list(tools).index('tool-m365-documents')
    assert tools['tool-m365-documents']['config']['connected_documents']
    assert tools['tool-m365-office-bridge']['config']['enabled']
    assert tools['tool-m365-documents']['source']==str(Path(SOURCE)/'modules/tool-m365-documents')
    assert plan['providers']==[{'module':'provider-fixture'}]
    assert not (tmp_path/'state').exists() and not (tmp_path/'host.token').exists()


@pytest.mark.skipif(not SOURCE,reason='Select the optional upstream contribution checkout')
async def test_shared_ui_agent_native_invocation_and_policy(tmp_path,monkeypatch):
    import sys
    for module in ('tool-m365-documents','tool-m365-auth'):
        monkeypatch.syspath_prepend(str(Path(SOURCE)/'modules'/module))
    import httpx
    from amplifier_core import HookResult
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_tool_m365_documents.client import DocumentsClient
    from amplifier_module_tool_m365_documents.connected import ConnectedDocuments
    from amplifier_module_tool_m365_documents.tool import M365DocumentsTool
    from amplifier_web.runtime_controls import RuntimeControls
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path/'host'))
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent
           for name in ('loop-live','context-simple')}
    prepared=PreparedBundle(plan,BundleModuleResolver(paths),Bundle(name='fixture',session=plan['session']))
    session=await prepared.create_session(session_id='documents-native-fixture')
    controls=RuntimeControls(session,Runtime('documents-native-fixture'))
    values,patches,events=[[1,2,3]],[],[]
    async def endpoint(request):
        nonlocal values
        assert request.headers['Authorization']=='Bearer synthetic'
        path=request.url.path
        if path.endswith('/me'):return httpx.Response(200,json={'id':'user'})
        if path.endswith('/drives/drive'):return httpx.Response(200,json={'driveType':'business'})
        if path.endswith('/items/book'):return httpx.Response(200,json={'id':'book','eTag':'v1'})
        if path.endswith('/createSession'):return httpx.Response(201,json={'id':'remote'})
        assert request.headers['workbook-session-id']=='remote'
        if path.endswith('/worksheets'):return httpx.Response(200,json={'value':[{'id':'sheet'}]})
        if '/range(' in path:
            if request.method=='PATCH':
                values=json.loads(request.content)['values'];patches.append(values)
            return httpx.Response(200,json={'address':'Sheet1!A1:C1','values':values,'formulas':values})
        return httpx.Response(204)
    auth=Mock()
    auth.get_cached_device_token.return_value=SimpleNamespace(access_token='synthetic',tenant_id='tenant')
    client=DocumentsClient(auth,auth_mode='device_code')
    await client._http.aclose()
    client._http=httpx.AsyncClient(base_url='https://graph.microsoft.com/v1.0',transport=httpx.MockTransport(endpoint))
    connected=ConnectedDocuments(client,tmp_path/'receipts')
    await session.coordinator.mount('tools',M365DocumentsTool(client,connected=connected),name='m365_documents')
    deny=False
    async def gate(event,data):
        events.append((data['tool_name'],data.get('actor')))
        return HookResult(action='deny',reason='Fixture policy') if deny else HookResult()
    session.coordinator.hooks.register('tool:pre',gate,name='connected-fixture-policy')
    class HostRuntime:
        async def start(self,record,emit):
            await emit('runtime.status',{'sessionId':record['id'],'status':'ready'})
        async def control(self,sid,operation,args):
            return await controls.perform(operation,args)
        async def close(self):pass
    app=AppService(tmp_path/'app',HostRuntime(),workspace=tmp_path)
    app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        sid=app._session()['id']
        async def call(operation,origin,**arguments):
            payload={'sessionId':sid,'operation':'tool.invoke','args':{'name':'m365_documents',
                'arguments':{'operation':'connected_'+operation,**arguments}}}
            command=str(uuid.uuid4())
            if origin=='ui':await app.dispatch('runtime.control',payload,command_id=command)
            else:await app.app_bridge('dispatch',{'action':'runtime.control','args':payload,'id':command},sid)
            async with asyncio.timeout(5):
                while app.state.get('managementResults',{}).get(command,{}).get('phase') not in {'ready','error'}:
                    await asyncio.sleep(.01)
            assert app.state['managementResults'][command]['phase']=='ready', app.state['managementResults'][command]
            return app.state['runtimeControl'][sid]['tool.invoke']
        opened=(await call('open','ui',drive_id='drive',file_id='book',expected_etag='v1',persist_changes=False,operation_id='open'))['result']['output']
        target={'session_id':opened['session_id'],'drive_id':'drive','file_id':'book','worksheet_id':'sheet','address':'A1:C1'}
        read=(await call('read','agent',**target))['result']['output']
        args={**target,'expected_revision':0,'expected_range_revision':read['range']['range_revision'],'operation_id':'edit','values':[[10,20,30]]}
        deny=True
        denied=await call('edit','ui',**args)
        assert denied['success'] is False and not patches
        deny=False
        edited=await call('edit','agent',**args)
        assert edited['result']['output']['range']['values']==[[10,20,30]] and len(patches)==1
        assert (await call('edit','ui',**args))['result']['output']==edited['result']['output']
        assert len(patches)==1 and ('m365_documents','ui') in events and ('m365_documents','agent') in events
        assert 'synthetic' not in json.dumps(app.state)
        assert 'amplifier_m365.server' not in sys.modules
    finally:
        await app.close();await controls.close();connected.close();await client.close();await session.cleanup()
