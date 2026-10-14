"""Exact saved revisions, identity, drafts and no replay across client lifecycles."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.resource_files import collect
from amplifier_web.state_storage import resource
from test_scoped_state_records import committed_session_payloads


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


@pytest.mark.parametrize('transition', ['small-large', 'large-small'])
async def test_file_versions_replace_content_storage_mode_and_survive_restart(app, tmp_path, transition):
    from amplifier_web.canvas_documents import raw_source
    small = '<h1>Small saved version</h1>'
    large = '<h1>Large saved version</h1><!--' + 'x' * 1_100_000 + '-->'
    first, second = (small, large) if transition == 'small-large' else (large, small)
    path = tmp_path/'versions.html'
    path.write_text(first)
    identity = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']['id']
    path.write_text(second)
    await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    path.unlink()  # Saved versions must not depend on the source file.
    assert row(app, identity)['revision'] == 2
    assert bool(row(app, identity).get('contentResource')) == (second == large)
    for version, expected in ((1, first), (2, second), (None, second)):
        await call(app, 'canvas.select', {'id': identity, **({'version': version} if version else {})})
        assert raw_source(canvas(app), app.db) == expected
        with app.clients.bind('one'):
            assert raw_source(app.canvas_views.canvas('primary'), app.db) == expected
        inspected = (await call(app, 'canvas.versions.inspect', {'id': identity, 'version': version or 2, 'includeSource': True}))['result']
        assert inspected['source']['content'] == expected
    await call(app, 'canvas.select', {'id': identity, 'version': 1})
    app._save()
    await app.close()
    reopened = AppService(app.data_dir, workspace=tmp_path)
    try:
        reopened.clients.attach('one')
        assert raw_source(canvas(reopened), reopened.db) == first
        await call(reopened, 'canvas.select', {'id': identity})
        assert raw_source(canvas(reopened), reopened.db) == second
        await call(reopened, 'canvas.versions.restore', {'id': identity, 'expectedRevision': 2, 'version': 1})
        assert raw_source(canvas(reopened), reopened.db) == first
        assert row(reopened, identity)['revision'] == 3
    finally:
        await reopened.close()


@pytest.mark.usefixtures('committed_session_payloads')
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
    from test_scoped_state_records import assert_only_superseded_session_payloads_collected
    assert_only_superseded_session_payloads_collected(app)
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


@pytest.mark.parametrize('case', ['valid', 'later', 'missing', 'ambiguous', 'scope',
                                  'source', 'message', 'suppressed', 'null-marker', 'partial-id'])
async def test_optional_legacy_inspection_is_metadata_only_exact_and_nonmutating(tmp_path, monkeypatch, case):
    from amplifier_web import canvas_versions, artifact_presentation, state_storage
    from test_retained_thumbnails import saved_host, make_true_legacy
    host, sid, mid, records, _ = await saved_host(tmp_path)
    service = host['service']
    try:
        service.clients.attach('metadata-tab')
        record = records[0]
        saved = make_true_legacy(service, record)
        saved['versions'].append({'version': 2, 'title': 'Ordinary revision', 'kind': 'image',
                                  'body': {'$resource': 'b' * 64}})
        saved.update(revision=2, body={'$resource': 'b' * 64})
        saved['publications'].append({'messageId': mid, 'version': 2})
        args = {'sessionId': sid, 'id': saved['id'], 'version': 1,
                'messageId': mid, 'qualifyLegacyGeneration': True}
        if case == 'later':
            args['version'] = 2
        elif case == 'missing':
            service.db.execute('DELETE FROM output_records WHERE id=?', (record['id'],))
        elif case == 'ambiguous':
            duplicate = {k: v for k, v in service.outputs.store.read(record['id']).items()
                         if k not in {'id', 'sessionId', 'version', 'revision', 'createdAt'}}
            service.outputs.store.create(sid, duplicate)
        elif case == 'scope':
            foreign = tmp_path / 'registered-other-workspace'
            foreign.mkdir()
            await service.dispatch('workspace.add', {'path': str(foreign)})
            foreign_id = next(row['id'] for row in service._state['workspaces']
                              if row['path'] == str(foreign))
            service.clients.records['metadata-tab']['selectedWorkspaceId'] = foreign_id
        elif case == 'source':
            args['includeSource'] = True
        elif case == 'message':
            args['messageId'] = 'foreign'
        elif case == 'suppressed':
            saved['publications'][0]['chat'] = 'none'
        elif case == 'null-marker':
            saved['publications'][0]['imageGeneration'] = None
        elif case == 'partial-id':
            saved['publications'][0]['outputId'] = record['id']
        before = deepcopy(saved)
        original_session = service._session
        calls = []

        def metadata_session(identity=None, *, hydrate=True):
            calls.append(hydrate)
            assert hydrate is False
            return original_session(identity, hydrate=False)

        monkeypatch.setattr(service, '_session', metadata_session)
        monkeypatch.setattr(service, 'state_resource', lambda *args: pytest.fail('Inspection opened resource bytes'))
        monkeypatch.setattr(state_storage, 'resource', lambda *args: pytest.fail('Inspection opened source history/body'))
        monkeypatch.setattr(artifact_presentation, 'output_bytes', lambda *args: pytest.fail('Inspection decoded output'))
        monkeypatch.setattr(service.cold_display, 'hydrate', lambda *args: pytest.fail('Inspection hydrated history'))
        with service.clients.bind('metadata-tab'):
            if case == 'scope':
                client = service.clients.records['metadata-tab']
                assert client['selectedSessionId'] == sid
                assert client['selectedWorkspaceId'] == foreign_id != saved['workspaceId']
            if case in {'valid', 'later', 'missing'}:
                result = canvas_versions.command(service, 'canvas.versions.inspect', args, 'ui')
                qualified = result['legacyGeneration']
                assert qualified['status'] == ('generated' if case == 'valid' else 'not-generated')
                if case == 'valid':
                    assert qualified['body'] == saved['versions'][0]['body']
                    assert qualified['outputId'] == record['id']
                    assert qualified['outputVersion'] == record['version']
                    assert qualified['sha256'] == record['sha256']
            else:
                with pytest.raises((AppError, ValueError)):
                    canvas_versions.command(service, 'canvas.versions.inspect', args, 'ui')
            ordinary = canvas_versions.command(service, 'canvas.versions.inspect',
                                               {'sessionId': sid, 'id': saved['id'], 'version': 1}, 'ui')
            assert 'legacyGeneration' not in ordinary and 'source' not in ordinary
        assert saved == before
        assert calls and all(value is False for value in calls)
    finally:
        monkeypatch.undo()
        await service.close()
