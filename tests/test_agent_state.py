import copy
import json
import pytest
from amplifier_web.agent_state import read_state
from amplifier_web.service import AppService


def test_scoped_overview_keeps_other_chat_content_out_and_pages_are_lossless():
    state={'revision':8,'selectedSessionId':'other','sessions':[{'id':'current','title':'New','messages':[{'role':'user','text':'Show a diagram'}]}, {'id':'other','title':'Old','messages':[{'text':'OLD_CHAT_PRIVATE_CONTENT'*10000}]}], 'canvas':{'content':'A'*40000}, 'escaped/key':{'~test':'abc'}}
    preview=read_state(state,{},session_id='current')
    assert preview['session']['id']=='current'
    assert 'OLD_CHAT_PRIVATE_CONTENT' not in json.dumps(preview)
    assert len(json.dumps(preview))<20000
    text='';offset=0
    while True:
        page=read_state(state,{'path':'/canvas/content','offset':offset,'revision':8})
        text+=page['value'];offset=page['nextOffset']
        if offset is None:break
    assert text==state['canvas']['content']
    assert read_state(state,{'path':'/escaped~1key/~0test'})['value']=='abc'
    with pytest.raises(ValueError,match='changed'):read_state(state,{'path':'/canvas','revision':7})
    with pytest.raises(ValueError):read_state(state,{'path':'/not-here'})


async def test_configuration_deduplication_and_on_demand_provenance_survive_restart(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app.state['selectedSessionId']
    provenance={'agents':[{'name':f'agent-{i}','include_paths':['foundation/path/'+('x'*1000)]*10} for i in range(100)]}
    config={'plan':{'tools':[{'module':'tool-fixture','config':{'limit':2}}]},'provenance':provenance}
    app.state['sessions'][0]['configuration']=copy.deepcopy(config)
    app.state['sessionConfiguration']={sid:copy.deepcopy(config)}
    app.state['runtimeControl']={sid:{'configuration.inspect':copy.deepcopy(config)}}
    before=len(json.dumps(app.state));app._save()
    assert len(json.dumps(app.state))<before/10
    assert 'sessionConfiguration' not in app.state
    assert app.state['sessions'][0]['configuration']['plan']==config['plan']
    assert app.state['runtimeControl'][sid]['configuration.inspect']=={'configurationSessionId':sid}
    path='/sessions/0/configuration/provenance/agents/99/include_paths/0'
    result=await app.app_bridge('get_state',{'path':path},sid)
    assert result['value']==provenance['agents'][99]['include_paths'][0]
    dispatched=await app.app_bridge('dispatch',{'action':'canvas.show','args':{'kind':'text','content':'hello'}},sid)
    assert len(json.dumps(dispatched))<30000 and dispatched['state']['canvas']['content']=='hello'
    actions=await app.app_bridge('list_actions',{'prefix':'canvas.'},sid)
    assert actions and all(a['name'].startswith('canvas.') for a in actions)
    await app.close()
    app=AppService(tmp_path,workspace=tmp_path)
    assert (await app.app_bridge('get_state',{'path':path},sid))['value']==provenance['agents'][99]['include_paths'][0]
    await app.close()
