"""Current usage continues the exact fold; unsupported inputs still rebuild."""
import copy
import sqlite3

import pytest

from amplifier_web import execution, execution_records as records
from amplifier_web import execution_working_set as ws
from amplifier_web.cold_display import ColdRecord, MARKER


@pytest.fixture
def owner_factory(tmp_path):
    connections = []
    def make(edit=None):
        tree = {'currentTurnId': 'active-turn', 'retiredUsageNodes': [],
                'turns': [{'id': 'old-turn', 'phase': 'completed', 'anchorMessageId': None},
                          {'id': 'active-turn', 'phase': 'running', 'anchorMessageId': None}],
                'nodes': [{'id': 'old-' + str(i), 'kind': 'llm', 'phase': 'completed',
                           'turnId': 'old-turn', 'usage': {'inputTokens': value, 'costUsd': value},
                           'payload': {'keep': i}} for i, value in enumerate([float(2**53), 1., 1.])]
                         + [{'id': 'worker', 'kind': 'worker', 'phase': 'running',
                             'turnId': 'active-turn', 'payload': {'keep': 'worker'}},
                            {'id': 'live', 'kind': 'llm', 'phase': 'running',
                             'parentId': 'worker', 'turnId': 'active-turn', 'usage': {}}]}
        if edit is not None:
            edit(tree)
        execution.refresh_usage(tree)
        db = sqlite3.connect(tmp_path / (str(len(connections)) + '.sqlite3'))
        connections.append(db)
        db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY, value TEXT)')
        ref = records.import_tree(db, ('chat',), tree)
        db.commit()
        session = ColdRecord({'id': 'chat', 'messages': [], MARKER: {'execution': ref}}, db)
        ws.refresh_usage(session)
        return db, session, ws.working(session)
    yield make
    for db in connections:
        db.close()


def save(db, session):
    owner = ws.working(session)
    pending = ws.PendingExecution(owner, owner.stage())
    db.commit()
    pending.committed(session)


def equivalent(session, monkeypatch, *, full_reads, canonical=False):
    expected = ws.snapshot(session)
    execution.refresh_usage(expected)
    calls = []
    read = records.read_usage
    with monkeypatch.context() as patch:
        def tracked(*args, **kwargs):
            calls.append(1)
            return read(*args, **kwargs)
        patch.setattr(records, 'read_usage', tracked)
        ws.refresh_usage(session)
    assert len(calls) == full_reads
    actual = ws.snapshot(session)
    if canonical:
        from amplifier_web.session_projection import stored_execution
        assert stored_execution(actual) == stored_execution(expected)
    else:
        assert actual == expected


def test_current_nested_alias_and_real_ingest_append_do_not_read_history(owner_factory, monkeypatch):
    db, session, owner = owner_factory()
    assert owner.usage_prefix is not None
    usage = ws.exact(session, 'nodes', {'id': 'live'})['usage']
    usage.update(inputTokens=1., costUsd=1., costUnavailableReason='provisional')
    equivalent(session, monkeypatch, full_reads=0)
    with monkeypatch.context() as patch:
        patch.setattr(records, 'read_usage', lambda *a, **kw: pytest.fail('Normal append rebuilt history'))
        execution.ingest(session, {'id': 'new-call', 'kind': 'llm', 'parentId': 'worker',
                                  'turnId': 'active-turn', 'phase': 'running', 'usage': {'inputTokens': 2.}})
        execution.ingest(session, {'id': 'new-tool', 'kind': 'tool', 'parentId': 'live',
                                  'turnId': 'active-turn', 'phase': 'completed', 'output': {'kept': True}})
    usage['inputTokens'] = 7.
    equivalent(session, monkeypatch, full_reads=0)
    save(db, session)
    equivalent(session, monkeypatch, full_reads=0)
    assert ws.exact(session, 'nodes', {'id': 'live'})['usage'] is usage


def test_actual_historical_mutable_admission_rebuilds_but_missing_lookup_does_not(owner_factory, monkeypatch):
    _, session, owner = owner_factory()
    prefix = owner.usage_prefix
    assert ws.exact(session, 'nodes', {'id': 'not-there'}) is None
    assert owner.usage_prefix is prefix
    ws.exact(session, 'nodes', {'id': 'old-1'})['usage']['inputTokens'] = 9.
    assert owner.usage_prefix is None
    equivalent(session, monkeypatch, full_reads=1)


@pytest.mark.parametrize('corrected', [8, 1])
def test_prefix_llm_ancestor_direct_borrowed_correction_rebuilds(owner_factory, monkeypatch, corrected):
    def nested(tree):
        tree['nodes'][-1]['parentId'] = 'old-2'
        tree['nodes'][2]['turnId'] = 'active-turn'
    _, session, owner = owner_factory(nested)
    assert 'old-2' in owner.usage_prefix.closed_folds
    ws.exact(session, 'nodes', {'id': 'live'})['usage']['inputTokens'] = 3
    equivalent(session, monkeypatch, full_reads=0)
    # Derived aggregate admission exposes this full old row through current().
    ancestor = next(row for row in ws.current(session)['nodes'] if row['id'] == 'old-2')
    ancestor['usage']['inputTokens'] = corrected
    equivalent(session, monkeypatch, full_reads=1)
    assert ws.exact(session, 'nodes', {'id': 'old-2'}) is ancestor
    assert ancestor['payload'] == {'keep': 2}


def test_late_missing_parent_and_orphan_turn_resolution_rebuild(owner_factory, monkeypatch):
    def orphan(tree):
        tree['nodes'][0].update(parentId='late-parent', turnId='late-turn')
    _, session, owner = owner_factory(orphan)
    assert owner.usage_prefix is not None
    owner.current['turns'].append({'id': 'late-turn', 'phase': 'running'})
    equivalent(session, monkeypatch, full_reads=1)
    owner.current['nodes'].append({'id': 'late-parent', 'kind': 'worker',
                                   'phase': 'running', 'turnId': 'late-turn'})
    equivalent(session, monkeypatch, full_reads=1)
    assert next(row for row in owner.current['nodes'] if row['id'] == 'late-parent')['aggregateUsage']['calls'] == 1


def test_unresolved_ordered_turn_repairs_keep_complete_reducer(owner_factory, monkeypatch):
    def chain(tree):
        tree['currentTurnId'] = None
        tree['nodes'] = [dict(id='a', kind='llm', phase='running', parentId='b', usage={}),
                         dict(id='b', kind='worker', phase='running', parentId='c'),
                         dict(id='c', kind='worker', phase='running', parentId='d'),
                         dict(id='d', kind='worker', phase='running', parentId='e'),
                         dict(id='e', kind='worker', phase='running', turnId='active-turn')]
    _, session, owner = owner_factory(chain)
    assert owner.usage_prefix is None
    equivalent(session, monkeypatch, full_reads=1)
    equivalent(session, monkeypatch, full_reads=1)
    assert ws.exact(session, 'nodes', {'id': 'a'})['turnId'] == 'active-turn'


def test_only_exclusive_successful_retirement_advances_prefix(owner_factory, monkeypatch):
    db, session, owner = owner_factory()
    borrowed = ws.exact(session, 'nodes', {'id': 'live'})['usage']
    borrowed.update(inputTokens=4, costUnavailableReason='unknown final price')
    ws.exact(session, 'nodes', {'id': 'live'}).update(phase='completed', endedAt=1)
    equivalent(session, monkeypatch, full_reads=0)
    before = copy.deepcopy(vars(owner.usage_prefix))
    old_reference = copy.deepcopy(owner.reference)
    owner.stage()
    db.rollback()
    assert owner.reference == old_reference
    assert vars(owner.usage_prefix) == before
    save(db, session)
    assert 'live' in owner.usage_prefix.suffix  # The issued nested alias is still live.
    borrowed['inputTokens'] = 5
    equivalent(session, monkeypatch, full_reads=0)
    del borrowed
    save(db, session)
    assert 'live' not in owner.usage_prefix.suffix
    assert not any(row['id'] == 'live' for row in owner.current['nodes'])
    equivalent(session, monkeypatch, full_reads=0)


def test_out_of_order_retirement_keeps_later_call_in_order(owner_factory, monkeypatch):
    db, session, owner = owner_factory()
    owner.current['nodes'].append({'id': 'later', 'kind': 'llm', 'parentId': 'worker',
                                   'turnId': 'active-turn', 'phase': 'completed', 'usage': {'inputTokens': 1.}})
    equivalent(session, monkeypatch, full_reads=0)
    save(db, session)
    assert owner.usage_prefix.suffix == ['live', 'later']
    assert not any(row['id'] == 'later' for row in owner.current['nodes'])
    ws.exact(session, 'nodes', {'id': 'live'}).update(usage={'inputTokens': 1.}, phase='completed', endedAt=2)
    equivalent(session, monkeypatch, full_reads=0)
    save(db, session)
    assert owner.usage_prefix.suffix == []
    equivalent(session, monkeypatch, full_reads=0)


def test_bounds_and_unsupported_topology_fall_back_without_rejecting_work(owner_factory, monkeypatch):
    from amplifier_web.execution_usage_prefix import MAX_ITEMS
    _, session, owner = owner_factory()
    owner.current['nodes'].extend({'id': 'extra-' + str(i), 'kind': 'llm', 'phase': 'running',
                                  'turnId': 'active-turn', 'usage': {'inputTokens': 1}} for i in range(MAX_ITEMS))
    equivalent(session, monkeypatch, full_reads=1)
    assert owner.usage_prefix is None
    assert len(owner.current['nodes']) == MAX_ITEMS + 2
    assert owner.current['aggregateUsage']['calls'] == MAX_ITEMS + 4


def test_detached_owner_has_independent_derived_state(owner_factory, monkeypatch):
    _, session, owner = owner_factory()
    detached = owner.detached()
    assert detached.usage_prefix is None
    detached.current['nodes'][-1]['usage']['inputTokens'] = 99
    assert owner.current['nodes'][-1]['usage'] == {}
    equivalent(session, monkeypatch, full_reads=0)


@pytest.mark.parametrize('change', ['filtered-number', 'dropped-placement'])
def test_canonical_retirement_changes_invalidate_raw_fold(owner_factory, monkeypatch, change):
    def observed(tree):
        tree['nodes'][-1].update(liveObservation=True, producerId='source',
                                 sessionId='native', rootSessionId='native', usage={'inputTokens': True})
    db, session, owner = owner_factory(observed)
    assert owner.usage_prefix is not None
    ws.exact(session, 'nodes', {'id': 'live'}).update(phase='completed', endedAt=1)
    if change == 'dropped-placement':
        ws.exact(session, 'nodes', {'id': 'live'})['canonicalHistory'] = True
    ws.refresh_usage(session)
    save(db, session)
    assert owner.usage_prefix is None
    equivalent(session, monkeypatch, full_reads=1, canonical=True)
    stored = records.materialize(db, owner.reference)
    if change == 'filtered-number':
        assert 'inputTokens' not in next(row for row in stored['nodes'] if row['id'] == 'live')['usage']
    else:
        assert not any(row['id'] == 'live' for row in stored['nodes'])


def test_supported_observation_projection_keeps_ordered_prefix(owner_factory, monkeypatch):
    def observed(tree):
        tree['nodes'][-1].update(liveObservation=True, producerId='source', sessionId='native',
                                 rootSessionId='native', status='working', usage={'inputTokens': 4})
    db, session, owner = owner_factory(observed)
    ws.exact(session, 'nodes', {'id': 'live'}).update(phase='completed', endedAt=1)
    ws.refresh_usage(session)
    save(db, session)
    assert owner.usage_prefix is not None
    assert 'live' not in owner.usage_prefix.suffix
    equivalent(session, monkeypatch, full_reads=0, canonical=True)


def test_flag_only_ancestor_ownership_change_survives_retirement(owner_factory, monkeypatch):
    def observed(tree):
        tree['nodes'][-2].update(liveObservation=True, producerId='source',
                                 sessionId='native', rootSessionId='native')
    db, session, owner = owner_factory(observed)
    ws.exact(session, 'nodes', {'id': 'worker'}).update(liveObservation=False, phase='completed', endedAt=1)
    ws.refresh_usage(session)
    save(db, session)
    assert not any(row['id'] == 'worker' for row in owner.current['nodes'])
    assert owner.usage_prefix.usage_owned['worker'] is True
    ws.exact(session, 'nodes', {'id': 'live'})['usage']['inputTokens'] = 7
    equivalent(session, monkeypatch, full_reads=0)
    assert ws.exact(session, 'nodes', {'id': 'worker'})['aggregateUsage']['inputTokens'] == 7
