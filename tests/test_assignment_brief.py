"""Bounded manager orientation over existing records, without runtime work."""
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action
from test_assignments import start


async def roster(app, count=3):
    source = app.state['sessions'][0]
    value = await start(app)
    for i in range(count - 1):
        row = app._new_session({'title': 'Member ' + str(i)})
        row['assignmentId'] = value['id']
        row['task'] = {'id': 'task-' + row['id'], 'revision': 3, 'status': 'blocked',
            'objective': 'Finish the feature', 'blockedReason': 'Waiting for an exact result',
            'corrections': [{'text': 'Retain every exact constraint'}]}
        app.state['sessions'].append(row)
        value['members'][row['id']] = {'status': 'bound', 'role': 'commissioned'}
    app.assignments.save(value)
    app._publish_changes(sessions={source['id']})
    return source, value


async def brief(app, source, **args):
    return (await agent_action(app, source, 'assignment.brief',
        {'sessionId': source['id'], **args}, 'read-brief'))['result']


async def test_brief_is_passive_and_keeps_exact_constraints_and_other_members_private(app):
    source, value = await roster(app)
    app.history.ensure_loaded = AsyncMock(side_effect=AssertionError('History must stay cold'))
    app.history.refresh = AsyncMock(side_effect=AssertionError('No history scan'))
    app.cold_display.hydrate = Mock(side_effect=AssertionError('No cold-field hydration'))
    app.runtime.prepare = AsyncMock(side_effect=AssertionError('No runtime admission'))
    before = copy.deepcopy(value)
    commands = app.db.execute('SELECT id,receipt FROM commands ORDER BY id').fetchall()
    result = await brief(app, source)
    assert result['assignmentId'] == value['id'] and len(result['members']) == 3
    peer = next(row for row in result['members'] if row['sessionId'] != source['id'])
    assert peer['task']['blockedReason']['text'] == 'Waiting for an exact result'
    assert 'corrections' not in peer['task']
    assert 'do not replace' in result['authority']
    assert app.assignments.current(source) == before
    assert app.db.execute('SELECT id,receipt FROM commands ORDER BY id').fetchall() == commands
    assert not app.runtime.inputs
    app.history.ensure_loaded.assert_not_awaited()
    app.runtime.prepare.assert_not_awaited()
    app.history.refresh.assert_not_awaited()
    app.cold_display.hydrate.assert_not_called()
    # Restore normal hydration for the separate exact-detail action below.
    del app.cold_display.hydrate
    # The emitted exact-detail handle is an actual accepted passive action.
    detail = await agent_action(app, source, peer['inspectRef']['action'], peer['inspectRef']['args'], 'inspect')
    assert detail['accepted']


async def test_unicode_pages_are_byte_bounded_and_cover_roster_once(app):
    source, value = await roster(app, 64)
    # The real working-set policy can evict roster members while publishing.
    # Initialize every exact member's retained task, not only resident rows.
    for identity in value['members']:
        row = app._session(identity, hydrate=False)
        row['title'] = '🧭' * 100
        app.state.setdefault('runtimeControl', {})[identity] = {'task.get': {'task': {
            'id': 'task', 'revision': 1, 'status': 'blocked',
            'objective': '🧭' * 3000, 'blockedReason': '🧭' * 3000}}}
        row.pop('task', None)
    app._save()
    seen, after, pages = [], None, 0
    while True:
        result = await brief(app, source, limit=16, **({'after': after} if after else {}))
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= 9 * 1024
        assert all(item['task']['objective']['truncated'] for item in result['members'])
        seen += [item['sessionId'] for item in result['members']]
        pages += 1
        after = result['nextAfter']
        if after is None:
            break
        assert pages < 65
    assert len(seen) == len(set(seen)) == 64
    assert set(seen) == set(value['members'])


async def test_cursor_survives_progress_but_refuses_changed_membership_or_scope(app):
    source, value = await roster(app, 4)
    first = await brief(app, source, limit=1)
    cursor = first['nextAfter']
    source['status'] = 'idle'
    source['task'] = {'status': 'completed', 'revision': 9}
    second = await brief(app, source, limit=1, after=cursor)
    assert second['members'][0]['sessionId'] != first['members'][0]['sessionId']
    member = next(sid for sid in value['members'] if sid != source['id'])
    value['members'][member]['status'] = 'detached'
    app.assignments.save(value)
    with pytest.raises(AppError, match='membership changed'):
        await brief(app, source, after=cursor)


async def test_unbound_or_moved_member_keeps_no_title_task_or_request_preview(app):
    source, value = await roster(app, 3)
    peers = [app._session(sid, hydrate=False) for sid in value['members'] if sid != source['id']]
    peers[0]['workspace'] = '/private-unrelated-workspace'
    peers[1]['assignmentId'] = 'a-different-assignment'
    result = await brief(app, source)
    for row in result['members']:
        if row['sessionId'] != source['id']:
            assert row['available'] is False
            assert not {'title', 'task', 'latestRequest', 'inspectRef'} & row.keys()


async def test_only_latest_direct_request_has_result_handle_and_no_false_success(app):
    source, value = await roster(app, 2)
    peer = next(sid for sid in value['members'] if sid != source['id'])
    for identity, sender, text, origin in [('old', source['id'], 'Old result', 'agent'),
            ('current', source['id'], 'Needs attention', 'agent'),
            ('nested', peer, 'Private nested message', 'agent'),
            ('notification', source['id'], 'Duplicate automatic notification', 'system')]:
        receipt = {'requestId': identity, 'commandAction': 'coordination.send',
            'senderSessionId': sender, 'target': {'sessionId': peer}, 'workspace': source['workspace'],
            'assignmentId': value['id'], 'origin': origin, 'delivery': 'accepted',
            'response': {'outcome': 'question', 'outcomeRevision': 2, 'qualified': False, 'text': text}}
        app.collaboration.insert(identity, identity, receipt)
    app.db.commit()
    result = await brief(app, source)
    row = next(item for item in result['members'] if item['sessionId'] == peer)
    request = row['latestRequest']
    assert request['requestId'] == 'current'
    assert request['text']['text'] == 'Needs attention' and request['qualified'] is False
    assert request['resultRef']['args']['requestId'] == 'current'
    detail = await agent_action(app, source, request['resultRef']['action'], request['resultRef']['args'], 'result-detail')
    assert detail['accepted'] and detail['result']['receipt']['requestId'] == 'current'
    assert detail['result']['qualified'] is False


async def test_foreign_caller_cannot_read_manager_brief(app):
    source, value = await roster(app)
    independent = app.state['sessions'][1]
    independent['collaborationGeneration'] = {'id': 'independent-generation', 'inputIds': []}
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(app, independent, 'assignment.brief', {'sessionId': source['id']}, 'foreign')


async def test_unassigned_root_gets_empty_brief_without_starting_assignment(app):
    source = app.state['sessions'][0]
    assert (await brief(app, source))['assignmentId'] is None
    assert app.assignments.current(source) is None


def test_brief_is_read_only_shared_work_recovery_surface():
    from amplifier_web.workspace_work_effects import recovery_read
    assert recovery_read('app_control', {'operation': 'dispatch', 'args': {'action': 'assignment.brief'}})


@pytest.mark.parametrize('transport', ['ui', 'agent'])
@pytest.mark.parametrize('dormant', ['source', 'member', 'both'])
async def test_directory_only_members_and_caller_never_admit_or_hydrate(app, monkeypatch, transport, dormant):
    from amplifier_web import state_records
    source, value = await roster(app, 2)
    peer = next(row for row in app.state['sessions'] if row['id'] in value['members'] and row['id'] != source['id'])
    # Retained runtime task projections remain readable; native control files,
    # messages/configuration/execution are intentionally not needed.
    app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task': {
        'id': 'source-task', 'revision': 5, 'status': 'active', 'objective': 'Current source objective'}}}
    app.state.setdefault('runtimeControl', {})[peer['id']] = {'task.get': {'task': peer.pop('task')}}
    app._save()
    ids = {source['id']} if dormant == 'source' else {peer['id']} if dormant == 'member' else {source['id'], peer['id']}
    app._state['sessions'][:] = [row for row in app._state['sessions'] if row['id'] not in ids]
    for identity in ids:
        app._state['runtimeControl'].pop(identity, None)
    app._state['selectedSessionId'] = next(row['id'] for row in app._state['sessions'] if row['id'] not in value['members'])
    app.projections.invalidate()
    assert all(app.directory.resolve(sid) is not None for sid in ids)
    assert not ids.intersection(row['id'] for row in app._state['sessions'])
    before = copy.deepcopy(app._state)
    records = {sid: state_records.load_owner(app.db, sid) for sid in ids}
    changes = app.db.total_changes
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('active-read')
        raise AssertionError('Brief must not admit, hydrate, refresh, persist or flush progress')
    with monkeypatch.context() as guard:
        for obj, name in [(app, '_session'), (app, '_admit_session'), (app, '_save'),
                          (app, '_flush_pending_progress'), (app.cold_display, 'hydrate'),
                          (app.history, 'refresh'), (app.history, 'ensure_loaded')]:
            guard.setattr(obj, name, forbidden)
        if transport == 'agent':
            result = await brief(app, source)
        else:
            result = (await app.dispatch('assignment.brief', {'sessionId': source['id']}, include_state=False))['result']
    assert attempted == []
    rows = {row['sessionId']: row for row in result['members']}
    assert rows[source['id']]['task']['objective']['text'] == 'Current source objective'
    assert rows[peer['id']]['task']['objective']['text'] == 'Finish the feature'
    assert rows[peer['id']]['task']['blockedReason']['text'] == 'Waiting for an exact result'
    assert app._state == before and app.db.total_changes == changes
    assert {sid: state_records.load_owner(app.db, sid) for sid in ids} == records


async def test_missing_member_has_no_preview_and_missing_caller_does_not_refresh(app, monkeypatch):
    source, value = await roster(app, 2)
    peer = next(sid for sid in value['members'] if sid != source['id'])
    app._state['sessions'][:] = [row for row in app._state['sessions'] if row['id'] != peer]
    app.projections.invalidate()
    app.history.refresh = AsyncMock(side_effect=AssertionError('No discovery'))
    result = await brief(app, source)
    row = next(row for row in result['members'] if row['sessionId'] == peer)
    assert row['available'] is False and 'task' not in row
    with pytest.raises(AppError, match='Select or create'):
        await app.dispatch('assignment.brief', {'sessionId': 'missing-root'}, include_state=False)
    app.history.refresh.assert_not_awaited()


async def test_child_principal_and_internal_caller_cannot_borrow_brief(app):
    source, value = await roster(app)
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {'action': 'assignment.brief',
            'args': {'sessionId': source['id']}, '_runtimeSessionId': 'child-worker'}, source['id'])
    source['sessionKind'] = 'internal'
    with pytest.raises(AppError, match='ordinary root'):
        await brief(app, source)


async def test_cold_control_payload_is_not_materialized_to_build_a_brief(app, monkeypatch):
    from amplifier_web.cold_display import ColdRecord, CONTROL_FIELDS
    from amplifier_web import cold_display
    source, value = await roster(app, 2)
    peer = next(row for row in app.state['sessions'] if row['id'] in value['members'] and row['id'] != source['id'])
    task = peer.pop('task')
    # Production keeps task.get hot; only the supported cold control fields
    # retire. A ColdRecord must not cause unrelated body materialization.
    assert 'task.get' not in CONTROL_FIELDS and 'modelCatalogs' in CONTROL_FIELDS
    app.state.setdefault('runtimeControl', {})[peer['id']] = ColdRecord({
        'task.get': {'task': task},
        '_coldFields': {'modelCatalogs': {'$resource': 'unloaded-body'}}}, app.db)
    with monkeypatch.context() as guard:
        load = Mock(side_effect=AssertionError('No cold control hydration'))
        guard.setattr(cold_display, 'load', load)
        result = await brief(app, source)
        row = next(row for row in result['members'] if row['sessionId'] == peer['id'])
        assert row['task']['objective']['text'] == 'Finish the feature'
        assert '_coldFields' in app.state['runtimeControl'][peer['id']]
        load.assert_not_called()
    app.state['runtimeControl'][peer['id']].pop('_coldFields')
