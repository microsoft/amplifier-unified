from types import SimpleNamespace

from amplifier_web.context_continuity import ContextContinuity
from amplifier_web.next_message_impact import LARGE_INPUT, MODEL_SCOPE, TOOLS_PREFIX, capture_basis, impact, observation

CHOICES = [{'id': alias, 'provider': family, 'promptCacheTraits': [MODEL_SCOPE, TOOLS_PREFIX]}
           for alias, family in [('work', 'openai'), ('alias', 'openai'), ('claude', 'anthropic')]]


def test_large_completed_request_shares_only_known_cache_scope():
    raw = object()
    coordinator = SimpleNamespace(get=lambda key: {'work': raw}, get_capability=lambda key: None)
    basis = capture_basis(coordinator, raw, {'capabilities': [MODEL_SCOPE, TOOLS_PREFIX]}, 'sol')
    receipt = {'model': 'sol', 'provider': 'openai', 'usage': {'inputTokens': LARGE_INPUT}, 'promptCostBasis': basis}
    assert impact(observation(receipt), CHOICES) == {
        'source': {'instance': 'work', 'model': 'sol'}, 'inputTokens': LARGE_INPUT,
        'coldConnections': ['claude'], 'coldModels': True, 'toolChangesInvalidatePrefix': True}
    assert impact(observation({**receipt, 'usage': {'inputTokens': 11}}), CHOICES) is None
    assert impact(observation({**receipt, 'promptCostBasis': {}}), CHOICES) is None  # ambiguous archived account


def test_portable_history_notice_is_specific_to_independent_vendor():
    receipt = {'model': 'sol', 'provider': 'openai', 'usage': {'inputTokens': 230},
               'promptCostBasis': {'instance': 'work', 'traits': [MODEL_SCOPE], 'portableHistoryTokens': 90000}}
    assert impact(observation(receipt), CHOICES) == {'source': {'instance': 'work', 'model': 'sol'}, 'inputTokens': 230,
                                        'portableHistoryTokens': 90000, 'coldConnections': ['claude']}
    assert impact(observation(receipt), CHOICES[:2]) is None


def test_history_measurement_bound_to_installed_native_only_checkpoint():
    identity = {'provider': 'work', 'model': 'sol'}
    record = {'state': 'committed', 'installedCandidate': 'sha', 'verification': {'before': 90000}}
    summary = {'kind': 'native', 'message': {'metadata': {}}}
    c = SimpleNamespace(_accepted_record={'summary': summary, 'identity': identity, 'sha256': 'sha'},
                        _journal={'current': 'a', 'attempts': {'a': record}})
    assert ContextContinuity.native_history_input(c, 'work', 'sol') == 90000
    assert ContextContinuity.native_history_input(c, 'work', 'astra') is None
    record['installedCandidate'] = 'earlier'
    assert ContextContinuity.native_history_input(c, 'work', 'sol') is None
    record['installedCandidate'] = 'sha'
    summary['message']['metadata']['context-managed:portable-summary'] = True
    assert ContextContinuity.native_history_input(c, 'work', 'sol') is None
