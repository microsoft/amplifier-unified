"""Inclusive budgets over normalized Core usage, without paid provider calls."""
import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import Usage

from amplifier_web.capacity import evaluate, usage_snapshot
from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents, public_usage
from amplifier_web.token_usage import with_gross_tokens


@pytest.mark.parametrize('input_tokens,reads,writes,output_tokens,total_tokens,gross_input,gross_total', [
    (3, 0, 12635, 7, 10, 12638, 12645),  # Recorded live normalized counters.
    (103, 100, 500, 7, 110, 603, 610),  # Reads are already part of Core input.
    (103, 100, None, 7, 110, 103, 110),
    (12638, None, None, 7, 12645, 12638, 12645),  # Native compact's inclusive counters.
])
def test_reported_counters_preserved_and_gross_counts_each_bucket_once(
    input_tokens, reads, writes, output_tokens, total_tokens, gross_input, gross_total,
):
    reported = Usage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens,
                     cache_read_tokens=reads, cache_write_tokens=writes, reasoning_tokens=5,
                     cost_usd=Decimal('0.0316775'))
    result = public_usage(reported)
    assert result['inputTokens'] == input_tokens and result['totalTokens'] == total_tokens
    assert result['grossInputTokens'] == gross_input and result['grossTotalTokens'] == gross_total
    assert result['costUsd'] == .0316775 and result['costType'] == 'reported'
    assert with_gross_tokens(result) == result  # Reading already-derived evidence is idempotent.
    assert reported.input_tokens == input_tokens


def test_historical_receipts_derive_on_read_without_rewrite_or_reconnect_double_count():
    original = {'id': 'call', 'kind': 'llm', 'phase': 'completed', 'sessionId': 'root',
                'rootSessionId': 'root', 'revision': 2, 'endedAt': 2,
                'usage': {'inputTokens': 3, 'outputTokens': 7, 'totalTokens': 10,
                          'cacheWriteTokens': 12635, 'costUsd': .0316775, 'costType': 'reported'}}
    root = {'id': 'root', 'execution': {'nodes': [copy.deepcopy(original)], 'turns': [],
                                      'retiredUsageNodes': [copy.deepcopy(original)]}}
    saved = copy.deepcopy(root)
    snapshot = usage_snapshot(root)
    assert root == saved
    assert snapshot['calls'] == 1
    assert snapshot['metrics']['totalTokens']['value'] == 10
    assert snapshot['metrics']['grossTotalTokens']['value'] == 12645
    assert snapshot['providers'][0]['metrics']['grossInputTokens']['value'] == 12638
    assert not evaluate({'enabled': True, 'maxTotalTokens': 100}, snapshot)['allowed']
    ingest(root, original)
    ingest(root, {**original, 'revision': 1, 'usage': {'totalTokens': 1}})
    assert usage_snapshot(root)['metrics']['grossTotalTokens']['value'] == 12645
    assert root['execution']['aggregateUsage']['grossTotalTokens'] == 12645


def test_missing_and_invalid_evidence_does_not_become_an_inclusive_zero():
    assert 'grossTotalTokens' not in with_gross_tokens({})
    assert 'grossTotalTokens' not in with_gross_tokens({'totalTokens': float('nan')})
    assert 'grossTotalTokens' not in with_gross_tokens({'totalTokens': 10, 'cacheWriteTokens': True})
    assert with_gross_tokens({'totalTokens': 10, 'cacheWriteTokens': 100})['grossTotalTokens'] == 110
    # Reasoning is included in output; cache reads are included in input.
    assert with_gross_tokens({'inputTokens': 3, 'outputTokens': 7, 'reasoningTokens': 7,
                             'cacheReadTokens': 3})['grossTotalTokens'] == 10


@pytest.mark.parametrize('boundary', ['complete', 'stream', 'native_compact'])
async def test_gross_budget_denies_next_real_boundary_before_provider_invocation(boundary):
    root = {'id': 'root'}
    events = ExecutionEvents('root', lambda event: ingest(root, event['event']))
    async def guard(row):
        if not evaluate({'enabled': True, 'maxTotalTokens': 100}, usage_snapshot(root))['allowed']:
            raise ValueError('budget reached')
    events.admission_guard = guard
    normalized = {'input_tokens': 3, 'output_tokens': 7, 'total_tokens': 10,
                  'cache_write_tokens': 12635, 'cache_read_tokens': 0, 'cost_usd': .0316775}
    class Provider:
        calls = 0
        def get_info(self): return {'id': 'fixture', 'defaults': {'model': 'fixture'}}
        async def complete(self, request):
            self.calls += 1
            return SimpleNamespace(usage=normalized)
        async def stream(self, request):
            self.calls += 1
            yield {'usage': normalized}
            yield {'usage': normalized}  # Cumulative snapshots replace, never add.
        async def compact(self):
            self.calls += 1
            # Provider-owned compact exposes inclusive SDK counters only, with
            # no separate write bucket. Do not normalize or add writes again.
            return {'usage': {'input_tokens': 12638, 'output_tokens': 7, 'total_tokens': 12645}}
    provider = events.instrument_provider('root', Provider())
    request = SimpleNamespace(model='fixture')
    async def invoke():
        if boundary == 'native_compact':
            return await events.provider_call('root', provider, request, provider.compact)
        if boundary == 'stream':
            return [chunk async for chunk in provider.stream(request)]
        return await provider.complete(request)
    await invoke()
    with pytest.raises(ValueError, match='budget reached'):
        await invoke()
    assert provider.calls == 1
    snapshot = usage_snapshot(root)
    assert snapshot['calls'] == 1 and snapshot['metrics']['grossTotalTokens']['value'] == 12645
    assert events.usage()['usage']['grossTotalTokens'] == 12645
    assert root['execution']['aggregateUsage']['grossInputTokens'] == 12638
