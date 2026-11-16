# App-level prompt suggestions — v1

Owner: Unified host; consumers: bundled clients. This opt-in utility is separate
from every conversation participant and execution runtime.

## Behavior

- App-global Settings selects an enabled saved connection and explicit model.
  No selection, ambient credential, chat model, routing role or provider failure
  authorizes a fallback. Saving does not start a conversation or make a model call.
- An interested browser may request after a new completed assistant display tail
  becomes idle, including external and shared multi-agent conversations. Focus,
  reconnect and status reads never retry generation. Ordinary composing remains
  available when the utility is disabled, busy, unsupported or unsuccessful.
- The host obtains its own authorized display transcript. It never accepts
  transcript text, native model context, private files, system/developer blocks,
  reasoning, tool payloads, recovery/service inputs or unsent drafts from a client.
  Public speaker IDs/labels are data, not instructions or recipient authority.
  `inputActor.kind` is human, agent, system or unknown from canonical saved
  provenance; role=user alone never establishes a human. ACP/shared accepted
  conversation inputs do not require native generation receipts. Native inputs
  retain their own progress checks, exact peer receipt attribution and public
  voice-input projection: spoken requests do not include reference-history envelopes.
- `last_n` defaults to six human messages and the following public conversation
  messages, counting humans across the displayed conversation, not agent-authored
  user-role inputs. A selection is independently bounded to 100 source rows and
  24 KiB of UTF-8 text/labels/identities. `full` requires a complete gap-free
  display window within those bounds; it does not silently degrade to a suffix.
  Individual raw text is limited to 16 KiB before sanitation. The existing
  naming text cleaner removes context/system-reminder envelopes (including
  unclosed envelopes), data URLs and encoded payloads; neither mode sends those.
  Internal/worker/scheduled-only sources and update-activation pauses are ineligible.
- One tool-free utility `provider.complete` call uses the selected account/model,
  512 output tokens and a 15-second overall host deadline including preparation.
  The standard isolated provider-probe constructor is reused, not a second SDK
  route, orchestrator, native cursor, context module or conversation worker.
  Installed providers only: no request-time installation or `Bundle.prepare`.
  Custom source selections without a qualified installed utility binding return
  unsupported rather than loading another implementation.
  A saved branch such as `@main` may match its qualified exact installed commit
  only when the recorded source policy and its provenance agree on repository,
  ref, subdirectory and that installed revision, with the frozen manifest bound
  to the same install. Shared-build commit markers are not branch policy.
  Missing preparation reports `runtime_not_prepared`; mismatched/unproven source
  evidence reports `qualified_source_mismatch`, without Git discovery, receipt
  rewriting, preparation or fallback.
- Utility-local provider configuration disables `max_retries`, overload fallback
  and refusal fallback. Provider/SDK-specific one-wire-attempt enforcement still
  requires qualification of that installed implementation; arbitrary third-party
  providers are not attested by this host. No host repair, retry or fallback call
  occurs after malformed, empty, cancelled, timed-out or uncertain output.
- Model output is exactly `{"suggestion": string|null}`. A string is nonempty,
  single-line, at most 240 Unicode code points, without controls or surrounding
  whitespace. Additional/duplicate fields, Markdown wrappers, malformed JSON and
  nontext/tool blocks are rejected, not repaired.

## Wire

`providers.suggestionModels {}` returns
`{suggestionModels:{enabled,provider,model,transcriptMode,humanTurns,revision,
takesEffect:"next_request"},providers:[existing nonsecret connection rows]}`.

`providers.configureSuggestions {enabled,provider?,model?,transcriptMode,
humanTurns,scope:"global",configurationTarget:{kind:"global"}}` saves the same
selection and returns `suggestionModels`, `configurationChanged:true`,
`takesEffect:"next_request"` and the updated `configurationRevision`.
Existing configuration authority applies. No setting changes a chat's model.

`session.suggestion.request {sessionId,requestId,expectedLastMessageId}` returns
promptly with `pending` or a typed terminal result.
`session.suggestion.status|cancel {sessionId,requestId}` targets that exact private
request. All return `{requestId,sessionId,status,suggestion,sourceKey,
configRevision,usage,cost,reason,expiresAt}`; unavailable fields are null.
`expiresAt` is admission wall time plus 60 seconds, expressed as epoch
**milliseconds**, fixed across pending/ready reads and exact request retries.
Clients use the earlier of that timestamp and their original request-start
60-second deadline, never a new TTL measured from receiving a response.

Statuses: `pending`, `ready`, `empty`, `disabled`, `missing_selection`,
`unsupported`, `stale`, `failed`, `timeout`, `cancelled`, `expired`,
`duplicate_source`, `busy`. Only ready carries candidate text. Usage contains
reported finite token counters only; missing usage and unknown cost stay null.
Cancellation requests process cleanup but never claim to undo provider effects.
The occupied slot remains held until the physical request/cleanup settles.

## Privacy, freshness and cost admission

The authenticated HTTP adapter supplies a live cookie-backed principal alongside
device-owner and client identities; a bearer credential plus a spoofed `web`
registration does not authorize private suggestions. Callers cannot supply a
principal or audience. The requesting browser must display
the intended conversation. Today's host has one authenticated user: this is not a
new multi-user ACL system. A future multi-user host must bind these operations to
its real principal-specific display authorization before enabling the feature.

Candidate bodies live only in a bounded in-memory request owner (128 records),
never shared session state, action status, agent readback, history, native storage
or durable command receipts. Candidates expire 60 seconds after admission, including
without polling. Separate cookie principals cannot read another principal's
candidate. Client IDs provide presentation partitioning, **not a security boundary
between tabs sharing the same login cookie**: that authenticated user can choose
another client ID in an HTTP header. No cross-tab secret isolation is claimed.

Before dispatch, after provider construction and before publishing/reading a
candidate, recheck current client/audience, saved sign-out witness, source owner,
display content/source revision, archive/delete/idle state and the exact model,
connection, credential and settings witness. A same-ID edit is not unchanged
source. The provider process requests a final host grant immediately before its
one completion; changed authority sends no grant. Already sent requests may finish
and be billed even when their result is discarded.

The client additionally fences draft/outbox/voice changes and navigation, validates
`configRevision == suggestionModels.revision` and the request's source key, and
invalidates/rereads settings when existing global `configurationRevision` changes.
No candidate is inserted or sent without an explicit user acceptance.

Content-free durable request/source hashes are claimed before inference, preventing
same-request/source replay after reconnect or restart. Each claim retains two
fixed-size SHA-256 hashes in indexed SQLite columns, not an in-memory journal.
An exact-request tombstone commits before bounded capture. After capture, fresh
authorization/currentness is rechecked under admission exclusion and the paid
source claim commits from the exact bounded canonical model input plus
principal/conversation/configuration identity, before provider dispatch. Resident,
native and cold-storage stamps remain freshness checks, not paid identity:
unchanged captured input cannot pay again merely because its storage retired.
Duplicate input can therefore report `pending` while capture runs, then
`duplicate_source` without inference.
Storage grows with distinct admitted requests; there is no lifetime request-count
quota or automatic age-based deletion that could reauthorize an uncertain paid
effect. Candidate memory remains independently bounded to 128 records. Claims
contain neither prompts, candidate bodies nor credentials. This is durable local
deduplication, not exactly-once remote billing or a general job queue.

## Qualification boundary

Focused offline regressions cover private request ownership, source/config/sign-out
races, request deduplication, cancellation occupancy, strict output, public native
block filtering and bounded history work. Parent integration owns real installed
provider, DTU, browser journey and independent review evidence. Passing source
tests alone is not live model, future multi-user, browser or release qualification.