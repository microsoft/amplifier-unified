# Delivery plan and parallel work lanes

Implementation work derived from ratified HP/AP/WS/CB and CS/CX/CD contracts.
The user approved the plan and direction on 2026-10-02 and explicitly excluded
`microsoft/amplifier-agent`; retain Core/Foundation and the modular native runtime.
Numeric budgets and repository names remain implementation choices; ratification
is not evidence that any runtime behavior is complete or shipped.

## Sequence and gates

| Wave | Work | Exit evidence |
| --- | --- | --- |
| 0: establish seams | Refresh ownership, finish full native control inventory, settle initial capability floor; create public fixtures, version matrix and performance traces | Reviewed interfaces, runnable AHP/ACP fixtures, independently reproducible baseline and proposed budgets |
| 1: build independently | ACP Amplifier adapter, generic AHP host, catalog/hints, web local-state/client seam and TUI backend | Each runs with fixture peers; no dependency on another lane's private checkout; bounded basic tests |
| 2: prove the architecture | Real web + TUI + host + native adapter; second ACP agent; independent ACP client; independent AHP peers | Core journey matrix, failure injection and large-catalog results; capability gaps named |
| 3: extract capabilities | Resources, canvas, configuration, devices/media, scheduling and remaining package boundaries | Public interfaces and feature-specific acceptance; no newly introduced host-native bypass |
| 4: migrate and release | Legacy classification/import, local draft migration, old transport retirement, compatibility and safe activation | Native history preservation, rollback drill, actual client/host/worker adoption; outstanding feature limitations visible |

Wave 0 should be small enough to unlock the independent lanes, not a design phase
that waits for every optional feature. Do not declare migration complete at Wave 2:
supported existing features require either qualified preservation or an explicit
documented product decision to retire them. Track that decision per feature.

## Launchable lane boundaries

### Repository ownership checkpoint, 2026-10-03

Each of the seventeen repositories now has a dedicated implementation or
qualification owner. The distribution integrator also coordinates a separate
Updates specialist. Repository ownership persists when its current task finishes;
an idle owner with a completed receipt is not an actively running worker.

| Repository | Current owner responsibility |
| --- | --- |
| `amplifier-unified` | Composition, migration ledger, cross-owner contracts, package intake and owned Spark-2 activation |
| `amplifier-unified-host` | Generic host scope/admission, public resource protocol and isolated full-owner Linux service qualification |
| `amplifier-app-acp` | Full native runtime, current dependency graph, native receipts and maintenance concurrency |
| `amplifier-unified-client-web` | Private browser state, durable client intents, rendered capability and recovery controls |
| `amplifier-unified-client-tui` | Connected terminal parity using negotiated public creation, workspace and maintenance capabilities |
| `amplifier-unified-protocol-extensions` | Generated bindings, versioned schemas and executable conformance against actual peers |
| `amplifier-unified-interop` | Independent clients/agents, vendor compatibility, replacement/reconnect and large-catalog qualification |
| `amplifier-unified-capability-native` | Native presentation bridge and exact outcome propagation without a second configuration authority |
| `amplifier-unified-capability-resources` | Canonical shared resources, reviewed legacy import and inactive historical payload ingestion |
| `amplifier-unified-capability-media` | Scoped device grants, transcript preservation, admitted capture lifetime and shutdown |
| `amplifier-unified-capability-operations` | Installed scheduling, questions, process controls, cancellation and receipt recovery |
| `amplifier-unified-capabilty-mcp` | Installed MCP lifecycle and selected offline registration/App evidence import |
| `amplifier-session-catalog` | Bounded metadata queries and rebuildable product-visibility projections |
| `amplifier-ahp-client-kit` | Durable private command journal, causality and independent installed consumers |
| `amplifier-publishing` | Indexed per-session exports, immutable releases and service/receipt preservation |
| `amplifier-portability` | Signed transfer mechanisms and immutable, bounded historical payload evidence |
| `amplifier-foundation` | Independent optional-library and canonical native history/lease compatibility |

Every owner uses an isolated checkout and task-owned test state. Shared sealed
packages may be read as inputs; another lane's checkout, environment, running
service or private implementation is not a writable dependency. A handoff contains
the source revision, archive hashes, public contract delta, installed-consumer
results, known omissions and the next integration obligation. The integrator owns
shared manifests and activation. A repository test does not qualify the assembled
product or authorize a pending feature retirement.

Cross-owner seams remain explicit:

- Standard AHP methods retain standard fields. Product workspace filtering uses
  an advertised capability; clients do not silently add fields to `listSessions`.
- Catalog visibility is derived from host-owned retained presentation records.
  Indexed-only reconstruction requires the host to fence product listing until
  metadata and visibility projection are both rebuilt. Native history and safety
  queries remain complete, including hidden rows.
- Detached transfer payloads are immutable historical copies. Signature, plan
  digest, source/destination and transfer identity are verified by the embedding
  owner before inactive resource ingestion; no second mutable artifact authority
  or automatic execution is created.
- Native question delivery currently advertises `active:false`. An active-turn
  refusal stays a saved refusal; clients and operations must not reinterpret it
  as steering or replay it. Broader delivery needs an explicit native admission
  and attribution contract.
- Reset preflight and admitted owner effects have different evidence. Only a
  proven typed no-effect preflight refusal may release the admission fence;
  transport ambiguity or an admitted unknown effect retains it without replay.

Names are responsibility slots, not assignments to current people or sessions.
Before launching a lane, resolve current ownership and replace proposed paths with
the exact target checkout/files. Every lane receives the common brief below.

| Lane / clauses | Writable owner and initial source | Producer / consumer and dependency | Deliverable and falsifier |
| --- | --- | --- | --- |
| L0: interfaces/fixtures, HP1/AP4/CB3–4 | `amplifier-unified-interop`, extension repo; this contract packet under one editor | Upstream schemas -> all lanes; first prerequisite is supported versions/capability floor | Minimal fake ACP agent + AHP host/client, failure fixtures; broken if peers need private implementation imports |
| L1: native ACP, AP1–6 | Adapter repo; extract from `runtime.py`, `runtime_worker.py`, `runtime_controls.py`, `execution*.py` | Native runtime -> standard ACP -> host and Zed; depends only on L0 schemas | New/prompt/stream/permission/cancel/load/resume plus complete limitation inventory; broken if a feature silently disappears or native history forks |
| L2: generic host, HP1–6/CS3–4 | Host repo; `server.py`, generic parts of `service.py`, command/ownership plumbing | ACP events -> AHP resources; use fake agent/catalog until L1/L3 ready | Two viewers, reconnect, receipt crash tests; broken if native imports or a global snapshot are needed |
| L3: catalog/hints, WS1–3/WS5–6 | Catalog repo; `automatic_history.py`, `history_watch.py`, `native_catalog.py`, history query; separately owned CLI/Foundation changes | Native durable facts/hints -> paged catalog -> host/agents; independent of L1 runtime completion | 4k/25k fixture, lost hints/rebuild, no agent starts; broken by synchronous historical scans or lost records |
| L4: web/local state, CS2–4/CX1–2/CB1 | Web repo; `frontend/`, persistence and AHP backend adapter | Upstream AHP + fixture host -> browser; depends on L0 only | Draft survives browser restart, zero typing traffic, scoped rendering; broken by shared private drafts or whole-app rebuild |
| L5: TUI/AHP, CS1–4/CX1–7/CB1 | New `amplifier-unified-client-tui`; the standalone predecessor remains intact | AHP fixture then actual host -> real terminal; independent of web | Actual Ratatui transcript/approval/reconnect tests; broken if connected mode imports native execution |
| L6: extensions/capabilities, AP5/CS5–8/CB2 | One owned repo per capability from repository map | Standard config/resources first, negotiated extension only for gaps; after L0 interface review | Feature mapping + unsupported-peer fixture; broken if ordinary chat requires the capability |
| L7: independent integration, HP/AP/WS/CB4–5 | `amplifier-unified-interop` and distribution manifests | Released/candidate public artifacts -> combined journeys; follows lane deliveries | Rerunnable real-peer and scale receipts; broken if only our default stack is exercised |
| L8: migration/activation, AP6/WS1/CD6 | Distribution migration tools and legacy bridge | Qualified components -> owned staging instance -> deployment qualification | Preserve/checksum history, import classification, rollback/fresh-worker receipts; broken if unknown resume causes replay |

Do not ask L1 and L2 to edit the same runtime extraction simultaneously. L1 owns
native behavior; L2 consumes its fixture interface. L0 settles any schema revision
before both implement it. L7's checker reruns public tests independently of authors.
After direct peer qualification, L7 can assess an ACP-facing/AHP-client bridge
for editors that should access hosted sessions but do not speak AHP themselves.

## Per-lane brief and evidence record

```yaml
lane: L1
status: implementation-active
contract_clauses: [AP1, AP2, AP3, AP4, AP5, AP6]
source_revisions: [exact-shas-before-work]
writable_repositories_and_paths: [resolved-owned-adapter-checkout]
read_only_dependencies: [upstream-acp-schema, native-runtime-contracts]
producer: native-Amplifier-runtime
consumer: ACP-host-and-independent-client
fixture_peer: versioned-ACP-client-fixture
acceptance: streaming-approval-cancel-native-resume-without-host-native-imports
falsifier: duplicate-execution-or-lost-module-capability
evidence_path: evidence/L1/<run-id>/manifest.json
independent_check: separate-reviewer-runs-the-public-entrypoint
handoff: exact-commit-package-version-capabilities-known-gaps
```

Evidence records include setup, commands, scenario, expected and observed outcome,
source/package versions, environment, raw artifacts and limitations. “Not run” is
not a pass. Record each ratified promise against actual implementation evidence; do not
convert the design approval into a conformance pass. Keep detailed work in lanes;
do not grow the concise contracts into implementation checklists.

## Required acceptance scenarios

| ID | Scenario | Evidence / falsifier |
| --- | --- | --- |
| Q1 | 2 browser tabs + 2 actual TUIs, viewing A/B with different drafts | Independent selection/input, one authority; any draft overwrite or wrong-target command fails |
| Q2 | Lose replies before/after admission and ACP dispatch; crash/restart host/agent | Exact command/native IDs, one accepted turn when established; uncertainty retained, never prompt replay |
| Q3 | Normal reconnect, replay overflow, lost/corrupt cache and missed catalog notifications | Correct snapshot/replay and catalog refetch, pending reducer reset, local intent reconciled separately |
| Q4 | 4,000 historical projects / 25,000 sessions, then 100,000 sessions with fixed active set | Filter before page/detail read; zero dormant agent starts and zero event-log parsing in catalog requests |
| Q5 | Root chat with large delegated tree, long transcript, big tool bodies; backfill; generic client omits view hint | Lazy children/body requests; standard state semantics preserved, memory measured; unresolved bound blocks strict claim |
| Q6 | Slow/uninterested/zero viewers; concurrent active work | Bounded queues/replay, unrelated details untouched, viewer loss does not stop execution |
| Q7 | Config inheritance/override with busy and idle agents; two dirty editors | Saved/effective/mounted revisions truthful; no lost local edit, no unrelated mounts |
| Q8 | Legacy native roots/children, external CLI owner, deleted/offline workspace, missing module | Resume/read-only/blocked classifications explicit; retained file checksums unchanged |
| Q9 | Copilot, current Codex ACP, Claude ACP through our host; Amplifier through Zed; AHP peer in each direction | Exact executable/version/auth/capabilities and real usage; README inspection alone fails the runtime gate |
| Q10 | Device/media and rich capability absent/present; same operation from user and agent | Correct targeted effect, fallback and authority; no blanket broadcast or replayed effect |
| Q11 | Each extracted core repo builds/starts with fixture peers | No sibling private imports, shared mutable checkout or live account needed for core development |
| Q12 | Upgrade, mixed compatible versions, rollback and new worker | Installed component and worker versions recorded; source/CI success alone fails adoption gate |

For Q4, vary projects and sessions independently (the earlier synthetic probe
varied both together). Example fixture: 4,000 historical project records, 100
existing directories, 25,000 total sessions, 1,000 relevant roots and many children;
then vary existing directories/root counts. Include missing/unknown parent metadata,
renames, out-of-order hints, offline mounts and concurrent catalog changes. These
counts are test data, not measured facts about the production host's relevant subset.

## Candidate budgets to measure and settle

These are proposed acceptance targets, not demonstrated performance or protocol
requirements. Record hardware, filesystem, network, p50/p95/p99, sample counts,
history sizes, active workers and client counts. Separate cold from warm paths.

| Operation | Proposed gate |
| --- | --- |
| Private draft/edit/expand/scroll | Zero backend traffic/writes; local feedback p95 under 50 ms |
| One scalar host read / one metadata update | No historical scans or whole-state copy/hash/checkpoint; direct resource access |
| Durable command acceptance on controlled local/LAN setup | p95 under 200 ms excluding cold agent start/model work; immediate truthful pending UI |
| First indexed catalog page | p95 under 200 ms on stated local fixture; default page 50, configurable bounded cap |
| First selected history page | Candidate p95 under 300 ms warm / 1 s cold local storage; no runtime initialization |
| Fixed active set, 25k -> 100k stored sessions | Explain indexed-query growth; no linear historical traversal on interactive updates |
| Cache and replay residency | Enforced byte/count/age budgets, documented defaults, no growth after churn settles |
| Concurrent clients/workers | Test 1/8/32 clients and 1/8 active sessions, plus one slow subscriber; measure CPU/RSS/queues rather than assume universality |

Derive concrete memory and idle-worker defaults from measured processes. A fixed
worker count alone is inadequate because provider/module footprints differ.
Instrument client event -> local paint -> host queue/admission -> durable write ->
ACP delivery -> resource publication -> subscriber receive/reduce -> paint. Report
model-first-token/cold-start separately so “send felt slow” can be localized.

## Safe migration without permanent dual architecture

1. Create versioned native identity mappings and rebuildable catalog side by side
   with current data. Initial index import is throttled, restartable and observable.
   Preserve all native files and keep missing-directory archives off ordinary lists.
2. Move private state into clients with a one-time read/import of existing
   client-scoped records. Existing local data wins; import ambiguous views only
   with a visible choice. Record successful import before stopping legacy writes.
   Retain a recoverable export until the migration window closes; no silent draft loss.
3. Add ACP/AHP routes under an explicit supported transport/version boundary.
   A legacy facade may serve old clients temporarily, but invokes the same host
   commands; it must not be the implementation beneath a supposedly incremental
   AHP endpoint. New clients never depend on the legacy full-state projection.
4. Classify old sessions: native-resumable after qualification, owned elsewhere,
   readable but missing a compatible engine/module, or invalid with a reason.
   Do not change their engine implicitly. If a format must break, retain read/export
   and describe the exact unsupported resume set before retiring it.
5. Cut over an owned isolated development instance with production-shaped synthetic
   history. Complete Q1–Q11 and feature migration accounting. Then qualify an owned
   isolated instance following its development guide; never use the production host as a lab.
6. Release compatible components, verify actual installation and fresh workers,
   and separately qualify production activation. Current work does not deploy.
   Roll back components only when their storage/capability compatibility allows it;
   preserve new native writes and mappings, never restore old snapshots over them.
7. Remove obsolete global-state paths and legacy transport after the published
   support window and adoption evidence. Until then, record its separate cost and
   users; do not claim it has the new path's scaling properties.

## Decisions deliberately left to implementation evidence

Host language/SDK choice, exact idle-memory defaults, minimum supported peer
versions, which advanced controls can use existing upstream extensions, and the
last unsupported legacy resume cases. None blocks L0 fixture work. Record their
resolution with evidence rather than silently inventing protocol fields or
discarding supported product behavior.
