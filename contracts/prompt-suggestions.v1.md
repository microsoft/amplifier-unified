# App-level prompt suggestions — v1

Owner: Unified host; consumers: bundled clients. This opt-in utility is separate
from every conversation participant and execution runtime.

## Behavior

- App-global Settings selects an enabled saved connection, explicit model and
  optional reasoning effort, with an editable prompt under Advanced and an
  explicit Reset to default. Effort `null` means the provider's own default:
  neither a chat selection nor saved connection reasoning/thinking overrides
  are silently inherited. Explicit effort uses the portable `reasoning_effort`
  request field and ordinary provider call option, not a host model-name map.
  No selection, ambient credential, chat model, routing role or provider failure
  authorizes a fallback. Saving does not start a conversation or make a model call.
- An interested browser may request after a new completed assistant display tail
  becomes idle, or when visiting/refocusing an eligible displayed chat with an
  empty composer, including external and shared multi-agent conversations.
  Client interest/visibility owns those triggers; the host proves cookie-backed
  selected-chat authority, not the document's physical visibility. Reconnect and
  status reads never start inference. Request/source deduplication still forbids
  replay of a prior attempt merely because a chat is revisited. Ordinary composing remains
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
  requests 512 output tokens and is subject to a 15-second overall host deadline
  including preparation. The provider owns its actual thinking/output budget:
  some implementations may raise it for reasoning effort. The host request is
  not an attested provider-output or billing cap. Higher reasoning effort can
  increase tokens and cost; the host adds no automatic budget scaling.
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
  and refusal fallback. Opaque `extra_request_params`, `extra_body` and `raw`
  overrides are stripped for this isolated utility only, without changing saved
  connection credentials, endpoint or transport headers. Provider/SDK-specific
  one-wire-attempt enforcement still requires qualification of that installed
  implementation; arbitrary third-party providers are not attested by this host.
  No host repair, retry or fallback call
  occurs after malformed, empty, cancelled, timed-out or uncertain output.
- Model output is exactly `{"suggestion": string|null}`. A string is nonempty,
  single-line, at most 240 Unicode code points, without controls or surrounding
  whitespace. Additional/duplicate fields, Markdown wrappers, malformed JSON and
  tool/action/unknown blocks are rejected, not repaired. Known private
  thinking/redacted-thinking/reasoning blocks are ignored without reading or
  projecting their payload; strict parsing still applies to the public text,
  and reasoning-only responses are unavailable output, not invented text.
- The saved prompt is literal instruction text (no format substitutions), defaults
  to the original suggestion instruction, and must be nonblank and at most 16 KiB
  of UTF-8, allowing newlines/tabs but not other control characters or invalid
  Unicode surrogates. A separate fixed output/privacy/no-tools contract follows it in the
  system message. Editing instructions grants no tools, hidden context, recipient
  change or send authority; strict parsing, the host's 512-token output request
  and 15-second deadline remain unchanged. Provider-owned reasoning may require
  more tokens than requested; no bounded-billing or universal output-cap promise,
  host budget increase, repair call or fallback is implied.

## Wire

`providers.suggestionModels {}` returns
`{suggestionModels:{enabled,provider,model,effort,prompt,defaultPrompt,transcriptMode,humanTurns,revision,
takesEffect:"next_request"},providers:[existing nonsecret connection rows]}`.
Each provider row includes `sharedCatalogKey`, derived by the ordinary discovery
catalog identity owner from that app-global connection and qualified runtime.
Clients may hydrate cached choices only by this exact key, never by connection
ID alone or a workspace override/mounted fork. Reading this roster does not
discover models, prepare a runtime or change settings.

`providers.configureSuggestions {enabled?,provider?,model?,effort?,prompt?,transcriptMode?,
humanTurns?,scope:"global",configurationTarget:{kind:"global"}}` saves the same
selection and returns `suggestionModels`, `configurationChanged:true`,
`takesEffect:"next_request"` and the updated `configurationRevision`.
Existing configuration authority applies. No setting changes a chat's model.
`effort` is null or the ordinary provider effort identifier; existing model catalog
metadata supplies choices. Cached choices, when available, use the same validation
as ordinary model selection without a save-time discovery or model call. Every
omitted preference preserves its saved/effective value (or its default for old
settings). Independent prompt-only/reset or model-only forms therefore do not
disable the utility, clear its account/model or reset its transcript window.
The complete merged value is validated before one atomic settings write;
invalid partial edits change neither settings nor the configuration revision.
`defaultPrompt` is read-only metadata; Reset sends its exact value as `prompt`.
Effort/prompt edits change the settings/configuration witness and invalidate pending
or ready candidates like other app configuration edits; no existing paid claim is
rewritten or retried.

`session.suggestion.request {sessionId,requestId,expectedLastMessageId}` returns
promptly with `pending` or a typed terminal result.
`session.suggestion.status|cancel {sessionId,requestId}` targets that exact private
request. All return `{requestId,sessionId,status,suggestion,sourceKey,
configRevision,usage,cost,reason,expiresAt}`; unavailable fields are null.
Request/status require that browser to display the intended chat. Cancellation
alone may target its exact authenticated principal/device/client/session/request
owner after navigation selects another chat. A foreign owner or unknown request
gets only an empty `expired` receipt and cannot cancel another request, start a
call, expose a candidate or create a new claim. It does not bypass authentication.
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

Failed utility calls may carry a static allowlisted `reason:"<stage>_<category>"`.
Stages are
`construction` (including import/configuration/metadata), `transport` (inside the
provider call; not proof that a network request occurred), `parse` (JSON syntax)
and `output` (response blocks/schema/usage). Categories come from closed host
exception-class/status allowlists (authentication, permission, rate_limit,
not_found, invalid_request, timeout, connection, service, configuration,
missing_dependency, value_error, type_error, internal, invalid_output).
Unknown exception classes yield only `provider_failed`; there is no arbitrary
diagnostic dictionary or error-type passthrough. No SDK error prose,
body, endpoint, module name, prompt or credential is copied. `not_found` does not
assert a missing model rather than a missing endpoint. These facts share candidate
ownership/expiry and never enter shared state, action status or durable claims.
Earlier failed receipts without diagnostics remain unattributed. No failure
authorizes a retry, fallback or assumption of zero provider cost.
Currentness refusal uses only a first-failed-gate reason: `current_closed`,
`current_auth`, `current_epoch`, `current_audience`, `current_selected`,
`current_config`, `current_session`, `current_archived`, `current_deleting`,
`current_eligibility`, `current_busy` or `current_sourcewitness`. Exceptions at an
existing gate report that gate, without error text or values. A changed capture
key reports `capture_source_changed`; explicit invalidation without a failed gate
reports `current_invalidated`. These observations do not change gate order,
authorization, status, deduplication or retries. A denied final transport grant
retains its first observed dimension even if a later check would succeed.
When the total source witness fails, admission-time private component
fingerprints identify the first mismatch using a finite `current_source_` suffix:
`owner_native`, `runtime_session`, `created`, `status`, `stop`, `archive`,
`deleting`, `storage_mode`, `cold_resource`, `cold_stamp`, `rows_count`, `rows_id`,
`rows_role`, `rows_text`, `rows_actor` or `rows_label`. Unknown/unavailable
breakdowns retain `current_sourcewitness`. Sixteen fixed-size fingerprints borrow
the same bounded projection already used by the total witness, without another
source read; they do not replace or relax that witness. They live only in the
bounded private request record and are removed at expiry. No fingerprint values,
row values or arbitrary component keys enter wire/shared state/durable claims.

## Privacy, freshness and cost admission

The authenticated HTTP adapter supplies a live cookie-backed principal alongside
device-owner and client identities; a bearer credential plus a spoofed `web`
registration does not authorize private suggestions. Callers cannot supply a
principal or audience. Requesting/reading a suggestion requires displaying the
intended conversation; withdrawing an exact owned request after navigation does
not require continuing to display it. Today's host has one authenticated user:
this is not a new multi-user ACL system. A future multi-user host must bind these operations to
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
Freshness binds the bounded public model input, not a generic presentation-cache
fence, raw resident row count or idle/ready spelling. Native primary/backup/append
stamps invalidate reuse and require a fresh bounded public-tail projection; their
change alone does not invalidate identical public input. The existing bounded
page cache avoids rereading unchanged files on status polls. Real public appends,
same-ID edits, provenance/acceptance changes and owner/Stop changes remain fenced;
no whole-history hydration, event-log scan or provider retry is introduced.
Resident and cold saved messages use the same bounded public projection and
selection in that witness. Cooling or hydrating unchanged saved content therefore
does not invalidate a suggestion. The existing page cache reuses a cold suffix
only for its exact owner, indexed resource and fresh backing-file signature;
a changed identity/signature rereads through `read_saved_window` under its existing
8 MiB raw-read / 4 MiB record limits and 100-row suffix bound, then applies the independent
24 KiB selected-input bound. Missing, malformed or unreadable backing sources
fail closed. No full resource read or new cache owner is introduced.
As with native freshness, a cold cache miss/change performs this bounded read
synchronously at currentness admission; repeated unchanged polls only validate
the signature/cache and bounded projected rows. This is not a latency guarantee
for slow storage and does not change the host's 15-second overall deadline.

The client additionally fences draft/outbox/voice changes and navigation, validates
`configRevision == suggestionModels.revision` and the request's source key, and
invalidates/rereads settings when existing global `configurationRevision` changes.
No candidate is inserted or sent without an explicit user acceptance.

An already-ready suggestion is an uncommitted placeholder/Tab-completion preview,
visually distinct from the person's draft and outside the editable document.
Ordinary typing or pasting hides it without dismissing that exact candidate.
When the composer becomes genuinely empty again, including after clearing an
accepted draft or Undo, an eligible view restores the same still-valid candidate
after an exact status-only check. Leaving and returning to the chat does not
consume a ready candidate hidden by an ordinary draft. Restoration never starts
inference, renews expiry, writes draft text, or retargets a candidate to another
chat. Escape or an explicit dismissal keeps that candidate dismissed.
Existing source, configuration, authentication, Stop, archive, expiry and
eligibility checks still apply; a delayed acceptance cannot insert after the
composer changes. Pending work cancelled while composing or leaving is not
restarted when the box becomes empty.

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