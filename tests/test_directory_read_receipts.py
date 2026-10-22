"""Directory acknowledgment joins remain exact without history-squared work."""
from copy import deepcopy

from amplifier_web.attention import session_item_at
from test_history_directory_navigation import directory_fixture


def notice(row, slot):
    item = session_item_at(row, {}, slot)
    return item['id'], item['fingerprint']


def projected(directory, state, **scope):
    sql, args = directory.effective(state, fields={'id', 'unread', 'activity_kind'}, **scope)
    return {row[0]: tuple(row[1:]) for row in directory.db.execute(
        sql + ' SELECT id,unread,activity_kind FROM directory_rows ORDER BY id', args)}


def test_receipt_join_work_scales_with_notices_and_receipts(tmp_path):
    state, directory = directory_fixture(tmp_path, 0)
    rows = [{'id': f'notice-{i}', 'status': 'idle', 'completion': {'id': f'response-{i}'}}
            for i in range(1000)]
    instructions = []
    for count in (250, 1000):
        directory.sync(rows[:count], state)
        state['attentionRead'] = dict(notice(row, 1) for row in rows[:count])
        directory.db.commit()
        steps = 0

        def progress():
            nonlocal steps
            steps += 1000
            return 0

        # Count SQLite VM work, not wall time. A virtual-table nested scan
        # grows about sixteenfold when both input sizes grow fourfold.
        directory.db.set_progress_handler(progress, 1000)
        try:
            result = projected(directory, state)
        finally:
            directory.db.set_progress_handler(None, 0)
        assert len(result) == count
        assert set(result.values()) == {(0, 'idle')}
        instructions.append(steps)
    assert instructions[1] < instructions[0] * 6, instructions
    assert state['sessions'] == []  # No dormant chat was admitted by the read.


def test_current_receipts_and_live_notices_keep_exact_fingerprints(tmp_path):
    state, directory = directory_fixture(tmp_path, 0)
    rows = [{'id': f's{i}', 'title': f'Chat {i}', 'status': 'idle'} for i in range(6)]
    rows[0]['completion'] = {'id': 'first'}
    rows[1]['manualUnread'] = 'manual-one'
    rows[2].update(error='Failure', errorAt=1)
    rows[3].update(error='Reviewed failure', errorAt=2, completion={'id': 'later response'})
    rows[4]['manualUnread'] = 'manual-two'
    rows[5].update(error='Reviewed failure', errorAt=3)
    state['attentionRead'] = dict(notice(rows[i], slot) for i, slot in ((0, 1), (1, 0), (3, 2), (5, 2)))
    state['attentionRead'].update({'session:s2': 'old error occurrence', 'unread:s4': 'old manual marker'})
    directory.sync(rows, state)
    directory.db.commit()
    expected = {'s0': (0, 'idle'), 's1': (0, 'idle'), 's2': (1, 'attention'),
                's3': (1, 'unread'), 's4': (1, 'unread'), 's5': (0, 'idle')}
    assert projected(directory, state) == expected
    assert projected(directory, state, public_ids=['s2', 's3']) == {key: expected[key] for key in ('s2', 's3')}
    assert projected(directory, state, identity='s1') == {'s1': expected['s1']}

    # The admitted overlay supersedes the saved notice; a receipt for the
    # same key and older completion must not acknowledge the new occurrence.
    live = deepcopy(rows[0])
    live['completion'] = {'id': 'second'}
    state['sessions'] = [live]
    expected['s0'] = (1, 'unread')
    assert projected(directory, state) == expected
    frozen = directory.read_snapshot(state)
    try:
        # Same containers and no save/revision: query parameters must still
        # observe current acknowledgments, while a frozen reader stays frozen.
        state['attentionRead'].update(dict(notice(row, slot) for row, slot in
                                           ((live, 1), (rows[2], 2), (rows[4], 0))))
        current = {**expected, 's0': (0, 'idle'), 's2': (0, 'idle'), 's4': (0, 'idle')}
        assert projected(directory, state) == current
        assert projected(frozen, None) == expected
    finally:
        frozen.close()
