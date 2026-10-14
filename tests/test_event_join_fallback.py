"""Legacy fallback placement must stay exact without a scan per event."""
import copy
import json

import pytest

from amplifier_web.event_log_view import EventLogView, FallbackTurnLookup, event_path


@pytest.mark.parametrize('native', [False, True])
def test_fallback_anchor_preserves_presentation_order_for_unsorted_legacy_times(native):
    rows = [
        {'id': 'latest-time', 'createdAt': 50},
        {'id': 'earlier-time-last-position', 'createdAt': 10},
        {'id': 'native-not-time-fallback', 'createdAt': 5, 'source': 'native'},
        {'id': 'invalid-time', 'createdAt': '20'},
        {'id': 'nan-time', 'createdAt': float('nan')},
        {'id': 'same-time-last-position', 'createdAt': 10},
        {'id': 'infinite-time', 'createdAt': float('inf')},
    ]
    lookup = FallbackTurnLookup(rows, {}, native_messages=native)
    for at, expected in [(10, rows[5]), (49, rows[5]), (50, rows[5]),
                         (float('inf'), rows[6]), (9, None), (None, None),
                         ('50', None), (float('nan'), None), (float('-inf'), None)]:
        if expected is None and not native:
            expected = rows[0]
        assert lookup.anchor(at) is expected
    assert FallbackTurnLookup([], {}, native_messages=native).anchor(20) is None


@pytest.mark.parametrize('reverse', [False, True])
def test_fallback_turn_keeps_first_saved_match_across_anchor_and_input(reverse):
    anchor = {'id': 'user', 'inputId': 'input'}
    rows = [{'id': 'input-match', 'anchorMessageId': 'older-user', 'inputId': 'input'},
            {'id': 'anchor-match', 'anchorMessageId': 'user', 'inputId': 'different'},
            {'id': 'later-match', 'anchorMessageId': 'user', 'inputId': 'input'}]
    if reverse:
        rows.reverse()
    turns = {row['id']: row for row in rows}
    lookup = FallbackTurnLookup([], turns, native_messages=False)
    assert lookup.turn(anchor) is rows[0]
    assert lookup.turn(None) is None
    assert lookup.turn({'id': 'missing', 'inputId': ''}) is None
    appended = {'id': 'new-native-turn', 'anchorMessageId': 'new-user'}
    lookup.ensure(appended['id'], appended)
    lookup.ensure(appended['id'], {'id': appended['id'], 'anchorMessageId': 'wrong'})
    assert lookup.turn({'id': 'new-user'}) is appended
    assert lookup.turn({'id': 'wrong'}) is None


def test_fallback_turn_retains_inspection_of_unhashable_legacy_identifiers():
    old = {'id': 'old', 'anchorMessageId': ['legacy', 'anchor']}
    lookup = FallbackTurnLookup([], {'old': old}, native_messages=False)
    assert lookup.turn({'id': ['legacy', 'anchor']}) is old
    added = {'id': 'added', 'inputId': {'legacy': 'input'}}
    lookup.ensure('added', added)
    assert lookup.turn({'id': 'different', 'inputId': {'legacy': 'input'}}) is added


@pytest.mark.parametrize('native_window', [False, True])
def test_event_join_preserves_fallback_turn_order_and_reuses_new_turns(tmp_path, monkeypatch, native_window):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'home'))
    session = {'id':'app', 'runtimeSessionId':'native', 'workspace':str(tmp_path/'project'),
               'messages':[{'id':'later-time', 'role':'user', 'createdAt':30},
                           {'id':'last-position', 'role':'user', 'createdAt':10, 'inputId':'input'}],
               'workers':[], 'execution': {'nodes': [], 'turns': [
                   {'id':'first-by-input', 'inputId':'input', 'anchorMessageId':'other'},
                   {'id':'second-by-anchor', 'anchorMessageId':'last-position'}]}}
    if native_window:
        session['messages'].append({'id':'native-page', 'role':'assistant', 'nativeIndex':1})
    path = event_path(session, 'native')
    path.parent.mkdir(parents=True)
    events = [('early', 5), ('middle', 20), ('late', 35), ('late-again', 36)]
    path.write_text(''.join(json.dumps({'event':'tool:post', 'timestamp':at,
        'data':{'session_id':'native', 'tool_call_id':call, 'tool_name':'bash'}})+'\n' for call, at in events))
    tree = EventLogView(None).read(session)
    by_call = {row['toolCallId']:row for row in tree['nodes']}
    assert by_call['early']['turnId'] == 'native-turn:' + ('native' if native_window else 'later-time')
    assert [by_call[key]['turnId'] for key in ('middle', 'late', 'late-again')] == ['first-by-input'] * 3
    assert len(tree['turns']) == 3
    assert next(row for row in tree['turns'] if row['id']=='first-by-input')['endedAt'] == 36
    # A subsequent read preserves grouping. An exact saved tool/turn match no
    # longer needs the first-read unassociated-page placement marker.
    session['execution'] = tree
    expected = copy.deepcopy(tree)
    if native_window:
        expected['nodes'][0].pop('anchorMessageId')
    assert EventLogView(None).read(session) == expected


def test_many_unassociated_events_do_not_rescan_every_user_or_turn(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'home'))
    reads = {'createdAt':0, 'anchorMessageId':0}
    class Metadata(dict):
        def get(self, key, default=None):
            if key in reads:
                reads[key] += 1
            return super().get(key, default)
    size = 240
    session = {'id':'app', 'runtimeSessionId':'native', 'workspace':str(tmp_path/'project'),
               'messages':[Metadata(id=f'user-{i}', role='user', createdAt=i) for i in range(size)],
               'workers':[], 'execution':{'nodes':[], 'turns':[
                   Metadata(id=f'turn-{i}', anchorMessageId=f'user-{i}') for i in range(size)]}}
    path = event_path(session, 'native')
    path.parent.mkdir(parents=True)
    path.write_text(''.join(json.dumps({'event':'tool:post', 'timestamp':size + i,
        'data':{'session_id':'native', 'tool_call_id':str(i), 'tool_name':'bash'}})+'\n' for i in range(size)))
    tree = EventLogView(None).read(session)
    assert len(tree['nodes']) == size
    assert {row['turnId'] for row in tree['nodes']} == {f'turn-{size-1}'}
    assert reads['createdAt'] <= size * 2
    assert reads['anchorMessageId'] <= size * 4
