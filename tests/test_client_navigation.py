from copy import deepcopy
import json
import uuid

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    await service.dispatch('session.create', {'title': 'First'})
    await service.dispatch('session.create', {'title': 'Second'})
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def select(app, sid, **kwargs):
    with app.clients.bind('one'):
        return await app.dispatch('session.select', {'id': sid}, **kwargs)


async def test_selection_is_local_durable_and_keeps_catalog_projections(app, monkeypatch):
    first, second = [next(row['id'] for row in app._state['sessions'] if row['title'] == title) for title in ('First', 'Second')]
    with app.clients.bind('one'):
        app.clients.draft(first, 'Private first draft')
        app.clients.draft(second, 'Private second draft')
        app.browser_state()
        own = app.subscribe()
    with app.clients.bind('two'):
        other_frame = app.browser_state()
        other = app.subscribe()
    index = app.projections.sessions(app.state)
    histories = deepcopy(app._state['sessions'])
    def forbidden(*args, **kwargs):
        pytest.fail('Selecting must not save or hydrate the full history/catalog')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        patch.setattr(app.projections, 'invalidate', forbidden)
        result = await select(app, first, command_id='select-first')
    assert result['state']['selectedSessionId'] == first
    assert result['state']['view']['draft'] == 'Private first draft'
    assert own.get_nowait()['selectedSessionId'] == first
    assert other.empty()
    assert app.projections.sessions(app.state) is index
    assert app._client_snapshots['two']['sessions'] is other_frame['sessions']
    assert app._state['sessions'] == histories
    assert app.clients.records['two']['selectedSessionId'] == second
    stored = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0])
    assert stored['selectedSessionId'] == first
    assert stored['drafts'][second] == 'Private second draft'
    app.unsubscribe(own); app.unsubscribe(other)
    await select(app, second)
    duplicate = await select(app, first, command_id='select-first')
    assert duplicate['duplicate']
    assert duplicate['state']['selectedSessionId'] == second


async def test_unrelated_hidden_workspace_keeps_selection_private(app, tmp_path, monkeypatch):
    from amplifier_web.session_files import project_slug
    from amplifier_web.state_records import load as load_saved_state
    from amplifier_web.workspace_selection import _registration

    first, second = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                     for title in ('First', 'Second')]
    path = tmp_path / 'unrelated-hidden'
    path.mkdir()
    unrelated = _registration(path)
    unrelated['nativeProject'] = project_slug(path)
    app._state['workspaces'].append(unrelated)
    hidden = [unrelated['id'], uuid.uuid5(uuid.NAMESPACE_URL,
        'amplifier-project:' + unrelated['nativeProject']).hex]
    app._state['hiddenNativeWorkspaces'] = hidden.copy()
    for row in app._state['sessions']:
        row['messages'].append({'id': 'retained-' + row['id'], 'role': 'assistant', 'text': 'Keep this saved answer'})
    app._publish_changes(sessions={first, second}, globals={'workspaces', 'hiddenNativeWorkspaces'})
    with app.clients.bind('one'):
        app.clients.draft(first, 'Private destination draft')
        app.clients.draft(second, 'Private source draft')
        app.browser_state()
    owners = list(app._state['sessions'])
    bodies = [(row, {key: row[key] for key in ('messages', 'execution', 'workers') if key in row})
              for row in owners]
    histories = deepcopy(owners)
    other = deepcopy(app.clients.records['two'])

    def forbidden(*args, **kwargs):
        pytest.fail('An unrelated hidden folder must not require saving or hydrating chat bodies')

    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        result = await select(app, first, command_id='select-unrelated-hidden')
    assert result['state']['selectedSessionId'] == first
    assert result['state']['view']['draft'] == 'Private destination draft'
    stored = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0])
    assert stored['selectedSessionId'] == first
    assert stored['drafts'][second] == 'Private source draft'
    assert app.clients.records['two'] == other
    assert app._state['hiddenNativeWorkspaces'] == hidden
    assert load_saved_state(app.db)['hiddenNativeWorkspaces'] == hidden
    assert app._state['sessions'] == histories
    for row, fields in bodies:
        assert next(owner for owner in app._state['sessions'] if owner['id'] == row['id']) is row
        assert all(row[key] is value for key, value in fields.items())


@pytest.mark.parametrize('alias', ['registered', 'native-project'])
async def test_selected_hidden_workspace_still_restores_durably(app, tmp_path, monkeypatch, alias):
    from amplifier_web import client_navigation
    from amplifier_web.session_files import project_slug
    from amplifier_web.state_records import load as load_saved_state
    from amplifier_web.workspace_selection import _registration

    first = next(row for row in app._state['sessions'] if row['title'] == 'First')
    workspace = next(row for row in app._state['workspaces'] if row['path'] == first['workspace'])
    first['workspaceId'] = workspace['id']
    workspace['nativeProject'] = project_slug(workspace['path'])
    selected_alias = (workspace['id'] if alias == 'registered' else
        uuid.uuid5(uuid.NAMESPACE_URL, 'amplifier-project:' + workspace['nativeProject']).hex)
    path = tmp_path / 'still-hidden'
    path.mkdir()
    unrelated = _registration(path)
    app._state['workspaces'].append(unrelated)
    app._state['hiddenNativeWorkspaces'] = [selected_alias, unrelated['id']]
    app._publish_changes(sessions={first['id']}, globals={'workspaces', 'hiddenNativeWorkspaces'})

    def forbidden(*args, **kwargs):
        pytest.fail('Restoring the selected workspace must use shared persistence')

    with monkeypatch.context() as patch:
        patch.setattr(client_navigation, 'select', forbidden)
        result = await select(app, first['id'], command_id='restore-hidden-' + alias)
    assert result['state']['selectedSessionId'] == first['id']
    assert app._state['hiddenNativeWorkspaces'] == [unrelated['id']]
    assert load_saved_state(app.db)['hiddenNativeWorkspaces'] == [unrelated['id']]
    stored = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0])
    assert stored['selectedSessionId'] == first['id']
    assert stored['selectedWorkspaceId'] == workspace['id']


@pytest.mark.parametrize('workspace_id', [None, 'stale-registration'])
async def test_path_fallback_restores_hidden_canonical_registration(app, monkeypatch, workspace_id):
    from pathlib import Path
    from amplifier_web import client_navigation, workspace_selection
    from amplifier_web.state_records import load as load_saved_state

    first = next(row for row in app._state['sessions'] if row['title'] == 'First')
    path = Path(first['workspace']).resolve()
    assert path.is_dir()
    canonical_id = uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex
    retained = next(row for row in app._state['workspaces'] if row['path'] == str(path))
    retained['id'] = 'retained-path-registration'
    if workspace_id is None:
        first.pop('workspaceId', None)
    else:
        first['workspaceId'] = workspace_id
    app._state['hiddenNativeWorkspaces'] = []
    app._publish_changes(sessions={first['id']}, globals={'workspaces', 'hiddenNativeWorkspaces'})
    assert canonical_id not in {row['id'] for row in app._state['workspaces']}

    def forbidden_resolution(*args, **kwargs):
        pytest.fail('No hidden folders must keep the existing path-match fast selection')

    with app.clients.bind('one'), monkeypatch.context() as patch:
        patch.setattr(workspace_selection, 'registration_for_session', forbidden_resolution)
        assert client_navigation.accepts(app, first)

    app._state['hiddenNativeWorkspaces'] = [canonical_id]
    app._publish_changes(globals={'hiddenNativeWorkspaces'})

    def forbidden_selection(*args, **kwargs):
        pytest.fail('The hidden canonical path registration requires shared restoration')

    with monkeypatch.context() as patch:
        patch.setattr(client_navigation, 'select', forbidden_selection)
        result = await select(app, first['id'], command_id='restore-path-' + (workspace_id or 'absent'))
    assert result['state']['selectedSessionId'] == first['id']
    assert app._state['hiddenNativeWorkspaces'] == []
    saved = load_saved_state(app.db)
    assert saved['hiddenNativeWorkspaces'] == []
    assert {row['id'] for row in saved['workspaces'] if row['path'] == str(path)} == {
        canonical_id, retained['id']}
    stored = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0])
    assert stored['selectedSessionId'] == first['id']
    assert stored['selectedWorkspaceId'] == canonical_id


async def test_selection_failure_restores_client_and_receipt(app, monkeypatch):
    first = app._state['sessions'][0]['id']
    previous = deepcopy(app.clients.records['one'])
    revision = app._state['revision']
    def fail(*args, **kwargs):
        raise OSError('fixture disk error')
    with monkeypatch.context() as patch:
        patch.setattr(app.clients, 'save', fail)
        with pytest.raises(OSError):
            await select(app, first, command_id='failed-selection')
    assert app.clients.records['one'] == previous
    assert app._state['revision'] == revision
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('failed-selection',)).fetchone() is None
    assert (await select(app, first, command_id='failed-selection'))['accepted']


async def test_selection_keeps_pending_runtime_publication(app):
    first = app._state['sessions'][0]['id']
    app._publish_progress(session_ids={first}, detail_only=True, record_only=True)
    pending = app._progress_publish_task
    await select(app, first, include_state=False)
    assert app._progress_dirty
    assert app._progress_publish_task is pending
    assert not pending.cancelled()


async def test_dirty_canvas_still_rejects_selection(app, monkeypatch):
    first = app._state['sessions'][0]['id']
    previous = app.clients.records['one']['selectedSessionId']
    def dirty(*args):
        raise AppError('Finish the viewer edit', 409)
    monkeypatch.setattr(app.canvas_views, 'guard_transition', dirty)
    with pytest.raises(AppError, match='viewer edit'):
        await select(app, first)
    assert app.clients.records['one']['selectedSessionId'] == previous


async def test_missing_workspace_uses_normal_registration_path(app):
    from amplifier_web.client_navigation import accepts
    row = app._state['sessions'][0]
    with app.clients.bind('one'):
        assert accepts(app, row)
        assert not accepts(app, {**row, 'workspaceId': 'unregistered', 'workspace': '/unregistered'})
        app.state['canvas'] = {'kind': 'text', 'id': 'saved-artifact'}
        assert not accepts(app, row)


async def test_unchanged_history_check_reads_only_identity(app, monkeypatch):
    row = app._state['sessions'][0]
    row.update(nativeProject='fixture', nativeIdentity='native-id', historyLoaded=True, nativeRevision=[1, 2])
    seen = []
    def revision(source):
        seen.append(source)
        return [1, 2]
    def forbidden(*args, **kwargs):
        pytest.fail('An unchanged history check must not hydrate/copy display payloads')
    monkeypatch.setattr('amplifier_web.automatic_history.revision', revision)
    resolve = app._session
    def metadata_only(identity, *, hydrate=True):
        assert hydrate is False, 'An unchanged check may resolve metadata, never hydrate display'
        return resolve(identity, hydrate=False)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', metadata_only)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        patch.setattr(app, '_publish', forbidden)
        await app.history.refresh_session(row['id'])
    assert len(seen) == 1
    assert set(seen[0]) == {'id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId'}


async def test_saved_canvas_restores_without_shared_writes(app, monkeypatch):
    first, second = [next(row['id'] for row in app._state['sessions'] if row['title'] == title) for title in ('First', 'Second')]
    with app.clients.bind('one'):
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Keep this saved artifact'})
        saved_id = app.state['canvas']['id']
        shared = deepcopy(app._state['canvasArtifacts'])
        app.state['canvas']['view'] = {'scrollTop': 42}
        def forbidden(*args, **kwargs):
            pytest.fail('Saved canvas navigation must not rewrite shared artifacts')
        with monkeypatch.context() as patch:
            patch.setattr(app, '_save', forbidden)
            await app.dispatch('session.select', {'id': first})
            await app.dispatch('session.select', {'id': second})
        assert app.state['canvas']['id'] == saved_id
        assert app.state['canvas']['content'] == 'Keep this saved artifact'
        assert app.state['canvas']['view']['scrollTop'] == 42
        assert app._state['canvasArtifacts'] == shared


async def navigation(app, source, destination, *, command='agent-navigation', client='one', transport=None):
    return await app.app_bridge('dispatch', {'action': 'session.select', 'id': command,
        'responseMode': 'receipt', 'args': {'id': destination, **({'clientId': client} if client else {})},
        **(transport or {})}, source)


async def test_agent_navigation_binds_client_receipt_not_calling_snapshot(app, monkeypatch):
    first, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                     for title in ('First', 'Second')]
    with app.clients.bind('one'):
        app.clients.draft(source, 'Unsubmitted source draft')
        app.clients.draft(first, 'Private destination draft')
        own = app.subscribe()
    with app.clients.bind('two'):
        other = app.subscribe()
    before = deepcopy(app.clients.records['two'])
    sessions = deepcopy(app._state['sessions'])
    try:
        result = await navigation(app, source, first)
        assert result['accepted']
        proof = result['result']
        assert proof['clientId'] == 'one' and proof['sourceSessionId'] == source
        assert proof['selectedSessionId'] == first and proof['rendering'] == 'unverified'
        assert proof['selectionRevision'] == app.clients.records['one']['selectionRevision']
        with pytest.raises(AppError, match='different contents'):
            await navigation(app, first, first)
        assert 'selectedSessionId' not in result['state']  # caller != navigated client
        assert 'Private destination draft' not in json.dumps(result)
        assert own.get_nowait()['selectedSessionId'] == first and other.empty()
        assert app.clients.records['two'] == before and app._state['sessions'] == sessions
        assert app._state['selectedSessionId'] == source
        assert app.clients.records['one']['drafts'][source] == 'Unsubmitted source draft'
        # get_state still truthfully describes the caller, not another chat's private UI.
        caller_state = await app.app_bridge('get_state', {}, source)
        assert caller_state['selectedSessionId'] == source
        request = result['state']['_stateAccess']['readback'][0]
        readback = await app.app_bridge(request['operation'], request['args'], source)
        assert readback['result']['status'] == 'selected'
        assert readback['result']['selectedSessionId'] == first
        # Exact transport recovery reads the receipt; it never navigates again.
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': source})
        duplicate = await navigation(app, source, first)
        assert duplicate['duplicate'] and duplicate['result'] == proof
        assert app.clients.records['one']['selectedSessionId'] == source
        changed = await app.app_bridge(request['operation'], request['args'], source)
        assert changed['result']['status'] == 'changed'
        app.unsubscribe(own)
        disconnected = await app.app_bridge(request['operation'], request['args'], source)
        assert disconnected['result']['status'] == 'disconnected'
        with pytest.raises(AppError, match='No selection receipt'):
            await app.app_bridge(request['operation'], request['args'], first)
    finally:
        app.unsubscribe(own); app.unsubscribe(other)


@pytest.mark.parametrize('kind', ['detached', 'ambiguous', 'foreign', 'saved-disconnected'])
async def test_agent_navigation_does_not_guess_a_client(app, kind):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    with app.clients.bind('two'):
        other = app.subscribe()
    if kind == 'saved-disconnected':
        app.unsubscribe(own)
    if kind == 'foreign':
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': destination})
    before = deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError) as error:
            await navigation(app, source, destination, client='one' if kind in {'foreign', 'saved-disconnected'} else None,
                             transport={'_inputClients': []})
        assert error.value.code == 'ui_client_required'
        assert app.clients.records == before
    finally:
        app.unsubscribe(own); app.unsubscribe(other)


async def test_agent_navigation_uses_origin_and_rechecks_after_lock(app, monkeypatch):
    import asyncio
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    try:
        result = await navigation(app, source, destination, client=None,
            transport={'_inputClients': ['one']})
        assert result['result']['clientId'] == 'one'
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': source})
        entered = asyncio.Event()
        dispatch = app.dispatch
        async def observed(action, *args, **kwargs):
            if action == 'session.select':
                entered.set()
            return await dispatch(action, *args, **kwargs)
        monkeypatch.setattr(app, 'dispatch', observed)
        async with app.lock:
            pending = asyncio.create_task(navigation(app, source, destination, command='wait-navigation'))
            await asyncio.wait_for(entered.wait(), 2)
            app.clients.records['one']['selectedSessionId'] = destination
        with pytest.raises(AppError, match='connected client'):
            await pending
    finally:
        app.unsubscribe(own)


async def test_agent_navigation_keeps_dirty_canvas(app):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Unsaved editor'})
        view = app.canvas_views.summary('primary')
        target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
        await app.dispatch('canvas.views.dirty', {**target, 'dirty': True})
    before = deepcopy(app.clients.records['one'])
    try:
        with pytest.raises(AppError, match='viewer edit'):
            await navigation(app, source, destination)
        assert app.clients.records['one'] == before
    finally:
        app.unsubscribe(own)


async def test_raw_agent_selection_cannot_change_default_or_borrow_a_client(app):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    before = deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError, match='explicit connected'):
            await app.dispatch('session.select', {'id': destination}, origin='agent', caller_session_id=source)
        with app.clients.bind('one'):
            with pytest.raises(AppError, match='registered calling'):
                await app.dispatch('session.select', {'id': destination}, origin='agent')
            with pytest.raises(AppError, match='attached client'):
                await app.dispatch('session.select', {'id': destination, 'clientId': 'two'},
                                   origin='agent', caller_session_id=source)
        assert app._state['selectedSessionId'] == source
        assert app.clients.records == before
    finally:
        app.unsubscribe(own)
