"""Counts use scalar facts, regardless of retained summary-body size."""
import pytest

from amplifier_web.history_directory_query import archived_count
from test_history_directory_navigation import directory_fixture


@pytest.mark.parametrize('kind,expected', [('roots', 120), ('children', 2), ('archived', 3)])
def test_count_queries_do_not_bind_retained_summary_bodies(tmp_path, kind, expected):
    state, directory = directory_fixture(tmp_path, 124)
    parent = state['sessions'][1]
    for index in (31, 62):
        state['sessions'][index]['parentId'] = parent['id']
    state['conversationOrganization']['archived'].update(s31=1, unknown=1)
    directory.sync(state['sessions'], state)
    directory.db.commit()
    # Most of the library is dormant. The admitted rows retain preparation
    # details, which are irrelevant to all three membership counts.
    state['sessions'] = state['sessions'][:6]
    for row in state['sessions']:
        row['preparation'] = {'detail': 'small'}
    readers = {
        'roots': lambda: directory.root_count(state),
        'children': lambda: directory.child_count(state, parent),
        'archived': lambda: archived_count(directory, state),
    }

    def read_with_statement_size():
        sizes = []
        directory.db.set_trace_callback(lambda sql: sizes.append(len(sql.encode('utf-8'))))
        try:
            return readers[kind](), max(sizes, default=0)
        finally:
            directory.db.set_trace_callback(None)

    small_count, small_statement = read_with_statement_size()
    assert small_count == expected
    for row in state['sessions']:
        row['preparation']['detail'] = 'retained detail ' * 16384
    large_count, large_statement = read_with_statement_size()
    assert large_count == expected
    # An unchanged scalar overlay may reuse the root count's warm fast path.
    assert large_statement <= small_statement
    assert len(state['sessions']) == 6

    # Narrow projections must still observe current membership. These mutate
    # the same admitted row, not a replacement object or a new library.
    if kind == 'roots':
        state['sessions'][0]['sessionKind'] = 'root'
        assert readers[kind]() == expected + 1
    elif kind == 'children':
        state['sessions'][0]['parentId'] = parent['id']
        assert readers[kind]() == expected + 1
    else:
        state['conversationOrganization']['archived'].pop('unknown')
        assert readers[kind]() == expected - 1
        state['conversationOrganization']['archived']['s0'] = 1
        assert readers[kind]() == expected - 1  # A worker is not a root.
        state['sessions'][0]['sessionKind'] = 'root'
        assert readers[kind]() == expected
