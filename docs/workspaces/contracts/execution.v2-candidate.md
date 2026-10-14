# Workspace execution v2 — candidate

Status: exact clause proposal encoding the [approved direction](collaborative-workspaces-decision.md), not publication of a replacement contract. Target: `execution.v1.md` as read at source `2e310274d3e401200d930c9e7a7e900563eaeaa1`. The accepted target remains unchanged. Newly authored normative wording needs the applicable decision/publication checks.

## Evidence and reason

The user reported effective cross-chat coordination in their OpenAI workflow and asked Unified to adopt a productive collaboration model, not make checkout locking its prerequisite. Read-only inspection of that installation found independent root task conversations, substantial recorded peer messaging/waiting, and command-specific worktree locations while the chat's starting directory stayed a shared project root. These observations establish actual use and placement flexibility, **not** conflict-free shared editing or measured cost of serialization.

Unified's existing `IMPLEMENTATION.md` explicitly lists cross-session conflicting-writer coordination as unfinished. Removing the universal claim therefore need not remove an implemented lease here, but other consumers' reliance has not been exhaustively checked. The cost/trade-off is explicit: cooperative sharing permits concurrent work and avoids mandatory copies, but cannot guarantee prevention of competing edits. Stable verification still needs a preserved or quiesced candidate.

## Exact replacements

### Definition paragraph

Replace the paragraph beginning “A change set groups” with:

> A change set groups one piece of ongoing work across repository checkouts or document drafts. Its execution bindings carry a revision, explicit locations, and the declared sharing mode. A conversation has one native execution owner; that ownership is not a checkout-wide write lock. An isolated working directory is not a security sandbox.

Replace the two illustrative JSON records with:

```json
{"changeSetId":"change1","sessionId":"native-session-1","mode":"workspace-folder",
 "bindings":[{"repositoryId":"repo1","baseCommit":"abc123","checkoutId":"co1","sharing":"cooperative"}],
 "executionRevision":4}
{"helperId":"helper1","parentId":"native-session-1","access":"read-snapshot",
 "snapshotId":"snapshot1","scratchId":"scratch1"}
```

### Promise 3

Replace promise 3 with:

> **Sharing is explicit.** A checkout may be shared cooperatively by independent conversations. Its reported sharing mode must not imply enforced single-writer protection. Supported version-aware write APIs refuse stale-source updates; arbitrary shell programs and external editors remain outside that guarantee. A protected execution backend, when supplied, must report its actual enforcement and refuse stale owner generations before claiming protection.

### Promise 5

Replace promise 5 with:

> **Concurrency choices belong to tasks.** Ordinary work remains in the chosen shared folder. Independent tasks may coordinate repository/file responsibilities or use separate per-repository working copies when overlap, experimentation or stable verification calls for isolation. Each chosen path and baseline is visible. Creating or attaching a conversation does not silently relocate it, include unrelated dirty work, or require an execution-mode decision before discussion.

### Promise 6

Replace promise 6 with:

> **Bindings describe actual execution.** Preserve the canonical history/configuration home separately from default execution directory, per-repository checkout bindings and actual operation cwd. Shell commands, file tools, dependencies, tests and supported preview launchers must identify the binding actually used. A claim about a preserved snapshot must not silently import a peer's mutable dependency or test changing source. Ordinary explicit paths remain subject to the executor's actual permissions.

### Not in v2

Retain the hard-isolation limitation. Replace the automatic-background-relocation item with:

> **Automatic background relocation** requires explicit task-level authority and visible placement. A declared sharing mode or cooperative file plan neither grants new filesystem permissions nor substitutes for protected execution.

### Checks

Replace P3 with:

> P3: inspect two cooperative writers and verify their mode does not claim protection; race a supported version-aware file write and require an explicit stale-source result. For any backend advertising protected ownership, restart/reassign and reject writes with the old generation. An unsupported protection check remains unsupported, not a pass.

Replace P5 with:

> P5: run coordinated tasks in the chosen shared folder and an explicitly isolated multi-repository fixture. Verify actual paths, retained original work, declared sharing modes and versioned test inputs. No communication operation alone relocates source or claims isolation.

Other checks remain unchanged; apply the revised promise 6 to P6's source/runtime provenance check.

## What does NOT change

Native history and one execution owner per conversation; passive inspection; folder attachment preservation; bounded child assignments; scope and tool permissions; private drafts; original-document protection; exact-version and dependency evidence where claimed; deliberate integration, publication and cleanup; user stop/revocation; AHP/ACP interoperability. Peer collaboration does not convert independent chats into children or grant unrestricted authority.

## Migration and acceptance

### Supported file-tool source checks — 2026-10-14

Unified wraps the mounted reference filesystem tools and both reference patch
engines at the existing root/child app-access boundary. Successful file reads
record an in-memory content version for that native coordinator. Updating or
deleting an existing file requires that version; a stale or missing observation
requires a fresh read. Successful writes advance only the invoking coordinator's
observation. Restart, remount and native children begin without inherited file
observations. Directory listings and failed reads do not authorize replacement.
Creation requires absence; patch moves require an absent destination.
Function patches touch each resolved path once; combine changes within one
file operation instead of relying on sequential duplicate path operations.

Short per-file locks in one app data home serialize the comparison and supported
operation across worker processes. Patch calls preflight every source and move
destination before invoking their engine. This prevents stale-source rejection
after earlier patch operations have already run; it does not make a multi-file
patch an atomic transaction against engine, OS or process failure. On an uncertain
or failed mutation, invalidate affected observations and require fresh reads.
Cancellation releases locks and does not replay a mutation.

The lock/observation domain is bounded to these wrapped tools in one installation.
Existing path policies remain authoritative. Unknown tools, image generation,
shell commands, external editors, separate app homes, network-filesystem lock
semantics and externally changed path aliases are outside this guarantee.
Content versions detect changed bytes, not edit history or change-then-restore.
Reads may be paginated; observing a version does not assert that every line was
shown. Observations are bounded and evicted entries require another read.
No new transcript store, mandatory workspace lease or automatic merge is added.

Qualification must mount the real reference tools, exercise both patch engines,
race separate worker processes, cover child/remount separation and preserve the
existing read/write path restrictions. Source-only doubles do not establish
native-runtime coverage or installed-worker adoption.

### Active-work awareness and declared file intent — 2026-10-13

The host supplies a bounded, fresh projection of its observed active work in the
calling conversation's canonical workspace at each supported provider request.
User-created roots, commissioned roots and observed native children remain
distinct. Include actual recorded execution folders and declared file intent,
never peer drafts, prompts, transcripts or credentials. This projection uses
existing catalog/lifecycle facts without loading dormant histories or runtimes.
Historical metadata may require the existing catalog index to be rebuilt; a
steady-state read visits active work rather than every saved conversation.

A root may replace its own advisory file intent using the observed intent and
execution revisions. The user may edit a selected root through the same action.
Native children cannot replace their enclosing root's declaration. Declarations
are explicit shared data, normalized to known task folders when submitted; they
grant no filesystem access, block no operation, and imply no reservation or
stale-write protection. Empty paths clear the declaration. Stable command retries
retain their original outcome, and stale revisions preserve the newer record.
Declaration bodies live in the existing command receipts; session metadata
retains only the current reference and bounded summary. Historical declarations
therefore do not put every saved path list into the resident chat catalog.

Active peers with intersecting declared paths receive an overlap indication.
Separate copies retain distinct locations. A changed execution placement marks
an earlier declaration for review rather than silently translating its paths.
Idle declarations remain retained but do not claim current activity. External
writers, other app installations, unobserved workers and later filesystem alias
changes remain outside this observation. Partial/truncated coverage is explicit.
Client drafts remain private through refresh, conflict, navigation and reload.
Live awareness is ephemeral reference data and must not accumulate in native
conversation history or grant peer control. Dynamic observations are added at
the existing fitted provider boundary, not retained as native reminders. Each
request receives one current observation even when the loop retains reminder
text within a turn. Bridge failure means unknown coverage, never no peers.

### Explicit repository selection — 2026-10-13

A task may inspect, create or attach a working copy for an explicitly selected
repository inside its canonical workspace, current execution folder or an
already associated ready checkout. The surrounding workspace need not be a Git
repository. Relative repository paths resolve against the reported execution
folder; successful inspections return the absolute repository root to carry
into later actions. A different repository's checkout cannot be attached under
the selected source identity. Existing exact source revision and dirty-copy
rules continue to apply independently to each repository.

Creation and attachment command identities are serialized across repositories
sharing managed storage. Concurrent reuse with different contents must preserve
the first retained record and reject the competing request.

Selection and creation do not move the conversation or remap shell imports,
dependencies or file tools. Checkouts retain their repository identity, source
and location in the existing worktree owner. Choosing a default execution
folder remains an explicit handoff. Multi-repository execution mapping and
combined runtime provenance remain a separate, unqualified step. Root worktree
mutations require root authority; a child cannot relocate its enclosing task.

### Cooperative resource reservations — 2026-10-13

Explicit reservations extend the existing resource lifecycle owner. Inventory
observations remain observations; they never acquire or release a reservation.
An exact resource key has either one exclusive holder or explicitly shared
holders. Admission is atomic across workspace registrations in the same app
data home. Separate app installations and resources reached under different
keys are outside that guarantee. A reservation neither provisions infrastructure
nor fences arbitrary shell programs, external services or filesystem writers.

The host binds an agent reservation to its actual root/child principal. An agent
may release or mark uncertain only its own reservation; a user may explicitly
reconcile any retained reservation. Peer messaging grants no resource control.
Stop, disconnect, expiry and workspace removal never imply teardown or silently
release a held resource. Uncertain reservations continue to block new holders,
including shared holders, until explicitly reconciled. Release relinquishes a
claim with evidence and does not claim physical teardown.

Stable command IDs retain their exact results across restart, including release
and later reacquisition. Retries cannot resurrect an old reservation. Updates
require the observed revision. Admission is bounded at 64 held reservations per
root (including its children) and 1024 per app data home; existing holders remain
readable and releasable at the limit. Reads are paginated, passive and do not
load or resume conversations. Shared actions serve both clients and agents.

### Coherent commissioned-task operation — 2026-10-13

A new commissioned conversation has one durable creation handle. The host
captures its configuration and execution placement, reserves the requester's
return notification by default, and commits the initial request and that wait
before admitting execution. An explicit `returnNotification:false` permits
independent work without an automatic return. Inspecting the creation handle
resolves its initial request; callers do not construct subordinate command IDs.

Preparation phases distinguish a committed plan, requested root creation,
retained root and committed input. Restart may resume a proven unperformed step
or reconcile an existing exact receipt. An unproven root-creation effect remains
unknown and is never blindly repeated. Stop, changed assignment, changed
placement or revoked admission suppress remaining automatic preparation. Reads
and duplicate requests remain passive. Private captured configuration is never
included in public receipts or ambient coordination context.

### Approved initial repair implementation — 2026-10-13

The initial program separates defect repairs from larger design experiments.
The shared-folder default and independent-chat model remain unchanged. A peer
created from a moved task inherits its actual execution directory without
claiming a new isolated copy. Typed failure/deferral/decline and authenticated
runtime-failure observations may wake a saved waiter; waking is distinct from
successful dependency acceptance. Inbox capacity may reject new input with
explicit backpressure before any message is added, while reserved result slots
preserve accepted waits. Result navigation must verify the exact referenced
bytes and retain the producer's execution binding; absence or mismatch is an
explicit limitation. These additions are tracked in the implementation evidence,
not a claim that every v2 proposal or original v1 isolation promise is delivered.

No current universal checkout fence is known in this source baseline; this is not proof that all integrations are independent of the earlier promise. Existing explicit worktree handoffs, permission checks and preservation checks stay in place. Consumers must distinguish cooperative from genuinely protected bindings and report unsupported enforcement. The first collaboration increment should retain existing folder behavior, add honest context/coordination, and not claim newly enforced write isolation.

Before publication: verify target revision and exact payload, decision coverage, affected consumers/checks and dated changelog through the supported publication guard; preserve this proposal. A refusal stops publication, not ordinary already-authorized implementation. DTU and Fable findings will be recorded against the exact implementation revision; none are claimed by this candidate.
## Assignment control addition — 2026-10-14

New commissioned roots share an explicit host-owned assignment with their
commissioning root. Existing peers and historical commissioning records are
never enrolled retroactively. Native descendants share their root's admission.
Membership, limits and Stop are retained in existing command receipts; model
usage stays in existing execution receipts and is attributed at admission.

Assignment limits cover aggregate supported model calls and open commissioned
roots. Optional token/cost reservations are declared allowances per call, not
provider-enforced maxima. Settled receipts replace their allowances; missing
outcomes remain unknown. Limits cannot promise an exact spend cap. Already
running effects may finish. Admission is serialized before provider effects.

The commissioning owner may manage the assignment; members may inspect it.
Membership does not confer control over existing independent peers. Assignment
Stop closes admission durably before interrupting still-bound roots and their
native children. A stopped assignment is not automatically resumed by a late
reply, restart, schedule or another input. The user may explicitly detach a
conversation after interruption settles; retained usage and late corrections
remain attributable. Detaching never interrupts another conversation or erases
accounting. No cleanup or external resource teardown is implied by Stop.

The current adapter cannot interrupt or account for only one request inside an
independent peer's mixed conversation. Stop reports that limitation and suppresses
pending assignment input without substituting a whole-conversation interruption.
When a token or cost limit is enabled, new queue/steer requests to independent
peers are refused with this limitation; commissioned helpers share the budget.
Informational notices remain available and do not start independent work.

### Managed resource observations — 2026-10-14

The resource surface may project bounded, explicitly selected task records from
existing physical owners. The local Publisher owns static preview/site listeners,
OS-assigned ports, immutable release directories and stopped/interrupted receipts.
Operations owns recorded native process/kernel lifecycle evidence; a recorded
running state is an observation, not an independent process-liveness probe.
The projection must preserve these different evidence levels, omit commands,
output and environment, and never provision, replay, stop or reassign resources.
Each owner uses an indexed task-scoped page; inspecting one task must not load
other tasks or all retained history. Browser and agents use the same read action;
agents may inspect only their host-bound task, while users may select a workspace
task. Cooperative reservations remain separately labelled, with no inferred link
between a user-chosen key and a physical resource.

Dedicated scratch folders, test databases, dynamic application preview listeners
and browser profiles remain unallocated by this workspace adapter. A documented
working-files namespace is not provisioned isolation. Unknown operation cwd,
remote publishing state or resources outside these owners must remain explicit
coverage limits, never reported absent or safely isolated.
