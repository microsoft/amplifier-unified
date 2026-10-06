"""Cold storage must not retain reconstructed execution payloads a hot save omits."""
import copy
import json

import pytest

from amplifier_web.cold_display import ColdRecord, compact_execution_references, load
from amplifier_web.resource_files import put, root
from amplifier_web.service import AppService
from amplifier_web.session_projection import stored_execution
from test_accounting_canonical import observed_tree, populated_logs, assert_usage


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    yield service
    if not service.closed:
        await service.close()


def tree(identity):
    result = observed_tree(identity)
    result['nodes'].append({'id': 'legacy', 'kind': 'tool', 'output': 'app-only result'})
    result['nodes'].append({'id': 'display', 'kind': 'tool', 'nativeHistory': True,
                            'output': 'reconstructible ' * 10000})
    result['turns'] = [{'id': 'native-turn', 'canonicalHistory': True},
                       {'id': 'app-turn', 'inputId': 'original'}]
    result['currentTurnId'] = 'app-turn'
    return result


async def populate(app):
    await app.dispatch('session.create', {})
    row = app._session()
    row['execution'] = tree(row['id'])
    row['messages'] = [{'id': 'message', 'createdAt': 1, 'role': 'user', 'text': 'retain conversation', 'inputId': 'original'}]
    row['status'] = 'stopped'
    app._state['selectedSessionId'] = None
    app._save()
    return row


async def test_retirement_uses_compact_form_and_preserves_accounting(app, monkeypatch):
    row = await populate(app)
    before = copy.deepcopy(row['execution'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    reference = dict.get(row, '_coldFields')['execution']
    assert reference['executionProjection'] == 1
    assert not dict.__contains__(row, 'execution')
    saved = load(app.db, reference)
    assert len(json.dumps(saved)) < len(json.dumps(before)) / 20
    assert saved['nodes'] == [{'id': 'legacy', 'kind': 'tool', 'output': 'app-only result'}]
    assert saved['turns'] == [before['turns'][1]]
    assert saved['currentTurnId'] == before['currentTurnId']
    assert_usage({'id': row['id'], 'execution': saved})


async def test_restart_migrates_old_blob_and_rebuilds_display_without_changing_logs(app):
    row = await populate(app)
    identity = row['id']
    paths = populated_logs(row)
    old = put(app.db, tree(identity))
    dict.pop(row, 'execution')
    row['_coldFields'] = {'execution': old}
    app._save()
    await app.close()
    original_logs = {path: path.read_bytes() for path in paths}
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        current = next(v for v in restored._state['sessions'] if v['id'] == identity)
        ref = dict.get(current, '_coldFields')['execution']
        assert ref['executionProjection'] == 1 and ref['$resource'] != old['$resource']
        assert not dict.__contains__(current, 'execution')
        assert_usage(current)
        await restored.event_log_view.refresh(identity)
        assert_usage(current)
        assert any(n.get('requestDetail') for n in current['execution']['nodes'])
        assert all(path.read_bytes() == text for path, text in original_logs.items())
        assert current['messages'][0]['text'] == 'retain conversation'
        assert not (root(restored.db) / (old['$resource'] + '.json')).exists()
    finally:
        await restored.close()


def test_migration_is_once_only_and_does_not_hydrate_or_rewrite_shared_blob(app, monkeypatch):
    old = put(app.db, tree('root'))
    rows = [ColdRecord({'id': sid, '_coldFields': {'execution': dict(old)}}, app.db)
            for sid in ['a', 'b']]
    state = {'sessions': rows}
    compact_execution_references(state, app.db)
    refs = [dict.get(v, '_coldFields')['execution'] for v in rows]
    assert refs[0] == refs[1]
    assert all(not dict.__contains__(v, 'execution') for v in rows)
    assert (root(app.db) / (old['$resource'] + '.json')).exists(), 'GC owns removal after manifest commit'
    monkeypatch.setattr('amplifier_web.cold_display.load', lambda *args: pytest.fail('already migrated'))
    compact_execution_references(state, app.db)


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'write'])
def test_migration_failure_retains_old_reference(app, monkeypatch, fault):
    old = put(app.db, tree('root'))
    app.db.commit()
    row = ColdRecord({'id': 'root', '_coldFields': {'execution': dict(old)}}, app.db)
    path = root(app.db) / (old['$resource'] + '.json')
    if fault == 'missing':
        path.unlink()
    elif fault == 'corrupt':
        path.write_text('{}')
    else:
        def fail(*args):
            raise OSError('disk full')
        monkeypatch.setattr('amplifier_web.cold_display.put', fail)
    with pytest.raises((OSError, ValueError)):
        compact_execution_references({'sessions': [row]}, app.db)
    assert dict.get(row, '_coldFields')['execution'] == old
    assert not dict.__contains__(row, 'execution')


def test_pending_and_uncertain_receipts_are_never_dropped():
    original = tree('root')
    original['nodes'][1].update(phase='outcome_unknown', endedAt=None)
    original['nodes'][2].update(phase='running', endedAt=None)
    compact = stored_execution(original)
    receipts = {v['id']: v for v in compact['retiredUsageNodes']}
    assert receipts['root-call']['phase'] == 'outcome_unknown'
    assert receipts['child-call']['phase'] == 'running'
    assert receipts['root-call']['producerId'] == 'producer'
    assert stored_execution(compact) == compact


def test_restart_settles_pending_cold_receipts_but_keeps_settled_trees_cold(app):
    from amplifier_web.capacity import restore_observation
    value = tree('root')
    value['nodes'][1].update(phase='running', endedAt=None)
    old = put(app.db, value)
    row = ColdRecord({'id': 'root', '_coldFields': {'execution': old}}, app.db)
    compact_execution_references({'sessions': [row]}, app.db)
    assert dict.get(row, '_coldFields')['execution']['pendingObservation'] is True
    restore_observation(row)
    assert next(v for v in row['execution']['retiredUsageNodes']
                if v['id'] == 'root-call')['phase'] == 'outcome_unknown'
