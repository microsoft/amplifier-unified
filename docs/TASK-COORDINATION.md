# Task and worker coordination

## Collaborative increment: admission specification

This scoped implementation follows the approved Canvas v4 direction recorded in
`workspaces/contracts/collaborative-workspaces-decision.md`. Accepted execution
contracts remain unchanged. Shared folders are cooperative, not protected by a
checkout-wide lease.

1. **Authority.** A host-owned grant records `id`, `sourceMessageId`, issuer,
   workspace, participant root IDs, purpose, allowed modes, idle-start and
   task-creation permission, revision and revocation. Direct authenticated human
   actions remain available. An agent may propose a grant anchored to its actual
   current delivered human `sourceMessageId`; the host validates transport-bound
   root/generation and retained human provenance, then requests one approval of
   the exact scope through the existing approval surface. It does not synthesize
   human speech or infer authorization from keywords. Only a human may revoke.
   Membership and model-supplied flags grant nothing.
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
4. **Reply qualification.** A recipient explicitly declares `coordination.reply`
   for the exact delivered request during its actual root generation, with
   `kind=result|ack|defer|decline`, outcome, text and artifact/message references.
   The host stages that declaration and seals it only on matching successful
   root termination, delivered (not merely accepted) input IDs, no active jobs,
   and an exact checkpoint-derived native assistant anchor without tools.
   No provider final channel is invented and no prose is classified.
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

## Corrective continuation: adapter loop checked; product acceptance remains open

This is the same lane and objective, preserving attempt 1 below. Independent
runtime inspection established that the missing final, steering and subscription
paths were app-adapter gaps, not demonstrated upstream blockers. The corrective
implementation does not relabel the earlier return or change accepted v1 files.

Implementation assumptions:

1. Retained current human input proves provenance, not semantic scope. The host
   therefore requests one explicit exact-scope approval for agent-mediated grants,
   keeps ordinary tool authorization, and rechecks source digest, workspace,
   root/generation and interruption revision afterward. A source already bound
   to a grant cannot be reused to widen authority under another command ID.
2. A sealed result is an agent claim plus native terminal evidence, never proof
   of artifact correctness. The persistence owner returns the exact canonical
   rows it saved, including system/developer filtering. Terminal IDs and current
   generation message aliases use those rows, not context indexes, prose equality,
   manufactured assistant messages or channel labels.
3. Steering uses the installed version-1 `live.steering` request-boundary submit
   capability under existing admission/ownership locks. Acceptance, applied,
   held and unknown remain distinct; held steering never starts a later turn.
4. One request-specific saved subscription yields one stable continuation ID.
   Its message, result seal and claim are published before dispatch. Existing
   queue admission checks grant/task/stop/owner/budget again. A startup recovery
   pass drains only known unsubmitted queues; submitting becomes unknown and is
   never replayed. Busy waits, and unrelated human pause/block is never cleared.

### Actual checks

Tested implementation source:
`060c2554a9debb8eb5b8466f336fd3769c96a6f5`.
The later evidence-only commit does not change that implementation.
All acceptance runs below used the existing parent-owned DTU; no second
environment, host app mutation, readiness-model replay or DTU teardown occurred.

| Area | Verdict | Evidence and limits |
|---|---|---|
| A: discovery, durable roots and result references | PASS, component/native scope | Ordinary task creation and configuration preserved; native source aliases, cold history and exact canonical terminal IDs checked. Native scripted-provider fixture resolves the result through `coordination.read`. |
| B: authority, attribution and delivery | PASS, component/native scope | Current human-source approval, forged/old/child/peer/generated/source-reuse rejection; actual native provider requests receive host attribution; anchored steering applies without cancellation. Stop/revision/revocation/owner and budget guards tested. |
| C: saved wait and continuation | PASS, component/native scope | Successful declaration/terminal linkage wakes once; busy, pause, stop, revision, revoked, active jobs, ack/defer/decline/failure, accepted-only, retry/unknown and restart recovery covered. Real runtime compaction plus an adjacent live-model round is not checked. |
| D: bounded awareness and installed resources | PASS | Existing receipt projections and on-demand skill retained. Built wheel installed into an isolated DTU target contains typed-reply skill and production assets. |
| E: useful cooperation as delivered to a real user | BLOCKED / unqualified | Deterministic production-service/Chromium complete loop passes, as does native Core/loop with scripted providers. Live-model efficacy, installed full worker-process/native-owner isolation, and live restart/compaction acceptance remain for parent qualification. Full repository regression is not green. |

Observed on the tested implementation:

- **326 Python checks passed, zero skipped**, including adapter, collaboration,
  coordination, service/runtime/stop admission, agent controls, canonical storage,
  standalone, history and browser-state suites.
- **440 frontend checks passed**, production build passed, and Chromium with
  `NODE_OPTIONS=--require=/opt/collaborative-dtu/chromium-env.cjs` passed.
  The browser records `qualifiedFinal`, `automaticDependencyContinuation`,
  `continuationOnce` and `completeLoop` as true. Its runtime and terminal anchors
  are deterministic emulation; it explicitly reports no native/live-model proof.
- `tests/fixtures/collaboration_native_probe.py` passed with Core 2.0.1,
  loop-live `bdd76badc58091ec47b55bb92cdf9e6f5bcc8e26` and context-simple
  `c7ec55db96888200ba619adedc857278daf787c5`. Two independent roots made 10 sender
  and 3 recipient scripted-provider requests, actual `app_control` calls, one
  simulated human approval, native in-flight steering, canonical multi-block
  terminal lookup and one continuation. The fixture independently read a concrete
  synthetic file. It uses direct sessions/serial event transport, not full worker
  subprocess ownership or a real model. No network model calls were made.
- Wheel build and isolated installation passed; packaged typed-reply skill and
  66 production asset files were verified. Assets/wheel are retained in the DTU,
  not committed into the source checkout.

Checks use the checkout's Python 3.13 environment with the already qualified
runtime site-packages on `PYTHONPATH` for installed native module coverage. The
checkout lock otherwise has Foundation `4e60f669` and no loop-live distribution;
the qualified runtime has Foundation `ab878820`. This deliberate check environment
selection is not an assertion that the normal installed-app resolver has qualified
the resulting composition.

The most recent full-repository fail-fast run, on `3c8df2c5`, stopped after
**523 passed, 5 skipped, 1 failed**:
`tests/test_canvas_draft.py:39` expected `Start a chat before opening Canvas` but
received `Choose a nonempty conversation identity` for a null UI target.
The same failure was reproduced on preserved source `4932263b`; it is not waived.
An earlier installed-module full run on `47bc8c71` reached about 76% with failures
before its 900-second bound; its identified pytest process was terminated. Neither
run is a full-suite pass. The inherited peer-pinning compatibility failure was
corrected with an exact presentation-only `session.pin` exception; execution and
settings fences remain.

Logs are retained under `/opt/collaborative-dtu/`: `corrective-tests-060c.log`,
`native-probe-060c.log`, `frontend-060c.log`, `build-060c.log`,
`browser-060c.log`, `wheel-build-060c.log`, `wheel-install-060c.log`,
`full-first-failure-3c8d.log` and `baseline-canvas-draft.log`. Earlier failed
checkpoint logs retain the multi-block rendering mismatch and incomplete native
test-fixture metadata/workspace corrections; passing replacements do not erase them.

One delegated Git checkpoint agent performed host Python syntax compilation
despite the DTU-only verification boundary. This is a recorded process violation,
not acceptance evidence; the implementation was subsequently checked in the DTU.
No host test suite or product build is claimed.

Parent owns independent real-model/installed-app qualification, final Fable
code/outcome review, full-suite disposition, any PR, and DTU/Gitea cleanup. This
lane has not pushed its branch, opened a PR, merged, deployed, edited accepted v1,
or retried the denied marker path.

## Attempt 1 status: preserved historical partial return

The following status and checks describe the earlier attempt only. Its upstream
limitation interpretation is superseded by the adapter investigation and corrective
evidence above; the original outcomes remain preserved.

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

DTU checks on source `4932263b94cc05d5949d231cbe517466876f80f4`:
202 focused Python checks passed across collaboration, coordination, service,
runtime, agent controls, builtin behavior composition and native/history queries.
The production frontend build and coordination browser fixture passed.
The frontend unit suite passed all 440 checks on that same source.
A built wheel installed into an isolated target contains
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

### Terminal recording boundary

The launcher-requested completion marker is outside this lane's allowed file-tool
write paths. The attempted write returned `Access denied ... is not within allowed
write paths`; no marker was written. No alternate tool or in-repository `DONE.json`
was used to evade that refusal. The parent manager owns terminal-status recording.
Final code checks and wheel/browser artifacts remain preserved in the parent DTU;
the supplied evidence directory also holds copied logs. This record is evidence of
the refusal, not an assertion that the launcher marker exists.

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
