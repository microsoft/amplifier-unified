# Conversation diagnosis and recovery

`session.inspect {id}` returns the app and runtime IDs, workspace, bundle, current status and a bounded, classified failure. Provider failures are captured at the public provider boundary before loop-live replaces the exception with its no-replay wrapper. Raw SDK requests, exception bodies and credentials are not copied into diagnostics. Older conversations can be diagnosed from their own saved provider error events; a missing cause stays explicitly unknown.

`session.recover {id}` creates an idle independent root conversation. It retains readable user/assistant history, voice references, artifact references and configuration. Native tool calls/results become labelled historical text, and image/provider-specific blocks are excluded from the new model context. The original transcript and UI history remain untouched. Goals, approvals, jobs and ownership are not inherited. No message is sent and no tool work is replayed. Computer-use safety stops remain in effect. Reattach images needed for future work.

Both actions are shared by the UI and agent action surface. Details and copyable IDs remain available under the conversation settings after an error notice is dismissed. Recovery is disabled while work or configuration changes are active.

## Model changes and context-preparation failures

The chat notice and Details & reporting use `failure.summary` and `guidance`.
Unified keeps context failure codes only when they match `[a-z_]{1,80}`. It never
uses an exception message as the explanation. Known codes have fixed guidance;
an unknown fixed code stays in diagnostics with a request to share those details.

Some loop versions omit newer context codes from `generation.failed`. Unified
can supplement the saved failure from the real immediate exception, bound to the
same failed generation and input IDs. It does not rewrite the native event,
infer a cause for older saved failures, or promote a different handled error.
Stop and stale-generation checks still apply.

`model_transition_required` means the selected model needs history preparation
that the participating host did not admit. To continue with the previous model,
the notice advises selecting that model and connection, then sending a new
message. Trying the new model again may rebuild history and use extra model
calls. It is not a promise that the next send will work or begin a rebuild.
If it fails again, copy the chat diagnostics from Details & reporting and
report the problem. Do not delete the checkpoint or reset a preparation record.

The exact-count transition path and the uncounted ordinary path are different:
the pinned streaming loop invokes `context.measured_request_view` only when the
provider advertises an exact counter. The pinned context module invokes the
host's journaled transition entry there, but not from `context.request_retention`.
An uncounted warm model change with an older checkpoint that cannot be safely
carried can therefore still refuse. Handling that route requires coordinated
context/loop support, not a synthetic exact count or removal of the host's paid
call guards. This diagnostic change does not claim to repair that limitation.

Offline tests use the real runtime manager and worker subprocesses with
synthetic model replies. At the failure notice, a send can still reach the
closing worker and be refused. After that process exits, a fresh send starts a
new worker. The tested history then completes with 12 summary calls and one
foreground reply. Saving and reopening the app state, or retiring the source
worker before the change, also retains the chosen target and admits only the
new input. These results do not promise success for a different history.

Merely restarting on every change is not a safe repair. On a connection without
an exact request counter, new summary work in this cross-model rebuild is not
saved between workers. An interrupted rebuild's next worker sent the same
request as an earlier completed summary call. The old saved summary remained
intact. The synthetic successful reply lacks the provider's completed-response
marker needed to publish an eligible rebuilt summary; it does not establish
whether a live successful reply would save that final summary. This change adds
neither automatic resend nor durable continuation of unfinished rebuild work.

`summary_work_paused` has two distinct meanings. The loop's notice reports this
attempt's limit of model calls. The original history and saved summary are kept,
but some new summary work may need to be done again. A larger synthetic
uncounted rebuild reached the default limit of 16 summary calls with no
foreground reply and no saved transition record. The limit does not guarantee
that a later attempt can finish. Copy the chat diagnostics from Details &
reporting and report the problem before trying again.

The host's distinct `ContextRecoveryError` reports a saved native transition
with an unconfirmed call and an offered continuation, even before the limit is
used. Its notice still says “Review preparation”; that control checks the
current offer before the person can approve more calls. These text changes do
not repair the model change itself or grant more calls.

The pinned live loop ends execution after a failed manager turn, including a
context-preparation refusal. Unified preserves the cause and closes that worker;
the next message can need a cold start. This change does not restart the loop,
reuse its closed runtime, or automatically send the failed input again.

## Bundle ID casing and legacy Work chats

New standalone bundle registrations use lowercase IDs with dashes, separate from
display labels such as **Work**. For a bare registered ID, an exact match wins;
a unique case-insensitive match is accepted when there is no exact registration
or local bundle. Ambiguous matches require an exact ID. Paths, URLs and existing
custom aliases are not rewritten.

`maintenance.canonicalizeBundleReferences {apply:false}` previews the explicit
legacy `Work` → `work` repair. `{apply:true}` changes only root-chat bundle
metadata and its Unified view, without mounting workers, sending messages,
replacing instructions or resetting model choices, controls or customizations.
Archived, unopened and zero-turn roots are included. Active writers are deferred;
missing/corrupt metadata, local bundles and conflicting aliases are reported
instead of silently substituted. Inspect `maintenance.bundleReferences` for
per-session outcomes; partial application is not completion. An interrupted
repair can be explicitly repeated to reconcile already-canonical metadata.

Preparation failures before a worker exists now settle runtime-controls startup
as an error, retain a private startup receipt, and classify bundle-resolution
failures with actionable guidance. Background preparation records `unavailable`
without changing a previous turn's failure or replaying its input.

## Context Intelligence library assessment

Reviewed Microsoft `amplifier-bundle-context-intelligence` at `9d9759d6357ffc99fdbfb9e040136996b6c4ca9c`:

- `context_intelligence.read_native_transcript(CaptureLocator, TranscriptRequest)` reads bounded, lossless pages of captured prompt/response text, with explicit partial/error states. Use it for recovering conversation text when the canonical transcript is missing; do not implement another parser for those events.
- `extract_transcript`, `extract_events`, `extract_metadata`, and `discover_sessions` reconstruct session history from a configured CI server. They need server access and captured content. Reconstruction must remain a separate explicit operation with source/completeness evidence.
- `context-intelligence-recover` durably delivers native captures that have not reached a configured server. It repairs telemetry delivery, not a model's conversation context.

The reported Spark failure has an intact canonical transcript. CI's local reader was exercised against a private capture copy: it returned five captured conversational messages, while the canonical context had 65 rows including tool exchanges. Reconstructing or re-uploading those records would not repair a provider that encodes a computer-tool halt message as image bytes. Unified therefore uses Foundation's existing `SessionHistoryStore` for this recovery copy, retains the original evidence, and supplies only the host-specific portable-context policy. No CI networking or telemetry replay is triggered by inspection or recovery.

## Known limit

This change makes failed sessions diagnosable and provides a safe continuation path. It does not change the OpenAI provider's native computer-result encoder. That encoder must separately reject non-image strings and preserve safety-stop/error semantics without manufacturing a screenshot. A new computer failure can therefore still stop a future turn until the provider/computer-use integration is corrected.

## Update source warnings

Unregistered bundle names on unselected imported history appear under older conversation settings, with workspace, native session ID and an Open conversation action. They do not raise an application configuration error or disable cached-source updates. A current selected conversation, enabled app bundle, module source or unreadable settings file still produces an actionable warning. All original bundle choices and transcripts are kept.
