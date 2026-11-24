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

## A deliberate model change in an existing conversation

The picker records only the final saved intent. Browsing a conversation's models
uses the local catalog. Explicit Refresh can query that connection's model list.
Neither browsing nor changing a choice admits a turn or prepares history.
For large prior input, the client can describe a known cache or history cost
before a change takes effect. The hint uses completed foreground usage, cached
or mounted provider capabilities and, when available, a verified native-only
history size. Saving requires no extra confirmation. Hints grant no authority.

On a genuinely admitted foreground turn the same preparation path applies to
an already active or newly started worker. Opening the chat, steering and
automatic job or scheduled continuation do not create a fresh recovery grant.
Identity includes the mounted connection, model and applicable options. The
target provider can locally qualify a complete native checkpoint for reuse.
The standalone OpenAI Responses window stays intact across qualified Astra
and Sol switches; the direct Anthropic adapter preserves the signed block
across qualified Claude switches. The source boundary, scope and target fit
must validate before installation. Reuse consumes zero additional compactions.

A completed plain-text summary can also travel with an explicit model change
on the same verified connection when the destination lacks an exact request
counter. Unified binds the saved summary to the connection that produced it.
On a fresh ordinary Send, it checks that binding, the saved source prefix and
configuration, and the selected destination. The source checkpoint stays on
disk while the destination receives the verified note and its retained history.
Only a successful foreground response to the request carrying that note permits
the writer to save it for the new model. The input, selection, source and Stop
state must still match at that point. A later worker can then reopen the new
model's checkpoint without regenerating the covered history.

A failed or interrupted request leaves the original checkpoint available.
Partial notes, opaque provider state and older records lacking proof of their
origin follow their existing recovery paths. A reused account alias alone
cannot establish that two models share the same connection.

When native reuse is unavailable, Managed Context uses original messages that
fit, or a validated readable checkpoint and its recent suffix that fit. If
summarization is needed, it budgets for the target's instructions, tools, output
and intact recent exchanges. The resulting ordinary history summary identifies
older context; original messages remain available. An opaque vendor carrier
never substitutes for readable history on a different provider. A legacy
adapter can use the shared portable preparation path without implementing
a native method. The existing call allowance bounds work when a very large
prefix needs more than one request. Target preparation never falls back to a
different model or a larger cost tier.

The existing recovery journal binds the source checkpoint, canonical prefix,
selection revision, target options, execution input and Stop/configuration
boundaries. Target work is detached from the accepted source state. Native and
supported portable compaction share the configured `summary_max_calls` allowance
(default 16) for the whole transition, with a durable reservation before each
logical paid call. Repeated preparation and restarts do not reset it. Provider
SDK retries retain their own bounded policy: a logical-call bound is not a claim
about physical HTTP requests or exactly-once billing. Completed acknowledged
progress survives; unknown paid calls never automatically replay.

After every base reservation is confirmed as native compaction and its native
checkpoint is saved, an exhausted model transition can offer one additional
allowance for that exact transition.
The existing `context.recovery.status` control returns `allowanceOffer` with an
opaque `offerId`, `expectedInterruptionRevision` and `maxAdditionalCalls` (32).
A fresh ordinary human `conversation.send` can include the original `sessionId`,
that Stop revision and `recoveryAllowance: {offerId, additionalCalls}`. The amount
must be an integer from 1 to 32. The native owner verifies the offer, source
checkpoint and original prefix, selected model and account, tool configuration,
idle admission, and the new Send. It records the authorization with that input
before reserving a paid call. A changed Stop or selection requires a current offer.
Duplicate delivery returns the original receipt. A retry, steering input, peer
message, schedule or later Send cannot use the authorization.

Status includes the number of unresolved logical calls in `unconfirmedCalls`
when that number is positive. If a native transition stopped with one
unconfirmed final reservation and an intact checkpoint for its previous
completed parts, the same approval can resume from that checkpoint. Its
`maxAdditionalCalls` is limited to the unused part of the original allowance.
Unified keeps the uncertain reservation and counts it against that allowance.
The new Send can reprocess the remaining source; the uncertain call's outcome
stays unknown. The bound is single-use and the rest of the original allowance
does not carry over to another Send. Missing progress, an ambiguous install, or
additional unresolved reservations suppress the offer.

The original `maxCalls` stays in the journal. Status reports the additional calls
authorized and used separately. The extension covers native compaction for
that input. Portable summarization keeps its configured allowance.
An opted-in component advertises version 2 via
`context.recovery.additional_call_allowance` and validates the separate grant.
The host keeps the previous confirmed calls and the last written partial result.
Reading status, opening the chat, and restarting consume zero calls.
An uncertain target call without an installation candidate does not prevent
ordinary continuation under the fully revalidated source identity. Its journal
and target-call fence remain intact. Source validation is passive after the
necessary restore; repeated preparation must not reset paid progress, a failure
latch or its retry cooldown. With no source checkpoint, a different safe empty
canonical identity may continue ordinarily without rearming the uncertain target.
A known refused preparation with no call
reservations, no progress and no installation effect may be prepared again only
on a new genuine foreground input under the same binding and cumulative budget.
That effect-free retry may use a newly authorized tool/request contract, retaining
the prior failed preparation's provenance in the existing journal. Paid progress
and uncertain effects cannot use this exception.
A human can request a separate one-Send comparison when a qualified worker
generation moved the host-selected skill cache. Supply `cacheMoveFromGeneration`
to `context.recovery.status`. The worker substitutes that qualified generation's
canonical `foundation/cache/skills` path into only the tool-skills defaults
injected on the current mount. The reconstructed full raw contract must hash to
the saved legacy digest. Both qualified generations must select the same verified
skill package and tracked source contents. The current POSIX verifier accepts
empty cache directories or identical sets of at most 64 empty private lock
files. Links, populated caches and ambiguous evidence suppress this option.

The separate `cacheMoveOffer` supplies the previous and current generation
IDs, both contract digests, the slot count, cache comparison and a
`cacheMoveId`. A new ordinary Send acknowledges that particular change by
including `cacheMove: {cacheMoveId, fromGeneration, toGeneration}` inside
`recoveryAllowance`. The worker verifies the evidence again at admission and
when binding the new input. It retains the complete prior contract digest and
stores the verified move together with the existing one-use grant, before any
additional reservation. The actual worker uses the current cache. Historical
effective contracts record the former physical path without recording whether
a user or the host originally supplied it. This separate choice authorizes the
observed change in either case. A budget-only acknowledgment permits only the
ordinary identical-contract continuation.

An altered earlier canonical prefix still refuses, even when the failed
preparation bought nothing. Request/tool changes do not authorize rewriting the
source bound to a retained attempt; preserve the originals and establish a
compatible validated source rather than retrying that attempt.

Eligible confirmed-progress continuation and exact candidate readback on a
genuinely new authorized foreground input bind the current interruption epoch
after current ownership, selection, contract and canonical-source guards. They
retain prior epoch/provenance and cumulative paid reservations. A newer Stop
counter alone never renews an old input, clears Stop, or permits UNKNOWN replay.

The complete target request must fit under the unchanged allowance. Immediately
before installing its derived checkpoint, recheck source, selection, options,
configuration, execution ownership and Stop. One atomic checkpoint write and
exact digest readback precede activation. A lost installation acknowledgement
requires a new legitimate foreground input and exact candidate-digest
reconciliation; it cannot repeat a paid call or rewrite the candidate.
If original history already fits, report **prepared originals**, not checkpoint
installation: buy no compaction, preserve the old checkpoint inactive, and
activate only the guarded measured target view. Later ordinary target
compaction retains its normal trigger policy.
The settled zero-paid disposition is reusable after cold startup or within a
tool continuation without another selection or new transition campaign.
Current identity, saved choice/account, exact retained checkpoint, canonical
prefix and an empty derived unit must verify; an old identity-only marker cannot
hide changed source/configuration or a foreign active summary/paid partial.
Current ordinary request fitting still applies, with ordinary target compaction
and persistence enabled. An ordinary target failure/cooldown latch without a
derived result is not a foreign derived unit: preserve that latch and let the
ordinary retry policy decide, without rearming the settled transition.

Model-transition preparation does not expand image-input eligibility. In the
selected OpenAI generic-image route, a provider-owned native compaction carrier
prevents stored-image projection, including after target-side native regeneration.
The request uses the existing **Image unavailable** warning and supplies no stored
pixels; canonical history and image references remain intact. Fitting original
history may retain supported image projection. Successful text continuation does
not mean the model saw those images: continue only work that does not need their
pixels, or use a separately supported image workflow. Do not edit opaque native
state or automatically change compaction strategy, model, endpoint or cost tier
to restore images. A checkpoint installation acknowledgement and Foundation's
canonical-image acknowledgement are separate boundaries; neither proves the other.

An invalid same-identity checkpoint may be reconstructed from canonical
originals through ordinary preparation. It is never transition provenance.
Older saved explicit model selections without a revision acquire one stable
revision only after verification of the mounted connection; restoring a default,
an absent selection or an explicit null fork revision does not grant admission.
Bundle conversion must retain that explicit null after resetting bundle controls,
including a later in-place bundle change that keeps the fork's inherited model
pin; account-binding fields from the source execution are not copied as admission.
If an older fork or portable control schema omitted the revision, retained canonical
fork metadata still prevents the inherited pin from being migrated as a legacy
choice. A new ordinary human Send can recover a choice explicitly applied on
that fork when its host selection has a saved connection binding created by a
local choice. Fork creation and transfer do not copy this binding. The idle worker verifies
the selected options and mounted account again and restores one stable revision.
A fork with only an inherited pin or missing account binding still needs a new
explicit selection. This admission
rule does not claim portable transfer of the recovery journal or its paid ledger.
Historical explicit-recovery dispositions are not fresh recovery commands, and
must not skip a separate model-transition check. An effect-free failed or refused
generic recovery may coexist with an eligible new transition; unresolved paid or
installation ownership still fences that transition. An unreadable journal may
allow ordinary fitting from canonical history, never derived-state activation,
journal replacement or renewed transition spending. A selected model alone does
not make that ordinary fallback an explicit recovery or a successful transition.
Missing transition intent tells the person to choose an explicit connection/model
and send a new ordinary message. When the installed Context lacks transition
support, ordinary sends and explicit recovery identify that capability gap. The
user can reselect the previous connection and model or update Managed Context
and send again. The host retains the original checkpoint. A rejected foreign
checkpoint needs a compatible validated source and preserved originals. Generic explicit recovery
is not a salvage path for that rejected foreign provenance, and known rejection
cannot arm a new generic recovery command. Returning to the previous model works
only if its source still validates; choosing a model does not repair corruption
or changed history. Default drift, peer/control input, schedules
and retries do not authorize a new paid transition campaign. This is not general
self-repair for every historical conversation or input route.

Only participating roots declare the host-managed transition policy. Other
Context consumers retain supported ordinary preparation, including one-window
native or existing portable generation when no durable writer participates.
A nonparticipating durable host with only a zero-argument checkpoint writer
cannot atomically retain old and staged target units: fitting originals remains
zero-paid, but oversized replacement is refused before generation with an
actionable persistence limitation. This is not an automatic memory-only
downgrade, native batching fallback, or permission to discard uncertain progress.

An unchanged full fork may carry a prefix-bound accepted checkpoint, but never
the source recovery journal, execution ownership or call reservations. Partial,
readable-recovery and configuration-converted forks retain originals only.
Forking itself runs no provider work. A checkpointless historical fork needs
its own explicit saved target choice and new foreground input for bounded
canonical-originals preparation; it cannot invent a valid old checkpoint.
These are offline-qualified mechanisms, not a claim of live device recovery,
complete archive migration, or model summary quality.

The host publishes the read command ID as `readCommandId` on the `runtime.control` projection of `context.recovery.status`. A client can use it with the command receipt to identify the result when other activity reads the same control.
