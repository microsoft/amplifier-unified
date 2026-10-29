# Unified source audit for the candidate transition

> **Candidate / WIP — discussion only.** This document does not govern current
> implementation, amend adopted contracts, or establish release requirements.
> See [package status](../README.md) before proposing adoption.

Source audit, 2026-10-28. Examined `amplifier-unified` commit
`924acff405de367ac408f7e9b2b9cc7ea4baa47a` (release 0.34.3). Read-only source
inspection; no service changes, deployed-instance claims, test execution, or
protocol conformance claims. Source paths below are relative to that checkout;
line references identify this exact revision. This report distinguishes current
implementation, existing project intent, and proposed transition choices.

## Main conclusion

The current repository already has a remarkably aligned destination: a durable
AHP host, ACP agents, independently owned components, bounded read models, and
native session continuity. However, that architecture packet explicitly says
implementation is pending. The production application still instantiates a
Python `AppService`, directly constructs `RuntimeManager`, and talks to its
native worker over private JSONL. The recent changes have substantially improved
bounded state access inside that application; those gains should be preserved
when protocols are introduced.

The first useful replacement seam is execution admission into an engine-bound
session, accompanied by a generic command receipt and observation interface.
Put the existing runtime behind a legacy adapter and add Converge for **new**
conversations. Do not infer that ACP compatibility permits resuming old Amplifier
history with Converge. Also do not pull every module named task/assignment/work
into separate repositories: they currently represent distinct layers of policy,
authority, and projection that need to be simplified before becoming public APIs.

## Existing destination versus implemented behavior

| Finding | Evidence | Consequence |
| --- | --- | --- |
| Oct 2 design says AHP between clients and host; ACP between host and agents; native session identity remains engine-bound | `docs/architecture/README.md:3–49`; `contracts/agent-protocol.v1.md:25–55`; `contracts/host-protocol.v1.md:24–53` | Reuse this direction as input; revise intentional differences explicitly. It is not an implemented transport. |
| The same design already proposes fixtures, separate repos, compatibility classification, staged activation and obsolete-route removal | `docs/architecture/delivery-plan.md:18–49,123–150`; `docs/architecture/repositories.md:8–35` | Avoid creating a competing migration program. Update its evidence and map responsibilities to our actual components. Names there are proposals. |
| Current server creates `AppService` and `RuntimeManager` directly and wires native callback `service.app_bridge` | `amplifier_web/server.py:76–103` | This is the immediate backend extraction seam. There is no generic agent registry at this construction point. |
| `/api/actions` dispatches current private action names and returns native receipts; `delta-v1` changes response shape, not protocol semantics | `amplifier_web/server.py:368–400` | Keep a temporary façade over one command owner. Do not call this transport AHP or wrap global snapshots behind AHP labels. |
| Worker protocol is bounded JSON-lines; worker commands and host bridge calls are bidirectional and app-specific | `amplifier_web/runtime_protocol.py:1–13`; `runtime.py:620–684,1042–1090`; `runtime_worker.py:1482–1503` | ACP extraction is a semantic translation and capability inventory, not replacing a serializer. |
| Worker mounts native loop-live Runtime, Foundation `SharedSessionStore`, bundle/session machinery, runtime controls and app-access hooks | `runtime_worker.py:633–760`; `host/session.py:830–850,990–1009` | Keep native internals together behind the legacy ACP adapter until replaced. Host must eventually stop importing these classes. |
| `rg` found no ACP/AHP implementation references under `amplifier_web/*.py` | Search of source at this revision; corroborated by actual server/worker construction | This bounded audit did not prove absence in all optional packages; it does establish the audited application path remains native/private. |

## Authority map: the difficult cuts

| State or operation | Current owner / source evidence | Target cut |
| --- | --- | --- |
| Command admission, deduplication, user/agent provenance | `AppService`, `app.sqlite3.commands`, fingerprint includes action/args/origin and sometimes client; `service.py:51–53,535–545,2335,2557–2570,3771–3806` | Generic host owns one durable admission receipt per intent. Protocol ACK, agent acceptance, execution completion and independently checked result remain separate facts. |
| App state and scoped session/runtime records | `state_records.py:1–8,19–23,106–200`; `AppService.__init__` loads working subset after directory migration, `service.py:582–610` | Preserve scoped repository methods and atomic writes. Do not restore a giant app JSON store or make a second canonical session history. |
| Native transcript, metadata and execution writer lock | `host/storage.py:39–49,123–159`; `runtime_worker.py:669–688` obtains Foundation shared-session ownership | Native adapter owns resume storage and writer lock. Host stores engine/native-ID mapping and display references. Catalog loss must not alter resume ability. |
| Native session task, goal, task receipts, capacity and selected model | Per-session `control-state.json`; `runtime_controls.py:169–219`; TaskController `task_continuity.py:1–18,54–99` | Native adapter keeps these legacy semantics. For the new family choose one owner of work objective/revision and pass run-bound contracts downward; do not create competing task canonical stores. |
| Cross-conversation assignments and optional claimed subtasks | `assignments.py:46–87`; `assignment_tasks.py:13–37,52–82` | Current completion is a participant report, not artifact proof. Claim does not execute or lock files. Preserve these distinctions; target work coordination should return one result bound to exact request revision rather than demand synchronization of multiple task boards. |
| Workspace commitments and event history | `WorkspaceWork` stores `workspace.work.record/event` in command receipts; `workspace_work.py:85–105` | A semantic Work owner can be independent, but its history must remain single-authority and referenced by host/agents. Do not duplicate it in a new queue or UI store. |
| Effect prerequisites binding work to native task/generation/claim | `workspace_work_invocation.py:53–98,203–229,282–390`; `workspace_work_effects.py:80–115`; worker bridge `service.py:5010–5045` | This is substantial hidden coupling. It needs explicit product decisions: which generic effect authority must survive and which legacy orchestration policy can be confined to the old adapter. It must not become mandatory for ordinary ACP turns. |
| Conversation catalog and display projections | `service.py:736–751`; `state_projections.py:37–46,145–158,258–305`; `session_projection.py:200–217`; `state_records.py:134–166` | Separate query/read-model contract; no dormant runtime starts to list or inspect old work. Preserve exact source identity and bounded detail. |
| Schedules and runs | `Schedules` wraps `amplifier_scheduling.ScheduleStore` in its own `schedules.sqlite3`; `schedules.py:39–51`; current task/config binding `schedules.py:104–140` | Good extraction candidate after admission seam. Scheduler owns due-run claim/history; submits stable intents through host. It should not own another agent task state. |
| Test environments, allocations and guest effects | `task_environments.py:246–285,287–326`; test queue durable admission `test_queue.py:395–416`; guest receipt validation `test_job_transport.py:33–57` | Execution service/library owns allocation, command identity, lifecycle and exact source/config binding. Verification policy judges checks against requested contract separately. Completion of a job is not acceptance of product work. |
| Operations display | `operations.py:140–207` adapts registered sources; schedules register their records `schedules.py:46–51,97–102` | Treat aggregate operation views as projections over owners, not a new canonical workflow engine. |
| Voice media/transcript/delegation | `voice.py:898–940` forms a prompt and calls durable `voice_delegate`; `service.py:5384–5408`; voice close deliberately leaves delegation work running, `voice.py:1188–1227` | Extract media lifecycle and transcript input separately from agent execution. Ending call/view must not cancel admitted work. Retain exact spoken-input/delegation identity. |
| Authentication and people | PAM allows only process-owner account, `auth.py:146–153`; owner control token auth `auth.py:233`; client views distinguish devices | Multi-client is not multi-human collaboration. Project membership, people/agent principals, grants and attribution need a new explicit contract before claiming two-person shared agent support. |

## Important existing recovery behaviors to preserve

1. **Admission outlives an HTTP request.** Server shields the service dispatch
   task (`server.py:378–382`). AppService commits the receipt and scoped state
   before running pending effects (`service.py:3771–3806`). Native transport
   holds in-flight work when callers timeout or a pipe write may have partially
   succeeded (`runtime.py:1068–1090`). New routes must query that receipt rather
   than replaying a prompt to discover whether it ran.
2. **Steering is bound to an existing run.** `RuntimeManager.steer` explicitly
   cannot start a worker and includes `targetGenerationId` (`runtime.py:1131–1148`).
   Ordinary ACP prompt support is not proof of this behavior; expose a negotiated
   extension or an honest unavailable state for peers lacking it.
3. **Restart is not seamless continuation.** `AppService.__init__` marks old live
   statuses interrupted and unresolved input deliveries unknown
   (`service.py:655–675,717–734`). `RuntimeManager.close` stops all workers
   (`runtime.py:1584–1587`); worker stdin EOF triggers cancellation/cleanup
   (`runtime_worker.py:1482–1530`). A host swap must drain existing work or retain
   the old execution service alive behind a bridge. It cannot promise transfer
   of live worker pipes merely because histories are durable.
4. **Saved controls retain native authority.** Worker startup reloads native
   control-state selections rather than silently taking a newer host display
   choice (`runtime_worker.py:697–730`). Scope migrations per engine and schema.
5. **Bounded working sets are implemented inside the current monolith.**
   `load_working` admits only startup/selected/requested owners; `save_working`
   does not tombstone nonresident sessions (`state_records.py:134–200`). Keep
   these properties under any new store interface.

## Proposed extraction sequence

These are recommendations for the new e2e design, not changes made by this audit.

### 1. Freeze the semantic seams before moving files

Inventory current user journeys and write fixture contracts for command admission,
engine/session routing, history/resources, work result/evidence, permission callbacks,
steer/cancel, and capability unavailability. Include current voice, scheduled work,
native resumability and test-run receipts. Confirm exact upstream protocol versions
separately; this audit used repository design references rather than independently
verifying current upstream schemas.

Introduce the smallest internal interfaces in the existing application with the
current implementation behind them. They can be Python packages first. Extract
only once each can be exercised against fixtures without importing `AppService`.

### 2. Add a per-session execution router, then the Converge ACP path

The current single `service.runtime` object is a native-manager abstraction, not
an engine registry. Introduce durable mapping such as host-session ID -> engine
kind/native ID/owning deployment/capabilities. Existing conversations remain on
legacy engine, new opt-in conversations select Converge. Same user experience can
render both through normalized public observations without rewriting native files.

The first legacy ACP adapter should contain its current private JSONL runtime and
native dependencies. Converge already has its own ACP owner; do not import its
engine internals into the host. Unsupported native composition controls remain
available only for sessions whose engine advertises them. Always test independent
ACP client -> Converge and host -> another agent, not just our own pair.

### 3. Extract host command ownership and catalog/resources

Once runtime can be called through a fixture peer, extract generic admission,
authorization, engine mapping and subscription state. Retain one command ledger;
legacy HTTP action façade and AHP entrypoint both call the same owning commands.
Separate read-model package behind bounded queries; resources use stable refs.
Do not copy all of `service.py` into a new generic-host repo and call it complete.

The frontend/protocol lane should own exact AHP resource mapping. Repository
boundaries need not all become processes; choose service separation only where
lifetime, isolation or remote placement earns it.

### 4. Separate native work policy from generic work responsibilities

This is the highest semantic-risk extraction. Current WorkspaceWork, Assignments,
AssignmentTasks, TaskController, WorkInvocation, test queue and Operations share
cross-references. Define minimal parent work/child assignment/result contracts
with exact objective revision and artifact evidence. Retain legacy task policy
inside old runtime adapter while new work uses one explicit Work owner. Aggregate
activity/views derive from those facts. Never declare legacy assignment completion
equivalent to independently verified contract acceptance.

### 5. Extract independent capability owners in parallel

After those contracts exist, scheduling, resource/artifact storage, runner/execution,
verification, voice/media, and context-intelligence observers can proceed against
fixtures in separate repos. Keep observation separate from authority. Model/context/
strategy modules already owned by Converge evolve behind the ACP boundary without
requiring host or client understanding. Use distribution manifests, not adjacent
source-path imports, to compose candidates for integration.

## Cutover, rollback and removal rules

- Test an isolated instance with copied production-shaped data or synthetic native
  histories; never use the live service as a migration test bed.
- Before each extraction record native data roots/checksums, session->engine mapping,
  active owner/generation, package/source/schema versions, and retained receipts.
- Route **new sessions** to the selected replacement first. Preserve all old sessions
  under their current owner. For existing live sessions, either keep the old service
  running behind a temporary route or wait for its safe drain; do not transfer pipe
  ownership or replay prompts. A new request can explicitly reference prior evidence
  in a new Converge conversation, but that is not cross-engine resume.
- Roll back traffic/candidate component versions only within compatible schemas.
  Never overwrite new writes with an earlier database snapshot. Preserve receipts
  whose downstream outcome is unknown and reconcile their existing identities.
- Give each façade/bypass a named owner, exact consumers, removal trigger and
  evidence. Remove the native host shortcut only after existing sessions are served
  through the native adapter and all promised controls have a qualified path or an
  explicit retirement decision. Retire old global-state transport once its clients
  are migrated; do not leave two competing production authorities permanently.
- Required user-level journeys: send/steer/cancel with two viewers; disconnect all
  viewers while work continues; lost admission reply and restart without duplicate
  effect; legacy native resume/read-only classification; zero runtime starts for
  catalog/history; scheduled run identity; voice hangup with task continuing; exact
  verifier source binding; late child result after revised parent request; safe
  rollback after new-engine writes. Multi-human membership/permission tests are a
  separate additional gate, not established by two browser tabs.

## Audit limits / next facts to settle

- This audit inspected source and current architecture contracts, not installed
  behavior or all ~350+ command action branches. A full feature inventory is still
  needed before promising parity, especially bundles/settings, worktree handoff,
  device effects, canvas/MCP Apps, approval and observation policy.
- Existing `amplifierd` host overlap must be checked before naming a second generic
  host (`docs/architecture/repositories.md:31–35` already flags this).
- Decide whether named persistent agents are identities above engine sessions or
  the sessions themselves. The candidate design proposes an identity above
  isolated workers; current native session IDs alone do not provide that product.
- Decide which legacy Work effect requirements remain mandatory. Current guards
  intentionally reject effects without a qualified final owner; preserving every
  guard by layering it onto Converge would reproduce friction, while removing all
  guards would lose genuine provenance/authority. Model this explicitly.
- No support for transparent cross-engine history or live-process conversion was
  established, and none is necessary for a gradual swap-in-place experience.
