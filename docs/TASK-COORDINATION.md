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
   Pending proposals survive the transient approval timeout and host restart in
   existing command receipts. `coordination.context.proposals` exposes their
   stable proposal IDs; Related work and `coordination.decide` allow a human to
   decide later without a live original generation or new user text. Approval
   rechecks the retained authenticated human source/digest, exact scope, roots,
   workspace and interruption revision. Explicit denial cannot be revived.
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
   Multiple inputs delivered in one generation may have separately typed replies
   sharing the same exact terminal message; this does not imply separate answers.
   Restart changes unresolved staged replies to unknown with dependency attention,
   leaving the continuation unclaimed. Already sealed evidence is preserved.
5. **Wait and continuation.** Cursors only acknowledge results. A saved wait
   binds one request and one saved task revision, interruption revision and grant.
   One qualifying response may admit one stable-ID continuation through existing
   worker input admission. Busy senders wait for idle; paused/stopped/revoked or
   exhausted senders stay stopped. Unknown/crashed admission is retained without
   resend. Unsupported runtime qualification/admission is a blocked acceptance
   area, not successful completion.
   Creation and subscriptions require both queue mode and explicit idle-start
   permission before any effects; a notify-only grant does not commission work.

Discovery filters root summaries before paging. Cold reads target one native
history, return bounded relevant windows and report unavailable history rather
than treating an unloaded projection as empty. Task chats are ordinary roots
with explicit title, creator/request links, same workspace, output namespace and
recorded configuration snapshot. No second transcript, database or scheduler.
Related work and agent actions share this service and preserve selection/drafts.
The on-demand `coordinate-work` skill must be installed and discoverable.

Implementation and validation status is recorded below only after observed
checks. This specification does not assert that the new capabilities exist yet.

## Live-acceptance gap continuation: fixes checked; final qualification BLOCKED

This continues the same lane, worktree and objective. Earlier attempts, failures,
commits and the passing independent model result are preserved. The final tested
implementation is `91e22bfe29ced4d8cd1e75220316b26714fad8f4`; any subsequent
evidence-only commit does not change its code, skill or compiled assets.
The terminal outcome is **BLOCKED for full-suite/live acceptance**, not a complete
feature claim. No final Fable review was requested from this lane.

### Three bounded fixes

1. **Credential rebinding.** Inherited task snapshots retain explicit environment
   references or a host-credential marker, never an expanded provider secret.
   Preparation resolves markers only against the same host-composed provider
   module, instance, source and nested-agent scope. Missing, disabled, ambiguous
   or changed bindings refuse explicitly; required-secret schema validation
   remains strict. No provider-family environment variable or alternate account
   is guessed. Bound workers persist reference-only effective snapshots too.
   Tests exercise the actual `prepare_manager` path, schema materialization,
   exact default/priority, pinned model/effort and multi-instance keys. Module
   mounting in those tests is mocked: this is **not** proof of a newly created
   real-model worker starting successfully.
2. **Native bridge metadata.** The service consumes only `_generationId`,
   `_runtimeSessionId`, `_inputBindings` and `_inputClients` before validating
   public operation arguments. Trusted principal/generation/provenance remain
   in their context bindings; browser origin and context-specific metadata stay
   intact. Actual `Worker.app_access_bridge` tests cover history list/search/read,
   forged transport replacement, arbitrary-extra rejection, observations,
   child identity and context restoration. Public history schemas were not
   relaxed.
3. **Scope-specific compatibility.** Nullable draft targets reach the existing
   Canvas guard; empty executable identities still refuse. Exact organization,
   history, presentation, readiness and deletion-preview actions retain their
   existing downstream checks. Message presentation/annotations retain the
   shared caller guard. Name regeneration, peer execution/control/settings and
   actual deletion are not made passive. Older fixtures now carry real caller
   provenance; unauthorized model creation remains denied while human creation
   and ordinary default/session inheritance are still tested.

The genuine pre-feature baseline is
`2e310274d3e401200d930c9e7a7e900563eaeaa1`. `4932263b` is this lane's first
implementation, not an upstream baseline. The null Canvas identity regression
was introduced by this feature's generic guard; reproducing it on `4932263b`
never established a pre-existing upstream defect.

### Checks after the last implementation change

All execution below was inside the existing parent-owned
`collaborative-e1667af2`; no new DTU or model acceptance run was created.
The checkout's Python 3.13 environment reused the installed native runtime via:

```sh
cd /workspace/checkouts/91e22bfe29ced4d8cd1e75220316b26714fad8f4
export PYTHONPATH="$PWD:/opt/collaborative-live-readiness/runtime/.venv/lib/python3.13/site-packages"
export UNIFIED_RUNTIME_PYTHON="$PWD/.venv/bin/python"
.venv/bin/python -m pytest -q \
  tests/test_provider_environment.py tests/test_runtime_controls.py \
  tests/test_runtime_worker.py tests/test_history_query.py \
  tests/test_collaborative_workspaces.py tests/test_canvas_draft.py \
  tests/test_browser_state.py tests/test_input_origin_browser.py \
  tests/test_observation_input.py tests/test_host_session_resume.py \
  tests/test_coordination.py tests/test_runtime.py tests/test_collaboration_input.py \
  tests/test_agent_chat_controls.py tests/test_agent_canvas_scope.py \
  tests/test_conversation_library.py tests/test_naming_controls.py \
  tests/test_canvas_visibility.py tests/test_attachments.py tests/test_client_layout.py \
  tests/test_builtin_behaviors.py tests/test_standalone.py \
  tests/test_creation_inheritance.py tests/test_desktop_readiness.py \
  tests/test_headless.py tests/test_managed_chats.py tests/test_managed_deletion.py \
  tests/test_message_delivery.py tests/test_message_interactions.py --tb=short -ra \
  --junitxml=/opt/collaborative-dtu/gaps-relevant-91e22bfe.xml
```

Observed results:

| Check | Result | Boundary |
|---|---|---|
| Final relevant Python suite | **616 passed, zero skipped**, 265.02 seconds | Includes all changed-test areas and inheritance/browser/draft/authority compatibility. |
| `npm test --prefix frontend` | **440 passed, zero skipped** | Final implementation source. |
| Production build and asset hashes | **PASS**, all 95 files identical | Rebuilt the exact final source; tracked outputs match the DTU manifest byte-for-byte. |
| `npm run test:coordination-browser --prefix frontend` | **PASS, completeLoop true** | Actual production service/Chromium; deterministic runtime and emulated terminal anchor, not live-model proof. |
| Native scripted-provider probe and wheel installation | **PASS** | Two native roots, actual tools, steering and one continuation; installed wheel has accurate typed-reply/create skill and all 95 matching assets. No real-model efficacy/startup claim. |

Browser commands also set
`AMPLIFIER_TEST_ROOT="$PWD"`, `AMPLIFIER_TEST_PYTHON="$PWD/.venv/bin/python"`
and `NODE_OPTIONS=--require=/opt/collaborative-dtu/chromium-env.cjs`.
The native command was
`.venv/bin/python tests/fixtures/collaboration_native_probe.py`.
Wheel commands were `uv build --wheel --out-dir /opt/collaborative-dtu/gaps-wheel-91e22bfe`
and `uv pip install --python .venv/bin/python --no-deps --target
/opt/collaborative-dtu/gaps-wheel-91e22bfe/installed
/opt/collaborative-dtu/gaps-wheel-91e22bfe/*.whl`.
Wheel import/resource checks used that installed target, not the source tree.

Assets were first generated from `019bd6e9681e332e5da2fa75eacadb37b3f167d3`,
transferred through `amplifier-digital-twin file-pull`, and committed with obsolete
hashed chunks removed. Rebuilds on `a72ce6b8`, `3940fc28` and final `91e22bfe`
reproduced every hash without tracked changes. The source/hash manifest is
[`workspaces/evidence/collaboration-gaps-assets.json`](workspaces/evidence/collaboration-gaps-assets.json).
The skill names exact `coordination.create` fields: acceptance criteria belong
in `text`, not an invented `acceptance` argument.

### Broad-suite accounting and exact blockers

The monolithic `pytest -q tests --tb=short -ra --junitxml=...` run on `019bd6e9`
was interrupted by the DTU CLI's **600-second JSON-exec timeout**, before the
requested 1500-second pytest bound. Its output/JUnit are retained, not relabelled
as complete. Of 5,110 collected nodes, 2,278 completed before that transport
timeout. The remaining 2,832 were declared in three areas and executed through
`exec --stream --timeout 930` with a 900-second pytest bound:

| Area | Source | Observed result |
|---|---|---|
| n–q, `@/opt/collaborative-dtu/gaps-remaining-n-q.args` | `6782ec89` | 942 passed, 4 failed, 12 skipped |
| t–z, `@/opt/collaborative-dtu/gaps-remaining-t-z.args` | `6782ec89` | 844 passed, 1 failed, 73 skipped |
| r–s, `@/opt/collaborative-dtu/gaps-remaining-r-s-files.args` | `91e22bfe` | 950 passed, 2 failed, 4 skipped |

The first r–s node-ID invocation failed before execution because the installed
Core pytest plugin called `Path.exists()` on an overlong parametrized node ID.
Its INTERNALERROR is retained. Changing to 69 ordinary file paths completed
the area without disabling the plugin, deleting tests or changing product code.

After overlaying the final relevant checks, the mixed-source accounting is
**4,992 passed, 8 failed, 110 skipped, zero unobserved collected nodes**.
This is **not a single-SHA full-suite pass**. Two modules skipped collection
(`test_browser_auth`, `test_context_continuity`) also remain explicit; their
uncollected test count is unknown. The structured record contains every run's
source, counts, skip reasons, remaining failure names and log hashes:
[`workspaces/evidence/collaboration-gaps-results.json`](workspaces/evidence/collaboration-gaps-results.json).

Remaining blockers, not waived:

| Tests | Exact cause / disposition |
|---|---|
| `test_host_children.py::test_real_foundation_child_lifecycle_and_delegate_contract` | Missing `amplifier_module_tool_delegate` in the selected check environment. Reproduced on genuine baseline `2e310274`; this proves an environment limitation, not a green product check. |
| `test_observation_presentation.py::test_exact_retry_cannot_upgrade_presentation_and_agent_cannot_choose_client`; `test_observations.py::test_operator_boundary_human_provenance_and_cross_session_scope` | Generic collaboration denial preempts the expected trusted-input/human-request diagnostics. Refusal remains, but scope-specific regression checks fail. Not claimed pre-existing. |
| `test_operations.py::test_operation_cancel_uses_existing_hooks_and_rejects_redirect`; `test_operations.py::test_process_stdin_uses_saved_owner_and_never_restarts_missing_runtime`; `test_voice_native.py::test_native_capture_existing_action_once_with_authoritative_metadata` | Direct model fixture calls omit caller provenance and now hit the peer-mutation fence. No permission bypass or optimistic pass substituted. |
| `test_runtime_progress_backpressure.py::test_buffered_worker_events_yield_to_bridge_without_reordering_events` | Fixture worker row lacks `runtime_id`; the reader raises that KeyError, then lacks `ready` on cleanup. This lane did not establish it as an upstream baseline defect. |
| `test_session_health.py::test_recovery_is_independent_durable_and_never_executes_or_replays` | Direct model recovery fixture lacks caller provenance and is denied by the collaboration fence. Recovery/new-root authority requires explicit disposition rather than silently weakening creation checks. |

Skipped checks include unavailable tool-bash, filesystem, context-managed,
profiling, CLI and Python Playwright modules; unconfigured isolated CI; and
opt-in contribution, history-edit, recipes and warm-worker checks. Exact grouped
reasons/counts remain in the structured record. No skip is converted to a pass.
The parent owns the remaining full-suite qualification/disposition.

### Evidence and preserved live acceptance

Final logs/JUnit are under `/opt/collaborative-dtu/`: `gaps-relevant-91e22bfe.*`,
`gaps-build-91e22bfe.log`, `gaps-assets-rebuild-91e22bfe.log`,
`gaps-frontend-91e22bfe.log`, `gaps-browser-91e22bfe.log`,
`gaps-native-91e22bfe.log`, `gaps-wheel-build-91e22bfe.log`,
`gaps-wheel-install-91e22bfe.log`, `gaps-wheel-resources-91e22bfe.json`,
the three area logs/JUnit named above, `gaps-full-019bd6e9.*`,
`gaps-collected-019bd6e9.txt`, `gaps-split-manifest.json`,
`gaps-genuine-baseline-children.log` and `gaps-final-results.json`.
Earlier focused failures and successful replacements remain under their exact
source-suffixed `gaps-*` names; none was erased.

The independent model evidence on `47bc8c71` remains a PASS for discover/read,
one current-source grant, attributed peer input, independently produced/read
`7*11=77`, typed native sealing, exactly one requester continuation, an adjacent
commission and passive restart preservation. Its failed new durable-task startup
and unrun live in-flight steering are **still not qualified on the corrected SHA**.
Parent owns those actual new-task model checks and final Fable review. No unknown
admission was replayed and `/opt/collaborative-live-acceptance-47bc` was not changed.

No locked v1 documents, active host installation, environments or prior captures
were replaced. This continuation ran no host tests/builds/install/compilation,
and performed no push, PR, merge, deploy, teardown or external DONE-marker attempt.
Parent retains DTU/Gitea teardown and terminal-marker ownership. An older retained
pytest plan-fixture process (DTU PID 48965, source `47bc8c71`) was observed and left
untouched; no lane test/worker process from this continuation remains running.

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
