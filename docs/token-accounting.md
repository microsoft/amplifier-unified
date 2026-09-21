# Token totals and task budgets

Provider-reported `inputTokens` and `totalTokens` remain unchanged in execution
receipts and capacity views. These are Amplifier Core counters, not necessarily
the provider's original wire counters. Core input includes fresh input and cache
reads; cache writes are reported separately. Reasoning is already part of output.

Unified derives `grossInputTokens = inputTokens + cacheWriteTokens` and
`grossTotalTokens = grossInputTokens + outputTokens`. An absent optional write
counter contributes no additional reported tokens. A receipt reporting only a
total can still contribute `totalTokens + cacheWriteTokens`. Missing totals
remain unknown. Task token limits and their visible totals use `grossTotalTokens`.
Cache reads and reasoning are never added a second time. Reported costs are
preserved; the host does not recalculate prices.

For example, Core input 3, output 7, total 10 and cache writes 12,635 give inclusive
input 12,638 and budget consumption 12,645. The original 3/7/10 counters remain
available beside the derived fields. A cached read of 100 in input 103 is already
counted in that 103.

Derivation runs when reading persisted receipts as well as on new observation.
It does not rewrite history or execute work. Reconnect and cumulative stream
updates retain the existing whole-call identity/revision deduplication. Retired
receipts remain part of lifetime budgets.

Provider-owned native compaction currently exposes only inclusive SDK input,
output and total counters, with no separate write bucket. Those totals pass
through unchanged. Ordinary native completion uses the normalized Core response
mapping. Request/context preflight budgets are distinct from cumulative usage
and are unchanged by this accounting correction. Unreported provider-internal
calls or cache metrics cannot be reconstructed by the host.
