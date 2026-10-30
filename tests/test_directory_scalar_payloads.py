"""Scalar SQLite reads do not bind retained chat bodies they cannot consume."""
from copy import deepcopy
import json

from amplifier_web.directory_attention import snapshot
from amplifier_web.history_directory_observations import revision
from test_history_directory_exact_ids import native_fixture


def test_scalar_queries_preserve_results_without_binding_retained_bodies(tmp_path):
    state, directory, _, project, workspace = native_fixture(tmp_path, 300, 0)
    rows = [{'id': f'owner-{i}', 'title': f'Owner {i}', 'workspace': str(workspace),
             'nativeIdentity': f'saved-{i:05d}', 'nativeProject': project, 'historyManaged': True,
             'status': 'idle', 'completion': {'id': f'complete-{i}'}} for i in range(6)]
    state.update(sessions=rows, selectedSessionId='owner-0', pinnedSessionIds=['owner-0'])
    directory.sync(rows, state)
    directory.db.commit()
    state['attentionRead'] = {item['id']: item['fingerprint'] for item in snapshot(directory, state)['items']}
    expected_notice = snapshot(directory, state, {'owner-0'})
    expected_revision = revision(directory, state)
    measurements = []
    for size in (100, 100_000):
        for row in rows:
            row['coordinationReference'] = {'notes': 'retained-body:' + 'x' * size}
        statements = []
        directory.db.set_trace_callback(statements.append)
        try:
            assert snapshot(directory, state, {'owner-0'}) == expected_notice
            assert revision(directory, state) == expected_revision
        finally:
            directory.db.set_trace_callback(None)
        assert not any('retained-body:' in statement for statement in statements)
        measurements.append(sum(len(statement.encode()) for statement in statements))
        # The full detail owner still has the exact body and canonical native
        # fields. Scalar reads must neither mutate nor replace that authority.
        before = deepcopy(rows)
        detail = directory.resolve('owner-0')
        assert detail['coordinationReference'] == rows[0]['coordinationReference']
        assert rows == before
    assert measurements[1] <= measurements[0]
    print(json.dumps({'scalarStatementBytes': measurements}))


def test_summary_only_changes_fence_observation_memo_and_frozen_reads(tmp_path):
    state, directory, _, _, _ = native_fixture(tmp_path, 1, 1)
    row = state['sessions'][0]
    row['naming'] = {'status': 'idle', 'detail': 'before'}
    before = revision(directory, state)
    frozen = directory.read_snapshot(state)
    try:
        frozen_before = revision(frozen, frozen._frozen_state)
        row['naming']['detail'] = 'changed without a save or app revision'
        after = revision(directory, state)
        assert after['navigation'] != before['navigation']
        assert revision(directory, state) == after
        assert revision(frozen, frozen._frozen_state) == frozen_before
        row['naming']['detail'] = 'before'
        assert revision(directory, state) == before
    finally:
        frozen.close()
