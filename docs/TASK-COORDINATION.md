# Task and worker coordination

## Collaborative increment: admission specification

This scoped implementation follows the approved Canvas v4 direction recorded in
`workspaces/contracts/collaborative-workspaces-decision.md`. Accepted execution
contracts remain unchanged. Shared folders are cooperative, not protected by a
checkout-wide lease.

1. **Authority.** A host-owned grant records `id`, `sourceMessageId`, issuer,
   workspace, participant root IDs, purpose, allowed modes, idle-start and
   task-creation permission, revision and revocation. Only authenticated human
   actions issue/revoke it. The grant action itself retains a real human request
   without waking a model. Membership and model-supplied flags grant nothing.
   Equivalent legacy send/create/worker routes must pass the same gate.
   Children retain their actual runtime identity and cannot borrow root authority.
   Collaboration never permits peer stop or settings/permission changes.
2. **Attributed input.** Store the original text once in the existing target
   message history with `inputOrigin=peer` and a host envelope: sender root/native
   identity, recipient, grant/revision, request/input ID, mode, purpose,
   references and optional reply linkage. The worker renders that immutable
   envelope around the text before model admission. Peer content is scoped task
   input/reference, never a new human instruction or authority.
3. **Delivery.** Notify persists without execution. Queue waits for a real idle
   boundary; ordinary busy `conversation.send` is not a queue adapter. Steer is
   supported only through generation-anchored runtime admission; unsupported is
   explicit with no message or execution side effect. At admission recheck the
   current grant, stop/task revision, budget and native owner. Unknown receipts
   are never replayed. Human control wins over a queued message.
4. **Reply qualification.** A dependency requires the same admitted input and
   request, an actual final-channel result (or an adapter-qualified terminal
   assistant result without tools), and successful root generation termination.
   Commentary, acknowledgement/progress, unrelated turns, failure, defer and
   decline cannot satisfy it. Message IDs remain the result identity; result
   references are not proof of independent artifact verification.
5. **Wait and continuation.** Cursors only acknowledge results. A saved wait
   binds one request and one saved task revision, interruption revision and grant.
   One qualifying response may admit one stable-ID continuation through existing
   worker input admission. Busy senders wait for idle; paused/stopped/revoked or
   exhausted senders stay stopped. Unknown/crashed admission is retained without
   resend. Unsupported runtime qualification/admission is a blocked acceptance
   area, not successful completion.

Discovery filters root summaries before paging. Cold reads target one native
history, return bounded relevant windows and report unavailable history rather
than treating an unloaded projection as empty. Task chats are ordinary roots
with explicit title, creator/request links, same workspace, output namespace and
recorded configuration snapshot. No second transcript, database or scheduler.
Related work and agent actions share this service and preserve selection/drafts.
The on-demand `coordinate-work` skill must be installed and discoverable.

Implementation and validation status is recorded below only after observed
checks. This specification does not assert that the new capabilities exist yet.

## Candidate status: partial, dependency loop blocked

The candidate adds human-issued task grants/revocation, attributed notify and
guarded idle queue admission, ordinary durable task roots with creator/brief/
configuration/output references, Related work controls and the packaged
`coordinate-work` skill. Grant issuance is an explicit human host action in
Related work, not automatic interpretation of a natural-language prompt.
Credentials are not copied into the task configuration; the normal host resolves
them. Output namespaces are guidance, not filesystem enforcement.

The selected loop-live runtime at `bdd76badc58091ec47b55bb92cdf9e6f5bcc8e26`
emits assistant text blocks without channel/result-kind classification.
`generation.finished` emits terminal text and `manager_turn_finished`, not an
attestation that the text is a final result rather than an acknowledgement,
progress, defer or decline. Installed-runtime inspection confirmed this.
`coordination.result` therefore reports `qualificationSupported:false`, and
`coordination.subscribe` explicitly returns unsupported with no execution effect.
No saved automatic dependency continuation is implemented. Peer steer likewise
returns unsupported; ordinary busy input is never relabelled as queued delivery.
This is **not the approved complete cooperation loop**.

| Acceptance area | Verdict | Observed evidence / remaining boundary |
|---|---|---|
| A: discovery, durable roots and Related work | BLOCKED | Root summaries, scoped reads, task/configuration/provenance and submitted-message links exercised; qualified exact result links depend on the missing final-result contract. |
| B: authority, attribution and delivery | BLOCKED | No-grant/current/revoked routes, actual child identity, committed admission fences, stop/task/budget checks and notify/idle queue tested; steer is unsupported and qualified final responses remain unavailable. |
| C: cursor and durable continuation | BLOCKED | Existing passive cursor/restart/reconnect behavior retained; no saved qualifying-response continuation can be enabled with current adapter evidence. |
| D: awareness, compact state and skill | PASS | Bounded affected-root references; no receipt scans on ordinary provider requests. Foundation namespace composition and installed-wheel skill resource checks passed. Explicitly disabled skills behavior remains respected. |
| E: useful cooperation acceptance | BLOCKED | Production-service/Chromium deterministic fixture consults, commissions, corrects, replies, independently reads a candidate and repeats after reconnect. It explicitly records `completeLoop:false`; no live provider efficacy or resource/isolation acceptance is claimed. |

DTU checks on source `d1af720a8d060e85491662cbbe2d4ce5669b6cbd`:
201 focused Python checks passed across collaboration, coordination, service,
runtime, agent controls, builtin behavior composition and native/history queries.
The production frontend build and coordination browser fixture passed.
The frontend unit suite previously passed all 440 checks and is rerun for the
terminal candidate. A built wheel installed into an isolated target contains
`bundle_data/skills/coordinate-work/SKILL.md` and production assets.
The browser uses explicit DTU Chromium flags `--no-zygote --disable-gpu`; default
Chromium launch on that environment is not claimed. Panel opening uses the
shared `view.update` navigation action; grant/message/task controls are rendered
UI interactions.

Peer queue requires an idle-start grant and a supported host admission adapter.
It never takes over a native owner. Unknown admissions and creations retain exact
IDs without replay; created roots record initial-input delivery separately.
User stop epochs, host update pause, task revision/pause and capacity denial are
checked again at admission. Passive `coordination.wait.results` are observations,
not successful dependency outcomes. Cold reads reuse the existing native reader,
which can parse one target's complete transcript for alignment; bounded returned
windows are not a claim of bounded underlying file parsing.

The parent manager owns independent/live checks, Fable review and the unresolved
runtime final-result/dependency-admission contract. No version bump, PR, merge,
release, deployment or teardown is part of this candidate. Generated frontend
assets and the built wheel are retained in the parent DTU, not refreshed in this
source branch; rebuild them before packaging/integration.

The **Tasks and workers** panel under Session details uses the same actions as
the agent app bridge. It watches explicit targets, sends follow-ups, and requests
interruption without selecting a different conversation or changing its composer
draft. There is no new scheduler, worker registry, or implicit delegation of
top-level conversations.

## Shared actions

- `coordination.list {sessionId?, limit?}` returns up to 100 conversation and
  worker summaries. A known conversation can be listed directly. Native saved
  child conversations remain in the existing subagent history view.
- `coordination.wait {targets, waitMs?, maxBytes?}` accepts 1–8 distinct targets.
  A target is `{sessionId, workerId?, afterCursor?}`. Without a cursor it returns
  the current snapshot. With cursors it waits for the first new report, completed
  turn, interruption, failure, pending question, or permission request, up to
  60 seconds. Tool/retry progress does not repeatedly wake it. `waitMs: 0` reads
  immediately. Missing targets return per-target errors.
- `coordination.followup {sessionId, workerId?, text}` uses the ordinary
  `conversation.send` path with draft preservation or `worker.message`, which
  submits an input to that exact live persistent child. A finite or retired
  child cannot silently be recreated. Use a stable action command ID for retries.
- `coordination.interrupt {sessionId, workerId?}` uses the existing conversation
  or worker stop path. It reports a request, never rollback or proof that all
  nested effects stopped.

Read actions neither start runtimes nor select conversations. They do not hold
the host command lock while waiting. Browser waits have their own independent
request path, so sending, navigation and stop controls remain available.

The authenticated app bridge supplies the calling root and actual runtime
identity. Own-root operations remain compatible. Cross-root messaging requires
a current human-issued host grant through `coordination.send`, including the
legacy `conversation.send`/`coordination.followup` aliases. Peer worker control,
stop and settings mutations remain denied to models. A child cannot borrow its
root's peer grant. There is no model-supplied authorization flag.

## Delivery and recovery

Conversation results refer to their existing saved message IDs. Child results
carry a stable child session ID, real parent session ID, a fresh run ID, and a
new report ID for each report. Completing an idle persistent child does not
publish its last report a second time. An idle child is available for follow-up;
it is not evidence that its entire responsibility or saved task is complete.
Saved task IDs and worker operation IDs are separate references, not aliases for
conversation or run identity.

The existing worker record retains 32 report receipts with at most 20,000
characters each. These live beside its lifecycle state in the existing session
view file. They are omitted from ordinary browser progress snapshots. Existing
operation adapters continue projecting the authoritative worker row; no worker
lifecycle is duplicated in another operation journal.

Each wait target returns `nextCursor`, `results`, `cursorGap`, and `hasMore`.
Consume/deduplicate results by ID and persist the cursor only after consumption.
The advanced cursor suppresses already delivered results across reconnects and
host restarts. Retrying an unacknowledged cursor is deliberately repeatable;
transport delivery by itself cannot guarantee exactly-once consumption. Retention
expiry or a rewritten transcript returns an explicit gap. It never replays work.

Text output is bounded to a caller-selected 4–64 KiB across all targets, with at
most 16 receipts per target/page. A truncated result is labelled; `availableBytes`
describes the retained excerpt, not invented original output size. Child source
truncation is preserved separately. The complete available conversation remains
in its existing saved history. The browser keeps up to eight recent excerpts per
watched target and bounded delivery state in tab-local session storage, independent
of the client ID that rotates on reload. It retries only failed reads after a
connection loss, never a follow-up or interruption.

Worker follow-up command receipts share the existing command store. A submission
that loses its acknowledgement remains `unknown`; retrying its command ID returns
that receipt without dispatching again. Unresolved receipts become unknown on
host restart, and persistent idle workers become interrupted because their old
runtime no longer owns them. Reopening the app does not restart them.

Every admitted `conversation.stop` increments the existing session's
`interruptionRevision` and records `lastInterruption {commandId, at, origin}`.
Worker stop does the same in its worker row. Duplicate command IDs do not advance
the revision twice. Scheduled adapters can bind and recheck this revision before
submission; internal runtime parking/reloading does not represent user stop intent.

## Portable boundary

`amplifier_operations.coordination` is a standard-library-only protocol in the
existing operations package. `delivery(snapshot, after_cursor, max_bytes,
max_results)` provides storage-neutral result cursors. `ChangeSignal.wait(read,
wait_ms)` provides bounded event notification and captures its event before
reading, avoiding a read/wait lost wakeup. The host owns storage, authority,
identities and lifecycle. The library does not import the app or run tools.

No loop-live dispatch setting changes. Programmatic dispatch remains opt-in.
No Work composition, dependency source pin, or release version changes.

## Validation

`pytest tests/test_coordination.py` covers multi-target waits, attribution,
attention, output bounds, gaps, cancellation, reconnect, restart, uncertain sends,
command idempotency, and agent scope. `tests/fixtures/standalone_children_probe.py`
also exercises real Foundation/Rust child sessions with a deterministic
orchestrator, including explicit message receipt IDs and report lifecycle.

`npm run test:coordination-browser --prefix frontend` runs an isolated production
service and production assets in Chromium. Its deterministic runtime supplies
worker events; it verifies two watched targets, a follow-up, reconnect,
interruption during a wait, draft/selection preservation, and mobile bounds.
This is actual service/browser coverage, not a claim of a live provider run or
deployed Work bundle acceptance.
