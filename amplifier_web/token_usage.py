"""Derive inclusive token counts without changing provider-reported counters."""
import math


def with_gross_tokens(usage):
    """Core input includes cache reads; cache writes are a separate bucket.

    Preserve inputTokens/totalTokens as reported. Gross input includes each
    input bucket once; gross total adds output (which already includes reasoning).
    Derive on read as well as observation so historical receipts need no rewrite.
    Recompute derived fields rather than treating them as a second usage ledger.
    """
    result = {key: value for key, value in usage.items()
              if key not in {'grossInputTokens', 'grossTotalTokens'}}
    def valid(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    writes = usage.get('cacheWriteTokens')
    if writes is None:
        writes = 0  # Optional Core metric; never infer an unreported write count.
    if not valid(writes):
        return result
    input_tokens, output_tokens = usage.get('inputTokens'), usage.get('outputTokens')
    if valid(input_tokens):
        result['grossInputTokens'] = input_tokens + writes
        if valid(output_tokens):
            result['grossTotalTokens'] = input_tokens + writes + output_tokens
    if 'grossTotalTokens' not in result and valid(usage.get('totalTokens')):
        result['grossTotalTokens'] = usage['totalTokens'] + writes
    return result
