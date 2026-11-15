"""Real host controls retain cold execution ownership and complete worker requests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import execution, execution_records as records
from amplifier_web.cold_display import ColdRecord, MARKER
from amplifier_web.execution_working_set import exact, snapshot, working
from amplifier_web.management import Management
from amplifier_web.service import AppService


@pytest.fixture
async def owned(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    runtime = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), workers={})
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    app.management = Management(app)
    await app.dispatch('session.create', {'title': 'Cold metadata controls'})
    session = app._session(hydrate=False)
    tree = {'nodes': [{'id': f'old-{i}', 'kind': 'tool', 'turnId': 'old',
                       'phase': 'completed', 'detail': {'values': [i]}} for i in range(160)],
            'turns': [{'id': 'old', 'phase': 'completed', 'anchorMessageId': None}],
            'retiredUsageNodes': [], 'currentTurnId': None}
    execution.refresh_usage(tree)
    reference = records.import_tree(app.db, (session['id'],), tree)
    app.db.commit()
    dict.pop(session, 'execution', None)
    dict.setdefault(session, MARKER, {})['execution'] = reference
    owner = working(session)
    original = ColdRecord.__getitem__
    def no_owner_promotion(row, key):
        if row is session and key == 'execution':
            pytest.fail('A metadata control promoted the complete execution owner')
        return original(row, key)
    monkeypatch.setattr(ColdRecord, '__getitem__', no_owner_promotion)
    try:
        yield app, session, owner, tree
    finally:
        await app.close()


async def test_task_mutation_uses_real_runtime_preparation_and_publication(owned):
    from test_task_continuity import controls
    app, session, owner, tree = owned
    sid = session['id']
    worker = controls(sid)
    async def control(identity, operation, args, **kwargs):
        assert identity == sid
        if kwargs.get('guard'):
            kwargs['guard']()
        return await worker.perform(operation, args)
    app.runtime.control = control
    try:
        created = await app.dispatch('task.create', {'sessionId': sid,
            'expectedRevision': 0, 'objective': 'Preserve the accepted history'}, command_id='create')
        changed = await app.dispatch('task.update', {'sessionId': sid,
            'expectedRevision': 1, 'artifactRefs': ['verified.json']}, command_id='update')
        assert created['result']['receipt']['revision'] == 1
        assert changed['result']['receipt']['revision'] == 2
        assert changed['result']['publication']['status'] == 'published'
        assert session['task']['artifactRefs'] == ['verified.json']
        assert app.runtime.start.await_count == 2
        for request in app.runtime.start.await_args_list:
            assert request.args[0] is not session
            assert request.args[0]['execution'] == tree
        assert session._working_execution is owner
        assert not dict.__contains__(session, 'execution')
        assert snapshot(session) == tree
        borrowed = exact(session, 'nodes', {'id': 'old-0'})['detail']['values']
        app._publish_changes(sessions={sid})
        borrowed.append('late correction')
        app._publish_changes(sessions={sid})
        assert snapshot(session)['nodes'][0]['detail']['values'] == [0, 'late correction']
    finally:
        await worker.close()


@pytest.mark.parametrize('detached', [False, True])
async def test_runtime_preparation_keeps_request_overrides_detached(owned, detached):
    app, session, owner, tree = owned
    request = app.management.session({'sessionId': session['id']}) if detached else session
    if detached:
        request['callerOnly'] = 'preserved'
    selected = {'instance': 'fixture', 'model': 'selected'}
    bundle = {'bundle': 'fixture-bundle', 'resetModel': False}
    await app.management.ensure_runtime(request, selection_override=selected,
                                        bundle_replacement=bundle)
    started = app.runtime.start.await_args.args[0]
    assert started is not session
    assert started['execution'] == tree
    assert started['selection'] == selected and started['replaceSavedSelection'] is True
    assert started['bundleReplacement'] == bundle and started['bundle'] == 'fixture-bundle'
    if detached:
        assert started is request and started['callerOnly'] == 'preserved'
    assert 'bundleReplacement' not in session and 'replaceSavedSelection' not in session
    assert session._working_execution is owner
    assert not dict.__contains__(session, 'execution')


@pytest.mark.parametrize('operation', ['configuration.catalog', 'provider.queueSelection'])
async def test_management_catalog_entry_does_not_copy_or_promote_history(owned, monkeypatch, operation):
    app, session, owner, tree = owned
    async def resolve(*args, **kwargs):
        return {'providers': [], 'catalogs': {}, 'effective': {'instance': 'fixture', 'model': 'one'}}
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', resolve)
    def no_full_read(*args, **kwargs):
        pytest.fail('Catalog and queued selection need no complete history snapshot')
    with monkeypatch.context() as guard:
        guard.setattr(records, 'materialize', no_full_read)
        await app.management.perform('runtime.control', {'sessionId': session['id'],
            'operation': operation, 'args': {'instance': 'fixture', 'model': 'one'}})
        if operation == 'configuration.catalog':
            await asyncio.gather(*app.management.conversation_catalog_requests.values())
            assert app.state['runtimeControl'][session['id']]['configuration.catalog']['phase'] == 'ready'
        else:
            assert session['pendingModelSelection'] == {'instance': 'fixture', 'model': 'one'}
    assert session._working_execution is owner
    assert not dict.__contains__(session, 'execution')
    assert snapshot(session) == tree
    app.runtime.start.assert_not_awaited()
