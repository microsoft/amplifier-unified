"""Independent arithmetic and public metadata oracles for an ordered continuation."""
import copy

from amplifier_web.execution import _append_rollup


METRICS = ('inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens',
           'totalTokens', 'grossInputTokens', 'grossTotalTokens', 'reasoningTokens')


def empty_prefix():
    return {**dict.fromkeys(METRICS, 0), 'metricKnownCalls': dict.fromkeys(METRICS, 0),
            'calls': 0, 'costUsd': 0.0, 'pricedCalls': 0, 'estimatedCalls': 0,
            'unknownCalls': 0, 'tokenUnknownCalls': 0, 'tokenPendingCalls': 0,
            'costPendingCalls': 0, 'costType': 'unavailable'}


def test_continuation_preserves_per_call_rounding_instead_of_adding_suffix_totals():
    huge = float(2 ** 53)
    prefix = empty_prefix()
    for key in ('inputTokens', 'totalTokens', 'grossInputTokens', 'grossTotalTokens'):
        prefix[key] = huge
        prefix['metricKnownCalls'][key] = 1
    prefix.update(calls=1, costUsd=huge, pricedCalls=1, costType='reported',
                  voiceSeconds=huge, voiceSessionCalls=1, voiceKnownCalls=1,
                  voicePendingCalls=0, voiceIncompleteCalls=0)
    suffix = [{'phase': 'completed', 'usage': {
        'inputTokens': 1.0, 'totalTokens': 1.0, 'costUsd': 1.0,
        'voiceSeconds': 1.0, 'usageFinalized': True}} for _ in range(2)]
    original_prefix, original_suffix = copy.deepcopy(prefix), copy.deepcopy(suffix)
    # Binary64 loses each individual +1 at this magnitude. Combining the two
    # suffix values first changes the result, despite identical real arithmetic.
    assert (huge + 1.0) + 1.0 == huge
    assert huge + (1.0 + 1.0) != huge
    result = _append_rollup(prefix, suffix)
    expected = copy.deepcopy(prefix)
    expected.update(calls=3, pricedCalls=3, voiceSessionCalls=3, voiceKnownCalls=3)
    for key in ('inputTokens', 'totalTokens', 'grossInputTokens', 'grossTotalTokens'):
        expected['metricKnownCalls'][key] = 3
    assert result == expected
    assert prefix == original_prefix and suffix == original_suffix
    assert result is not prefix and result['metricKnownCalls'] is not prefix['metricKnownCalls']


def test_nonvoice_continuation_does_not_create_voice_fields():
    result = _append_rollup(empty_prefix(), [{'usage': {'totalTokens': 3, 'costUsd': 0}}])
    expected = empty_prefix()
    expected.update(calls=1, totalTokens=3, grossTotalTokens=3, pricedCalls=1, costType='reported')
    expected['metricKnownCalls'].update(totalTokens=1, grossTotalTokens=1)
    assert result == expected
    assert not any(key.startswith('voice') for key in result)


def test_voice_fields_initialize_once_and_survive_nonvoice_suffix():
    prefix = empty_prefix()
    prefix.update(calls=1, totalTokens=3, grossTotalTokens=3, pricedCalls=1, costType='reported')
    prefix['metricKnownCalls'].update(totalTokens=1, grossTotalTokens=1)
    result = _append_rollup(prefix, [
        {'phase': 'running', 'usage': {'voiceSeconds': 1.25, 'usageFinalized': False}},
        {'phase': 'completed', 'usage': {'voiceSeconds': 2.75, 'usageFinalized': True}},
        {'status': 'idle', 'endedAt': 9, 'usage': {'usageFinalized': False}},
        {'phase': 'completed', 'usage': {'voiceSeconds': True}},
        {'phase': 'completed', 'usage': {'voiceSeconds': float('nan')}},
    ])
    expected = copy.deepcopy(prefix)
    expected.update(calls=6, unknownCalls=5, tokenUnknownCalls=5, tokenPendingCalls=1,
                    costPendingCalls=1, costType='partial', voiceSeconds=4.0,
                    voiceSessionCalls=5, voiceKnownCalls=2, voicePendingCalls=1,
                    voiceIncompleteCalls=3)
    assert result == expected
    assert not any(key.startswith('voice') for key in prefix)
    continued = _append_rollup(result, [{'phase': 'completed', 'usage': {
        'inputTokens': 2, 'outputTokens': 1, 'costUsd': .5, 'costType': 'estimated'}}])
    expected.update(calls=7, inputTokens=2, outputTokens=1, grossInputTokens=2,
                    grossTotalTokens=6, costUsd=.5, pricedCalls=2, estimatedCalls=1)
    expected['metricKnownCalls'].update(inputTokens=1, outputTokens=1, grossInputTokens=1, grossTotalTokens=2)
    assert continued == expected
    assert result['calls'] == 6 and result['metricKnownCalls']['inputTokens'] == 0


def test_continuation_preserves_pending_coverage_reasons_and_detached_prefix():
    # One reported call plus one still-pending unpriced call, specified without
    # using rollup() (which delegates to the same implementation under test).
    prefix = empty_prefix()
    prefix.update(inputTokens=5, outputTokens=2, cacheWriteTokens=3, totalTokens=7,
                  grossInputTokens=8, grossTotalTokens=10, calls=2, costUsd=.25,
                  pricedCalls=1, unknownCalls=1, tokenUnknownCalls=1,
                  tokenPendingCalls=1, costPendingCalls=1, costType='partial',
                  costUnavailableReasons=['old-a', 'old-z'])
    prefix['metricKnownCalls'].update(inputTokens=1, outputTokens=1, cacheWriteTokens=1,
                                      totalTokens=1, grossInputTokens=1, grossTotalTokens=1)
    suffix = [
        {'phase': 'completed', 'usage': {'inputTokens': 3, 'outputTokens': 4, 'cacheReadTokens': 2,
            'costUsd': .5, 'costType': 'estimated', 'costUnavailableReason': 'old-a'}},
        {'phase': 'running', 'endedAt': 9, 'usage': {'costUnavailableReason': 'new-b'}},
        {'phase': 'queued', 'usage': {'totalTokens': 0, 'costUsd': -1, 'costUnavailableReason': 'old-z'}},
        {'status': 'pending', 'usage': {'costUnavailableReason': 7}},
    ]
    original_prefix, original_suffix = copy.deepcopy(prefix), copy.deepcopy(suffix)
    result = _append_rollup(prefix, suffix)
    expected = empty_prefix()
    expected.update(inputTokens=8, outputTokens=6, cacheReadTokens=2, cacheWriteTokens=3,
                    totalTokens=7, grossInputTokens=11, grossTotalTokens=17, calls=6,
                    costUsd=.75, pricedCalls=2, estimatedCalls=1, unknownCalls=4,
                    tokenUnknownCalls=3, tokenPendingCalls=2, costPendingCalls=3,
                    costType='partial', costUnavailableReasons=['new-b', 'old-a', 'old-z'])
    expected['metricKnownCalls'].update(inputTokens=2, outputTokens=2, cacheReadTokens=1,
        cacheWriteTokens=1, totalTokens=2, grossInputTokens=2, grossTotalTokens=3)
    assert result == expected
    assert prefix == original_prefix and suffix == original_suffix
    result['metricKnownCalls']['inputTokens'] = 999
    result['costUnavailableReasons'].append('detached edit')
    assert prefix == original_prefix


def test_cost_type_follows_actual_continued_price_coverage():
    unpriced = _append_rollup(empty_prefix(), [{'phase': 'running', 'usage': {}}])
    assert (unpriced['costType'], unpriced['unknownCalls'], unpriced['costPendingCalls']) == ('unavailable', 1, 1)
    reported = _append_rollup(empty_prefix(), [{'usage': {'totalTokens': 1, 'costUsd': .25}}])
    assert (reported['costType'], reported['costUsd'], reported['pricedCalls']) == ('reported', .25, 1)
    estimated = _append_rollup(reported, [{'usage': {'totalTokens': 2, 'costUsd': .75, 'costType': 'estimated'}}])
    assert (estimated['costType'], estimated['costUsd'], estimated['estimatedCalls']) == ('estimated', 1.0, 1)
    partial = _append_rollup(estimated, [{'phase': 'completed', 'usage': {'totalTokens': 3}}])
    assert (partial['costType'], partial['costUsd'], partial['unknownCalls'], partial['costPendingCalls']) == ('partial', 1.0, 1, 0)
