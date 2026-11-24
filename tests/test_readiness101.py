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
    from amplifier_web.update_health import current
    state={'settings':{'updates':{'autoInstall':True}},'updates':{'phase':'available','available':1,'items':[{'id':'component','label':'Included','status':'update'}]}}

    def project():
        # UpdateManager publishes current health before attention consumes it.
        state['updates']['currentStatus']=current(state['updates'])
        return snapshot(state)

    automatic=project()
    assert state['updates']['currentStatus']['status']=='available'
    assert not automatic['items']
    assert automatic['badgeUnread']==automatic['settingsUnread']==0

    state['updates'].update(phase='error',error='Could not finish')
    failed=project()
    error=next(i for i in failed['items'] if i['id']=='updates:current')
    assert error['actionRequired'] is True
    assert error['title']=='Update could not finish'
    assert error['detail']=='Could not finish'
    assert error['updateAction']=='diagnostics'
    assert failed['badgeUnread']>0 and failed['settingsUnread']>0
    state['attentionRead']={i['id']:i['fingerprint'] for i in failed['items']}
    reviewed=project()
    assert reviewed['badgeUnread']==reviewed['settingsUnread']==1
    assert state['updates']['error']=='Could not finish'

    state['updates'].update(phase='available',error=None)
    automatic=project()
    assert not automatic['items']
    assert automatic['badgeUnread']==automatic['settingsUnread']==0
    state.pop('attentionRead')  # Exercise a manual choice not already reviewed.
    state['settings']['updates']['autoInstall']=False
    manual=project()
    assert [i['id'] for i in manual['items']]==['source:component']
    assert manual['badgeUnread']==manual['settingsUnread']==1


@pytest.mark.asyncio
async def test_settings_artifact_is_on_demand_reusable_scoped_and_has_no_snapshot(tmp_path):
    from amplifier_web.state_storage import resource
    app = AppService(tmp_path/'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'First'})
        first = app.state['selectedSessionId']
        assert not any(r.get('kind') == 'settings' for r in app.state.get('canvasArtifacts', []))
        await app.dispatch('canvas.show', {'kind': 'settings'})
        identity = app.state['canvas']['id']
        row = next(r for r in app.state['canvasArtifacts'] if r['id'] == identity)
        assert row['title'] == 'Settings'
        assert resource(app.db, row['body']['$resource']) == {}
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Notes'})
        notes = app.state['canvas']['id']
        await app.dispatch('canvas.select', {'id': identity})
        assert settings_state(app.state)['surface'] == 'canvas'
        await app.dispatch('canvas.tabClose', {'id': identity})
        assert app.state['canvas']['id'] == notes
        await app.dispatch('canvas.show', {'kind': 'settings'})
        assert app.state['canvas']['id'] == identity
        assert len([r for r in app.state['canvasArtifacts'] if r['kind'] == 'settings']) == 1
        before = copy.deepcopy(app.state['canvas'])
        with pytest.raises(Exception):
            await app.dispatch('canvas.show', {'kind': 'settings', 'content': 'must not save'})
        assert app.state['canvas'] == before
        await app.dispatch('session.create', {'title': 'Second'})
        assert settings_state(app.state)['surface'] != 'canvas'
        await app.dispatch('canvas.show', {'kind': 'settings'})
        assert app.state['canvas']['id'] != identity
        await app.dispatch('session.select', {'id': first})
        await app.dispatch('canvas.select', {'id': identity})
        assert settings_state(app.state)['surface'] == 'canvas'
    finally:
        await app.close()
