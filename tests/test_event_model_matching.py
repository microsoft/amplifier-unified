import copy

import pytest

from amplifier_web import event_log_view


def model(identity, end, *, host=False, session='root', **fields):
    return {'id': identity, 'kind': 'llm', 'sessionId': session, 'model': 'fixture',
            'provider': 'test', 'startedAt': -10, 'endedAt': end, 'phase': 'running',
            **({'_appModel': True} if host else {}), **fields}


@pytest.mark.parametrize('host_end,native_end,merged', [
    (12, 13, False), (12, 11, False), (12, 12.999, True), (12, 11.001, True),
    (-1.5, -1, True), (-1.5, -.5, False), (0, None, True), (None, 0, True),
    (0, .5, True), (.5, 0, True), (None, .5, False), (None, None, True),
    ('bad', 12, False), (12, 'bad', False), ('', None, True),
    (float('inf'), float('inf'), False), (float('-inf'), float('-inf'), False),
    (float('nan'), 12, False), (12, float('nan'), False),
    (10**400, 10**400, True),
])
def test_time_prefilter_preserves_pending_and_strict_terminal_boundaries(host_end, native_end, merged):
    rows = [model('host', host_end, host=True), model('native', native_end)]
    assert [row['id'] for row in event_log_view.merge_model_observations(rows)] == (
        ['host'] if merged else ['host', 'native'])


def test_equal_distance_ties_keep_original_host_and_native_order():
    # Terminal candidates are indexed by time; their original order is still
    # authoritative when two host observations are equally close.
    rows = [model('host-first', 12.25, host=True), model('host-second', 11.75, host=True),
            model('native-first', 12, requestInfo={'source': 'first'}),
            model('native-second', 12, requestInfo={'source': 'second'})]
    result = event_log_view.merge_model_observations(rows)
    assert [row['id'] for row in result] == ['host-first', 'host-second']
    assert [row['requestInfo']['source'] for row in result] == ['first', 'second']


def test_aliases_do_not_absorb_child_calls_or_change_model_and_usage_matches():
    rows = [model('host', 12, host=True, session='app', usage={'inputTokens': 4}),
            model('child', 12, session='child'),
            model('different-model', 12, session='native', model='other'),
            model('different-provider', 12, session='native', provider='other'),
            model('different-usage', 12, session='native', usage={'inputTokens': 9}),
            model('native', 12, session='native', usage={'inputTokens': 4},
                  requestDetail={'id': 'native', 'field': 'request'})]
    result = event_log_view.merge_model_observations(copy.deepcopy(rows), aliases={'app', 'native'})
    assert [row['id'] for row in result] == ['host', 'child', 'different-model',
                                          'different-provider', 'different-usage']
    assert result[0]['requestDetail']['id'] == 'host'
    assert rows[0].get('requestDetail') is None
    # No alias declaration means no cross-session coalescing.
    assert len(event_log_view.merge_model_observations(copy.deepcopy(rows))) == len(rows)


def test_historical_calls_do_not_require_quadratic_identity_comparisons(monkeypatch):
    original = event_log_view.same_model_call
    comparisons = 0
    def compare(observed, native):
        nonlocal comparisons
        comparisons += 1
        return original(observed, native)
    monkeypatch.setattr(event_log_view, 'same_model_call', compare)
    rows = []
    for index in range(400):
        rows.extend([model(f'host-{index}', index * 10, host=True),
                     model(f'native-{index}', index * 10 + .25,
                           requestInfo={'sequence': index})])
    result = event_log_view.merge_model_observations(rows)
    assert [row['id'] for row in result] == [f'host-{index}' for index in range(400)]
    assert [row['requestInfo']['sequence'] for row in result] == list(range(400))
    assert comparisons <= 800
