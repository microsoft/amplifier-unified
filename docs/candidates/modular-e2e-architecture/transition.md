# Transition from Unified to replaceable components

> **Candidate / WIP — discussion only.** This document does not govern current
> implementation, amend adopted contracts, or establish release requirements.
> See [package status](README.md) before proposing adoption.

Working plan, October 28, 2026. Source baseline:
[`microsoft/amplifier-unified@924acff`](https://github.com/microsoft/amplifier-unified/tree/924acff405de367ac408f7e9b2b9cc7ea4baa47a),
release 0.34.3. This is the audited source revision; it is not a deployment status claim.

**Sequence:** establish contracts around existing behavior, prove one complete
replacement path, extract its owners, then improve those owners in parallel.
Repeat for additional capabilities. We should not first build an abstraction for
every current subsystem or move the monolith into several coupled repositories.

![Transition and parallel work](diagrams/transition.svg)

## Current implementation to target boundary

Implementation source links below are pinned to the audited revision. The
[backend audit](evidence/unified-source-audit.md)
contains precise revision-bound line references and recovery details.

| Current path | What exists today | First boundary / eventual owner |
| --- | --- | --- |
| [`server.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/server.py), `AppService`, `RuntimeManager` | Server directly constructs native runtime; `/api/actions` owns private action routing | Agent execution port and durable session/engine binding; then generic host command admission |
| [`runtime_protocol.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/runtime_protocol.py), `runtime_worker.py` | Native worker uses private bidirectional JSONL with app bridge callbacks | Keep native composition behind an ACP adapter; map semantics and unsupported capabilities explicitly |
| [`service.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/service.py), `state_records.py` | Command ledger and keyed state with bounded working-set loads | One command owner and scoped storage ports; preserve bounded reads/writes during extraction |
| [`workspace_work.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/workspace_work.py), `assignments.py`, `workspace_work_invocation.py`, `runtime_controls.py` | Several distinct work/policy layers linked by native task/generation/claim identifiers | Collaboration owns desired work; native policy stays behind native adapter; runtime owns attempts/effects. Resolve ownership before splitting files |
| [`frontend/src/api.js`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/frontend/src/api.js), `main.jsx`, `state-stream.js`, `state-transport.js` | Private REST plus snapshot/delta stream over WebSocket/EventSource; current transport is not AHP | Client transport/store port, then AHP client against the same host state owner |
| [`draft-journal.js`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/frontend/src/draft-journal.js) | Local journal protects unacknowledged composer text; server still owns acknowledged drafts | Complete private view/draft ownership deliberately; do not call the existing journal full local-state migration |
| [`mcp-app-viewer.jsx`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/frontend/src/mcp-app-viewer.jsx) | Real MCP Apps bridge with scoped canvas/tool actions and operation polling; imports app/domain components | Preserve host-enforced scope; inject capability port. Extract pure presentation components into the Storybook owner |
| [`host/storage.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/host/storage.py), `state_projections.py`, `session_projection.py` | Native histories/locks, bounded projections and catalog-like queries | Adapter retains native history; catalog/resource ports expose bounded authorized views without starting workers |
| [`auth.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/auth.py) | PAM login and control-token access represent the process owner | Preserve local-owner mode first; add distinct people/agent principals and project membership for shared collaboration |
| [`voice.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/voice.py), [`frontend/src/voice.js`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/frontend/src/voice.js) | WebRTC media setup and durable voice delegation; call lifetime separate from admitted work | Media owner submits through host admission; retain hangup/continued-work behavior |
| [`schedules.py`](https://github.com/microsoft/amplifier-unified/blob/924acff405de367ac408f7e9b2b9cc7ea4baa47a/amplifier_web/schedules.py), `test_queue.py`, `task_environments.py` | Due-run claims, test admission and runner allocations already have receipts | Scheduler submits stable host intents; execution/verification separate effect lifecycle from product acceptance |

The existing [October 2 architecture](../../architecture/README.md)
already specifies AHP/ACP separation and a component repository plan. Start there;
this packet updates the current code inventory and adds collaboration and Converge.
The host-owner investigation must compare the existing daemon work before creating
another host implementation. Do not assume a proposed repository already exists.

## Phase 0 — establish the comparison and interfaces

1. Pin source, schema, protocol and package versions. Capture the supported feature
   inventory, old session classifications and current user-level behavior in an
   isolated development installation. Use synthetic or carefully copied test data;
   never test migration by changing the production state root.
2. Turn B1/B2/B3 from [the boundary draft](boundaries.md) into small typed ports and
   peer fixtures. Choose one owner for each authoritative field before extraction.
3. Compare current `amplifierd`/host work, existing native adapter efforts and the
   proposed repository names. Choose a host owner based on runnable code, lifecycle
   and fixture fit; preserve the protocol contract regardless of packaging choice.
4. Inventory native-only controls: bundles, providers, models, task/goal controls,
   background work, approvals, steering, skills/extensions, worktree transfers and
   app bridge effects. Record preserve, replace, explicitly unavailable or retire
   per feature; no broad parity claim from a successful chat demo.

**Exit evidence:** current behavior fixtures, explicit authority map, source manifest,
and independently runnable client/agent peers. This packet provides the design and
source audit; runnable Unified fixtures and full feature inventory remain work.

## Phase 1 — put existing behavior behind internal ports

Introduce an execution port at the current runtime construction/dispatch boundary.
The native implementation remains the initial provider. Introduce durable binding
of a host session to its engine/native ID/owner. Legacy sessions receive native
bindings without rewriting their opaque histories or task state.

Route old HTTP actions and the first AHP resource/command slice through the **same**
command owner. Wrap storage in scoped ports; do not copy all of `AppService` into
a supposedly generic host. Isolate the frontend transport and private view state.
Keep the existing web/server release matched while these internals change.

The native adapter can initially contain its existing JSONL worker implementation;
the externally exercised ACP interface is what changes. App bridge/tool effects
must return through scoped host operations rather than import `AppService` directly.
No agent-specific work guard becomes mandatory for every generic ACP prompt.

**Exit evidence:** existing send/stream/steer/cancel, reconnect, native history,
voice delegation and capability errors pass through the ports with unchanged
observable behavior. Fail a duplicate writer and uncertain dispatch honestly.

## Phase 2 — prove the first replacement through the existing experience

Use an isolated development distribution with two explicit engine choices:
native Amplifier for existing sessions, Converge ACP for new coding sessions.
Map the smallest client/host path onto AHP: create/open, submit, stream, correct,
cancel, inspect result and reconnect. More advanced features stay on their current
path until their boundary is qualified; both paths call the same relevant owner.
This temporary coexistence is a migration façade, not two work stores.

The first slice is deliberately single-owner. A person opens the familiar UI,
starts a new Converge coding session, supplies a vision/contract, sees early
admission and streamed work, corrects it mid-turn, leaves and returns, then inspects
the exact candidate and independent check evidence. An existing native session
remains usable. Use a real model and real ACP client/host as well as fixtures.

**Exit evidence:** one complete user journey with unchanged acceptance; compatible
independent peers; hard failure cases retained; explicit unsupported features;
measured admission, first-content, interruption and total outcome latency.
No new UI rebuild or speculative collaborative scheduler is needed for this slice.

## Phase 3 — extract qualified owners, then iterate in parallel

Publish artifacts for the native ACP adapter, host, web experience and UI components
as their fixtures and independence become real. Existing Converge repositories
remain independent owners. The distribution consumes versions/profiles, starts a
matched composition and records activation; it does not vendor private source.

Extract pure reusable UI components with Storybook and fixtures. Keep application
network/store/policy logic in the experience. Split catalog/resources where useful
after their scoped contracts work; a small host package can remain in-process.
Repository, package and process boundaries need not coincide.

**Exit evidence per extraction:** clean install without sibling source imports;
own vision/contracts and focused tests; fixture-driven development; unchanged real
user journey in composition; substitution and version mismatch demonstrated.
Minimal refactoring needed for the seam is expected. Broad rewrites happen after
that owner can be changed and assessed independently.

## Phase 4 — add persistent collaboration and shared people

Introduce collaboration identity above engine sessions: named agent, project,
membership, discussions, accepted decisions, commitments and versioned assignments.
One agent may use multiple isolated engine sessions. First reuse the existing
storage location through the sole owner; moving storage is a separate migration.

Add distinct authenticated principals, resource scopes, attribution and explicit
conflict handling before a two-person claim. Test one named agent across two
discussions, two humans and a focused coding assignment. Parent checks remain
unchanged; a late child result after a changed objective is preserved and reassessed.
Use lightweight attention rules first, measuring interruptions and stalled work.

This domain can develop in parallel against fixtures once B3 is stable. It joins
the running composition only when its access and work semantics are qualified.
The host remains generic; it does not import Converge strategy or native task policy.

## Phase 5 — improve and replace individual components

Run independently owned improvements against fixed contracts and a shared
composition test pack: fresh-worker delegation, context retention/compaction,
provider/model recipes, decision policies, execution runners, verification,
scheduling, media, artifacts, workshop and authorized context intelligence.

Native capabilities retire one by one only after a replacement meets the user
journey or a deliberate product decision removes the capability. A2A and A2UI
remain optional candidates. MCP Apps already has an implementation to preserve
and qualify. Do not adopt every protocol merely to complete the diagram.

Use stable defaults for ordinary users and separate development state/manifests
for experimentation. Quality leads speed, and speed leads cost; human effort is
also measured. Model benchmark gains alone do not prove an improved experience.

## Cutover and rollback

Today `RuntimeManager.close` stops all workers, and worker input EOF cancels and
cleans up execution. A source/configuration update is therefore **not** a seamless
live host replacement. Before a host cutover choose a tested approach:

- **Retain the old owner:** keep the old service alive and route its existing
  sessions through a temporary bridge. The new host must not also open/write its
  native histories. Qualify query/cancel/control behavior through that bridge.
- **Drain:** stop admitting new work to the old owner, let active work reach a
  safe terminal state, then transfer exclusive ownership. Interrupted/unknown
  states remain explicit; do not replay unfinished effects to force a clean state.

Record the session mapping, active owner/generation, manifests, schema versions
and durable receipts before transfer. Route new work to the replacement first.
An old native session stays on its engine; explicit facts/artifacts can seed a new
Converge session. That is a handoff, not transparent conversion of model history.

Rollback changes routing or a compatible component manifest. It never restores an
old database over later writes. New-engine sessions remain bound to the engine
that understands them even if new admission returns to the old default. Preserve
unknown outcomes for inspection. Schema changes require a qualified forward/backward
policy; backups alone are not a reversible migration strategy.

Read-only shadow comparison may compare projections. Never shadow-execute user
commands against both owners. No permanent dual write or alternate task authority.

## User-level smoke pack

Each row becomes a public-interaction test with inspectable receipts/artifacts.
These are acceptance obligations, not tests claimed to exist already.

| Journey | Failure it should expose |
| --- | --- |
| New Converge code change in existing UI; independent real ACP client too | Private host assumptions, incomplete compatibility, unverifiable completion |
| Stream, mid-turn correction, cancel, delayed approval | Hidden blocking, lost steering, revived cancelled work, partial tool execution |
| Two viewers with separate drafts; all leave and return | Client state collisions, work tied to a browser, stale reconciliation |
| Reply lost after admission; reconnect and inspect | Duplicate work from blind retries, false certainty about downstream effect |
| Native old session and new Converge session coexist | Wrong-engine resume, duplicate history writer, silent capability loss |
| History/catalog with many dormant sessions and slow client | Unbounded materialization, workers starting just to browse, blocked fast viewer |
| Correct objective while a child works; retain late result | Lost evidence, stale acceptance, multiple mutable task boards |
| Verified leaf whose parent still fails | Incorrect parent completion from narrow checks |
| Voice call ends; scheduled run retries; artifact opened | Lost delegated work, duplicate schedule, broken resource identity |
| MCP App reads/mutates only its scoped resource | Cross-project actions or host credentials leaking into a renderer |
| Distinct people, changed membership, conflicting directions | Multi-client mistaken for multi-human, unauthorized replay or history access |
| Replacement fails after new writes; rollback route | Lost history, incompatible schema, new session bound to old engine |

Run focused component checks and affected user journeys on each change. Run broad
composition/performance coverage independently at the appropriate checkpoint; do
not make every edit wait on every long test. Preserve Unified's existing separation
between fast foreground checks and broader validation. Protocol fixtures alone
cannot substitute for real-peer and real-model journeys.

## Parallel work and first implementation tickets

| Lane | First bounded result | Depends on | Writable ownership |
| --- | --- | --- | --- |
| Integration/host | Decide host reuse, pin B1/B2, add native execution port and session binding | Current source and fixtures | Runtime construction, admission, binding schema; one migration owner |
| Native/ACP | Capability inventory and native ACP peer retaining history/locks | B2 fixture | Native adapter/runtime internals; no independent host-store writes |
| Experience/components | Transport/view-state port; first AHP journey; Storybook extraction candidate | B1 fixture | Client transport and component owner; no work schema changes |
| Converge quality | Fresh bounded worker, unchanged parent checks, hard-task comparison | Existing core/context/model contracts and B3 packet | Existing Converge repos; isolated evaluation data and endpoint reservation |
| Collaboration | Named-agent/project/discussion fixtures and authority prototype | B3 and principal contract | Domain records/API; no generic host or runtime-private edits |

Start with integration, native/ACP, experience and Converge quality as available
capacity allows. Collaboration design/fixtures can proceed alongside them; actual
multi-human integration follows its access tests. Parallelize disjoint owners,
not simultaneous edits to the same schema or competing native-model experiments.
Integration owns composition and contract reconciliation, not every implementation.

The first tickets are: **(1)** native execution port and durable engine binding,
**(2)** native/Converge ACP profile fixtures, **(3)** existing-UI thin AHP coding
journey, **(4)** fresh-worker hard-task improvement behind unchanged ACP. Each has
a useful result even if another lane's experiment fails. No bulk repo move is
needed before these interfaces can be tried.

Every temporary façade gets an owner, named consumers and a removal trigger.
Remove the old global transport after its supported clients migrate. Remove the
private host shortcut after native sessions/controls work through their adapter.
Reduce `amplifier-unified` to distribution/composition only after all supported
behavior has an owner or explicit retirement decision, and its regression pack
proves the assembled result. That is the endpoint of the transition, not phase one.
