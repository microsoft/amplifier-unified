# Message delivery and editing

The browser clears the composer and shows a user bubble immediately. The draft being sent is captured independently from subsequent typing. Request IDs identify delivery across disconnects and browser reloads; they do not depend on the current presentation client ID.

A server-side bubble can exist before the worker accepts input. `conversation.send` receipts and messages therefore distinguish `sending`, `accepted`, and `unknown`. Duplicate requests return the current recorded outcome without executing again. A lost reply shows **Check delivery**, using exactly the original ID and payload. A definite rejection shows **Retry**, which starts a new admission attempt. An unsent message can be edited before retry. Reload never resubmits automatically. A browser tab keeps its pending payloads in session storage; this is not a cross-device drafts store.

The new browser clears its scoped draft before submitting and sends `preserveDraft: true`, so a late acknowledgement cannot clear a newer draft containing the same text. Older clients keep their existing draft behavior. Message delivery and view changes have separate queues so a pending admission does not block typing or navigation.

`message.edit` accepts `mode: current | fork`. The UI defaults to the current conversation, including the last user message. An explicit checkbox creates a new conversation instead. Omitted mode retains the legacy fork behavior for existing callers.

Per-turn Fork uses `session.fork` with `afterMessageId`, the exact saved user
message anchoring that turn. A bounded message page does not invent a global turn
number. Only the explicit command loads canonical history and resolves that ID;
missing, duplicate, non-user or display-only anchors are refused. The existing
ordinal `turn` API remains available but cannot be combined with a message anchor.
Native display-only observations do not gain fork authority. A fork preserves the
canonical context through the chosen turn without replaying work or changing the
original conversation.

Current-conversation edits require idle execution and exclusive Foundation ownership. The worker checks pending inputs, interactions and delegated work, prepares a reliable transcript boundary, records revision evidence, replaces the active context and submits the edited input once. The original event log and pre-edit context evidence remain available. External tool effects are never undone or replayed. Earlier message identities and execution records remain in the active conversation. Older compacted history without a reliable boundary returns an error and leaves the original intact.

Prepared revision evidence is private and durable. After interruption, recovery classifies the saved transcript; it never overwrites a newer owner's changes or automatically retries the edited input. A committed edit with an unconfirmed response is shown as interrupted for review.

Validation covers delayed admission and identical newer drafts, duplicate delivery across clients and restart, uncertain results without replay, failed local edits, current/fork selection, preserved earlier tool context, rollback and interrupted edits. `RUN_HISTORY_EDIT_WORKER=1 pytest tests/test_history_edit_worker.py` additionally runs the actual worker with a local fixture provider through edit, parking and restart. It uses isolated session files and does not contact a model provider.

## Ordinary messages follow the conversation

The composer sends one ordinary message with its stable input identity and payload,
not a browser-observed generation or a route. The host forwards it unclassified
through the existing native inbox. After quote preparation, the native owner uses
live provider steering when supported and actually ready (mid-step); otherwise it
inserts the input at the next model-request boundary within the current turn
(mid-turn). Idle input begins a new turn. Earlier boundary-pending input cannot be
overtaken by later live input. No additional mailbox, delivery queue or poller is
introduced.

The browser captures `expectedInterruptionRevision` from this conversation when
the person presses Send, before upload/checkpoint/network waits. This existing Stop
epoch is a cancellation fence, never a route or generation hint. Fresh admission
after a changed epoch refuses with `input_stopped`; exact duplicate receipts are
returned first. A deliberate new post-Stop message captures the new epoch. Unknown
delivery keeps its original ID, payload and epoch; it never silently refreshes them.
The host and worker also recheck captured admission after preparation awaits.

`expectedGenerationId` remains an explicit strict API: an ended, changed or
unavailable target refuses the input, never converts it into another turn. The
former `allowNextTurn` option and host/worker completion handoff are removed
deliberately. Independent callers must choose ordinary conversation input or strict
anchored input, not infer a route from displayed status.

Saved Next message settings do not interrupt guidance to active work. Where those
choices require idle application, the existing worker's send admission decides
under its lock: active input captures the mounted settings and leaves future
choices saved; idle input returns explicit no-effect configuration readiness, then
the host applies its existing preparation once before first admission. If normal
completion races an active admission, that input may begin the next turn with its
captured mounted settings. This does not claim the saved future choices were
applied. An accepted/unknown input never takes this no-effect path again.

An explicit Retry of a confirmed rejected browser submission uses a new command
identity without a generation. Unknown delivery retains the
original identity and arguments for reconciliation, never an automatic resend.
The UI calls the submitted content a message without classifying its intent.
Historical unknown submissions with removed route options remain inspectable through
passive delivery checks; changing their payload under the old ID is refused, not
treated as fresh work.

Inbox acceptance is not provider acceptance, context insertion, a successor
response, or model obedience. Existing identity-bound receipts distinguish local
boundary queueing, native sent/accepted/pending, applied, held and unknown. A native
False result is a pre-send readiness refusal; a provider-owned pre-acceptance failure
may use its existing boundary insertion. Neither the host nor a reconnect resubmits
an accepted/pending/unknown input. Stop fences waiting preparation and local queued
input; an unconfirmed remote cancellation remains unknown. Ownership, configuration,
destination, source and exact ID/content deduplication checks still apply.

## Portable failure diagnosis

Copy diagnostics uses a versioned, bounded metadata report. It retains the host's
original failure identity and timestamp when correlated native evidence offers a
more specific classification. Unknown native evidence must not replace another
unknown failure. Reports include application/Python/platform versions, generation
and bridge timing, operation identifiers, and log availability/truncation limits.
Bridge timeouts are internal transport failures with uncertain operation outcomes;
they must not be described as provider failures or trigger automatic action replay.
Prompt contents, tool arguments, response bodies and arbitrary SDK exception text
are excluded. Provider recordings remain a separate, explicit diagnostic facility.

HTTP responses expose a request ID and `Server-Timing` handler duration. The
support report includes bounded client and host request timing rings to separate
browser/network elapsed time from host handling. These contain route templates,
status and timing only, never query strings, request arguments or response bodies.
Handler timing cannot measure an event-loop stall before connection acceptance;
a fast health probe between stalls does not rule out intermittent host stalls.

The owning worker rechecks Stop after input preparation; the loop owns the current
turn boundary without relying on host projections. Read-only media discovery
requalifies its entire identity
proof after a transient revision race, with at most three attempts; this does not
relax byte access, mutation or identity-collision checks.
