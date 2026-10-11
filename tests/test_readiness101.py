import copy
import pytest
from amplifier_web.service import AppService
from amplifier_web.settings_navigation import route_legacy, settings_state

@pytest.mark.asyncio
async def test_settings_canvas_and_plan_reopen_preserve_identity_and_validate_atomically(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'Pinned work'})
        sid=app.state['selectedSessionId']
        await app.dispatch('view.update',{'patch':{'planExpanded':{sid:True},'settingsCanvas':True,'settingsExpanded':['voice']}})
        assert app.state['canvas']['open']
        assert settings_state(app.state)['surface']=='canvas'
        assert settings_state(app.state)['page']=='voice'
        await app.dispatch('session.create',{'title':'Another'})
        await app.dispatch('session.select',{'id':sid})
        assert app.state['view']['planExpanded'][sid]
        await app.dispatch('view.update',{'patch':{'panel':'settings','settingsExpanded':['loaded-modules']}})
        assert app.state['view']['panel']=='chat-models'
        assert not app.state['view']['settingsCanvas']
        previous=copy.deepcopy(app.state['canvas'])
        with pytest.raises(Exception):
            await app.dispatch('view.update',{'patch':{'settingsCanvas':True,'unknown':True}})
        assert app.state['canvas']==previous
        with pytest.raises(Exception):
            await app.dispatch('view.update',{'patch':{'planExpanded':{sid:'yes'}}})
    finally: await app.close()
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try: assert app.state['view']['planExpanded'][sid] is True
    finally: await app.close()

def test_settings_legacy_routes_are_pure_and_global_inventory_has_no_chat_controls():
    state={'view':{},'canvas':{'open':False}}
    before=copy.deepcopy(state)
    result=route_legacy(state,{'panel':'settings','settingsExpanded':['outputs']})
    assert result['canvasDraft']['delivery']=='outputs'
    assert state==before
    assert not {'conversation','runtime','recall','outputs','publishing','loaded-modules','share-bundle'} & {p['id'] for p in settings_state(state)['pages']}

def test_automatic_update_work_has_no_badge_but_errors_and_manual_choices_do():
    from amplifier_web.attention import snapshot
    state={'settings':{'updates':{'autoInstall':True}},'updates':{'phase':'available','items':[{'id':'component','label':'Included','status':'update'}]}}
    assert not snapshot(state)['items']
    state['updates']['error']='Could not finish'
    assert any(i['id']=='updates:error' for i in snapshot(state)['items'])
    state['updates'].pop('error')
    state['settings']['updates']['autoInstall']=False
    assert any(i['id']=='source:component' for i in snapshot(state)['items'])
