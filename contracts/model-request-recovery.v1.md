# Recovery of an interrupted model request

The host's portable provider boundary may retry an unreturned model request in
its still-running execution. Browser reconnect restores views; it never sends a
new turn or triggers this policy. Other clients observe the same host-owned work.

- Retry only provider-declared retryable timeout/unavailable errors, at most two
  additional attempts with bounded backoff (2 and 5 seconds, respecting provider
  retry-after up to 30 seconds). Provider/SDK internal retry policies remain their
  own responsibility; these bounds count host attempts, not physical API calls.
- Retain the exact prepared request, selected connection/model, options, current
  generation and completed tool results. Never restart the orchestrator, repeat
  a tool or append the human message again. Changed requests or authority decline
  retry. User Stop cancels the current request or its backoff.
- Any yielded streaming event, exposed response/output hook, or tool invocation
  prevents generic retry. Stateful native transports retain their provider-owned
  reconciliation policy. They are never resubmitted through this wrapper.
- Authentication, invalid input, unknown errors, cancellation and exhausted retries
  keep the existing truthful failure/recovery path. Attempt diagnostics contain
  metadata only, never request or response bodies.

This is recovery of a surviving worker after transient transport loss (including
host suspension). It does not infer process death was sleep, automatically start
terminated workers, resume partial output, or replay requests from diagnostic
recordings. A terminated process or provider-owned native request needs a durable
checkpoint/receipt qualified by its execution owner before automatic continuation.
A provider may have performed and billed inference before a response was lost;
retry does not promise exactly-once remote inference or unchanged generated text.

Tests exercise retry exhaustion, Stop, changed authority, partial output,
provider opt-out, exact input retention, and root/child adapter installation with
synthetic providers. Physical sleep and paid inference require separate evidence.

## A changed fitted request before transport

At the prepared dispatch boundary, Streaming may request one new measurement
through the optional provider `refit_request(request, error=...)` capability.
SurfaceProvider returns a budget decision only for its exact retained local
content/binding refusal, before any provider transport began. The existing
explicit budget preparation stabilizes stale visual warnings before measurement.
The loop validates the normal hard-fit decision and sends that newly measured
request once, without repeating a tool, hook, input, turn or context compaction.

Selected-model wrappers preserve this optional capability through the execution
adapter. They retain the exact refused selected request and error for that adapter,
including the measured model, effort and output reservation; a copied request or
changed selection cannot stand in for it. Native regression coverage traverses
the selected-model wrapper as well as the surface adapter and real loop boundary.

The one refit retains its original request-scoped preparation observation instead
of fetching all sections again. Ask refreshes the required Work version through
the canonical currentness check after refit/measurement and before transport.
Full retains its standing authorization to act with incomplete Work assurance;
a refit does not reintroduce that optional gate. The current Full policy is
rechecked at native admission. Each new model request obtains its own
observation. Required Ask currentness reads use the bridge's response deadline
and cancellation; optional context collection's shorter deadline must not cancel
an otherwise valid required check. Stop and input/generation identity are checked
again after the await. An unavailable observation is not evidence of changed
authority and must be reported separately.

The original native generation, complete inputs and Stop revision must remain
valid; Ask also retains its required Work version. Revoked policy, changed native
scope, unavailable required Ask assurance, unknown/sent calls,
ordinary provider errors, any stream event, an unavailable/oversized measurement
or a second refusal remain terminal. This is not the timeout retry policy above.
Local diagnostic reasons distinguish missing/changed binding, changed request
components, changed native scope and changed/unavailable authority; they contain
only fixed reason/component names, never prompt text, image data or request hashes.
Correlated terminal diagnostics retain whether the bridge timed out, failed,
returned malformed data, or reported unavailable required Work. Only a refusal
observed at the adapter's local pretransport boundary may identify the request as
unsent; a provider exception with the same type cannot make that claim.
The recovery event is metadata only and does not prove provider admission.

## Current context and reasoning continuity

Unified prepares current host observations before measurement, without saving a
second provider transcript or retaining superseded authority, memory or pixels.
Ambient observations have a stable position after the latest ordinary accepted
input, before its assistant/tool continuation. Explicitly acquired evidence stays
after its owning complete tool-result batch; it must not appear before acquisition
or split a parallel call/result batch. Focus without an acquiring receipt retains
request-time placement. This placement does not authenticate input or grant authority.

Unchanged current observations reproduce the same provider-visible prefix during
an ordinary tool loop. Known observation-sampling clocks are omitted from prompt
presentation, not from the source records; semantic revisions, expiry, grants,
source identities and fresh private request bindings remain intact. Each request
still reads current context, and existing final revocation/currentness checks and
exact-fit refusal/refit rules still apply.

A real context change, acquired evidence, new input, lost receipt, resume or
compaction may deliberately replace that prefix. The provider owns native
reasoning validity and selective invalidation against its actual system, tools
and message envelope. Unified neither edits signed thinking nor replays old
observations to make it valid. This is not append-only continuity across turns:
necessary invalidation can lose reasoning and incur provider latency/token costs.
Host and provider qualification must distinguish unchanged-prefix preservation
from successful requests that discarded incompatible reasoning.
