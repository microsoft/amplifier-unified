"""Exact saved revisions, identity, drafts and no replay across client lifecycles."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.resource_files import collect
from amplifier_web.state_storage import resource


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'data', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service._message(service._session(), 'user', 'First document')
    service.clients.attach('one'); service.clients.attach('two')
    yield service
    await service.close()


async def call(app, name, args=None, client='one'):
    with app.clients.bind(client):
        return await app.dispatch(name, args or {})


def canvas(app, client='one'):
    return app.clients.records[client]['canvas']


def row(app, identity):
    return next(r for r in app.state['canvasArtifacts'] if r['id'] == identity)


async def revise(app, identity, content, expected=1):
    return await call(app, 'canvas.versions.revise', {'id': identity, 'expectedRevision': expected, 'content': content})


async def test_file_identity_unchanged_reopen_changed_source_and_distinct_same_names(app, tmp_path):
    path = tmp_path/'plan.md'; path.write_text('# Original')
    original = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']
    await call(app, 'canvas.show', {'kind': 'auto', 'path': './plan.md'})
    assert canvas(app)['id'] == original['id']
    assert row(app, original['id'])['revision'] == 1
    app._message(app._session(), 'user', 'Improve it')
    path.write_text('# Improved')
    await call(app, 'canvas.show', {'kind': 'markdown', 'path': str(path)})
    saved = row(app, original['id'])
    assert saved['revision'] == 2 and len(saved['versions']) == 2
    assert len(saved['publications']) == 2
    assert canvas(app)['id'] == original['id']
    (tmp_path/'other').mkdir(); other = tmp_path/'other'/'plan.md'; other.write_text('# Improved')
    await call(app, 'canvas.show', {'kind': 'auto', 'path': str(other)})
    assert canvas(app)['id'] != original['id']
    assert len(app.state['canvasArtifacts']) == 2


async def test_exact_links_latest_followers_cas_restore_and_restart(app, tmp_path):
    original = (await call(app, 'canvas.show', {'kind': 'markdown', 'title': 'Plan', 'content': '# One'}))['result']
    identity = original['id']
    await call(app, 'canvas.select', {'id': identity, 'version': 1}, 'two')
    await call(app, 'view.update', {'patch': {'draft': 'Keep unsent draft'}})
    app._message(app._session(), 'user', 'Second version')
    await revise(app, identity, '# Two')
    assert canvas(app)['content'] == '# Two'
    assert canvas(app, 'two')['content'] == '# One'
    with app.clients.bind('two'):
        assert app.canvas_views.canvas('primary')['content'] == '# One'
    with pytest.raises(AppError, match='changed'):
        await revise(app, identity, '# Stale', 1)
    original_versions = deepcopy(row(app, identity)['versions'])
    await call(app, 'canvas.versions.restore', {'id': identity, 'expectedRevision': 2, 'version': 1}, 'two')
    assert row(app, identity)['revision'] == 3
    assert row(app, identity)['versions'][:2] == original_versions
    assert canvas(app)['content'] == canvas(app, 'two')['content'] == '# One'
    await call(app, 'canvas.select', {'id': identity, 'version': 2}, 'two')
    assert canvas(app, 'two')['content'] == '# Two'
    app._save()
    assert not collect(app.db, app._state)
    await app.close()
    reopened = AppService(app.data_dir, workspace=tmp_path)
    try:
        reopened.clients.attach('one'); reopened.clients.attach('two')
        assert canvas(reopened, 'two')['content'] == '# Two'
        with reopened.clients.bind('two'):
            assert reopened.canvas_views.canvas('primary')['content'] == '# Two'
        assert reopened.clients.records['one']['view']['draft'] == 'Keep unsent draft'
        await call(reopened, 'canvas.select', {'id': identity})
        assert canvas(reopened)['content'] == '# One'
    finally:
        await reopened.close()


async def test_dirty_edit_blocks_selection_and_other_client_refinement(app):
    identity = (await call(app, 'canvas.show', {'kind':'text','content':'Original'}))['result']['id']
    with app.clients.bind('one'):
        view = app.canvas_views.summary('primary')
    target = {k: view[k] for k in ('viewId','resourceId','resourceRevision','generation')}
    await call(app, 'canvas.views.dirty', {**target, 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await call(app, 'canvas.select', {'id':identity, 'version':1})
    with pytest.raises(AppError, match='artifact edit'):
        await call(app, 'canvas.versions.revise', {'id':identity,'expectedRevision':1,'content':'Replacement'}, 'two')
    assert row(app,identity)['revision'] == 1
    await call(app, 'canvas.views.recover', target)
    await revise(app,identity,'Replacement')
    with pytest.raises(AppError, match='changed'):
        await call(app, 'canvas.views.command', {**target,'action':'canvas.view','args':{'patch':{'source':True}}})


async def test_agent_actions_are_conversation_scoped_and_preserve_historical_views(app):
    sid = app._session()['id']
    created = await app.app_bridge('dispatch', {'action':'canvas.show','args':{'kind':'text','content':'Original'}},sid)
    identity = created['result']['id']
    await app.app_bridge('dispatch', {'action':'canvas.versions.revise','args':{'id':identity,'expectedRevision':1,'content':'Agent edit'}},sid)
    inspected = await app.app_bridge('dispatch', {'action':'canvas.versions.inspect','args':{'id':identity,'includeSource':True,'version':1}},sid)
    assert inspected['result']['source']['content'] == 'Original'
    await app.app_bridge('dispatch', {'action':'canvas.select','args':{'id':identity,'version':1,'clientId':'one'}},sid)
    assert canvas(app)['readOnlyVersion']
    await call(app, 'session.create', {}, 'two')
    other = app.clients.records['two']['selectedSessionId']
    with pytest.raises(AppError, match='unavailable'):
        await app.app_bridge('dispatch', {'action':'canvas.versions.inspect','args':{'id':identity}},other)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action':'canvas.versions.revise','args':{'id':identity,'sessionId':sid,'expectedRevision':2,'content':'Attack'}},other)


async def test_earlier_fork_does_not_inherit_future_revisions(app):
    identity = (await call(app, 'canvas.show', {'kind':'markdown','content':'First turn'}))['result']['id']
    source = app._session()
    app._message(source,'assistant','Published one')
    app._message(source,'user','Next turn')
    await revise(app,identity,'Future version')
    app._message(source,'assistant','Published two')
    await call(app,'session.fork',{'id':source['id'],'turn':1})
    fork = app.clients.records['one']['selectedSessionId']
    saved = next(r for r in app.state['canvasArtifacts'] if r['sessionId'] == fork)
    assert saved['revision'] == 1
    assert len(saved['versions']) == 1
    assert resource(app.db,saved['body']['$resource'])['content'] == 'First turn'
    assert row(app,identity)['revision'] == 2


async def test_surface_history_retention_and_historical_state_is_read_only(app):
    spec = {'version':1,'stateSchema':{'type':'object'},'events':{},'requests':{}}
    created = (await call(app,'canvas.apps.create',{'title':'App','content':'<h1>One</h1>','manifest':spec,'initialState':{'n':1}}))['result']
    identity = created['id']
    await call(app,'canvas.apps.state',{'id':identity,'expectedRevision':1,'expectedStateRevision':0,'patch':{'n':2}})
    for revision in range(1,23):
        await call(app,'canvas.apps.revise',{'id':identity,'expectedRevision':revision,'expectedStateRevision':revision,'content':f'<h1>Revision {revision+1}</h1>'})
    assert len(row(app,identity)['app']['versions']) == 23
    await call(app,'canvas.select',{'id':identity,'version':1})
    assert canvas(app)['app']['state'] == {'n':1}
    with app.clients.bind('one'):
        selected = app.canvas_views.canvas('primary')
        assert resource(app.db,selected['contentResource']['$resource'])['content'] == '<h1>One</h1>'
    with pytest.raises(AppError,match='read-only'):
        await call(app,'canvas.apps.state',{'id':identity,'expectedRevision':23,'expectedStateRevision':23,'patch':{'n':3}})
    await call(app,'canvas.apps.restore',{'id':identity,'expectedRevision':23,'expectedStateRevision':23,'version':1})
    assert canvas(app).get('selectedVersion') is None
    assert row(app,identity)['app']['revision'] == 24
    assert row(app,identity)['app']['state'] == {'n':2}
