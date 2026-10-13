# Implementation and evidence

## Coherent commissioned-task operation — 2026-10-13

New `coordination.create` operations retain one public handle and a private
captured configuration. The host reserves the requester's return capacity before
creating a root and commits the initial request, saved wait and completed
creation receipt together before execution can be admitted. Explicitly independent
work can use `returnNotification:false`. Result inspection through the creation
handle includes its exact initial request, without caller-generated subordinate IDs.

Restart resumes committed plans and reconciles root creation only through its
existing committed command receipt. It can then finish the known-unperformed
initial-input step. A missing acknowledgment leaves root effects unknown and
retained. Lost acknowledgment after input commit keeps the committed outcome.
Recorded Stop, changed assignment or changed execution placement suppresses
remaining preparation. Root identity and captured configuration are checked
before the initial brief. These are local preparation guarantees, not an
exactly-once promise for arbitrary tool effects or external systems.

The private preparation record contains configuration rather than transcripts,
uses existing provider credential references, and is removed when input commits.
Public receipts and ambient context never include that private record. Legacy
operations remain read-only evidence and are not retroactively made resumable.
Automatic preparation and refused/uncertain admission notices are host observations; they do not claim
an uncreated peer produced a report, satisfy a dependency, or authorize replay.

Local validation: 283 focused coordination, creation, recovery and inheritance
tests pass. All 19 new operation/recovery checks also pass against the built
wheel imported outside the source checkout. Three actual Core guidance probes
pass. The production-service Chromium journey completes two rounds for two
peers, using automatic first-request returns and creation-handle result reads;
its runtime and terminal evidence remain scripted. Wheel and source distribution
builds pass. These checks do not establish real-provider behavior, live-host
worker adoption, or the broader multi-client assignment experience.

## Initial repair tranche — 2026-10-13

The implementation program starts with reproduced defects and established
coordination mechanisms. It does not make the broader assignment/resource
architecture a release prerequisite. The baseline is `be1f1d3`; nine isolated
review probes reproduced the reported defects without touching live services.

| Owner | Implemented behavior | Evidence boundary |
| --- | --- | --- |
| Worker and native children | Each mounted child receives its own app adapter and transport principal, including finite/custom loops, persistent children, resumes and nested delegation. Nested children retain the assigning input context. Root-only controls reject a child principal. | Real Core/Foundation mounting with a deterministic orchestrator; no provider call |
| Host file policy | Filesystem/patch child allow lists intersect across parent, spawn, overlay and retained plans; denies accumulate. Explicit read-only declarations stay read-only. Repository paths follow the execution folder; unrelated external paths remain. Image boundaries remain covered. | Composition and host-policy regressions; no claim of shell or OS sandbox enforcement |
| Shared dispatch | Agent permission writes require an explicit existing scope. Root agents can still perform scoped configuration actions; settings are not made categorically human-only. | Direct-dispatch and app-bridge rejection tests; this is not a general capability-grant system |
| Collaboration | Sealed negative reports and host-observed failed generations notify the exact waiter once without satisfying dependencies. Queue admission reserves space for saved replies. A destination has 64 pending/reserved slots; each ordered sender/recipient pair admits 32 messages per minute. Retries retain their identities. | Deterministic admission, failure, duplicate, reservation and Stop tests |
| Creation and worktrees | A commissioned peer uses the creator's actual execution folder and keeps canonical history grouping. A failed read-only handoff preflight is rejected without fencing the source; uncertainty after release still fences it. | Temporary Git repositories and restart/failure injection; no shared host changes |
| Canvas and related work | Referenced bytes are hashed before presentation in the recorded producer execution folder. A changed or unavailable source is explicit. A verified preview retains its bytes independently of the source. User-created peers are visible alongside commissioned peers. | Actual service/Chromium journey with scripted runtime; no real-provider evidence |

The changed writer class was inspected across filesystem, patch and image
configuration, prepared plans, retained child plans and mounted app controls.
Arbitrary shell programs, third-party tools, and other agent engines remain
outside these configuration guarantees. Older artifact references without a
recorded execution folder remain inspectable as receipts but cannot silently
open guessed source bytes. Files are not automatically archived at reply time;
if they change before first opening, the exact-result view reports a mismatch.

The separate bridge cleanup change is tracked by PR #616; this tranche does not
duplicate that implementation. Source integration must verify its final status.
All generated web assets must be rebuilt from the combined candidate.

Local candidate validation: 797 Python tests and two subtests passed across the
affected coordination, runtime, storage, settings, worktree and Canvas families;
638 frontend tests passed. The production-service Chromium journey passed two
rounds with two commissioned peers, reconnect and exact artifact inspection,
including rejection after changing a reported source file while retaining the
existing view and draft. The native child tests used real Core/Foundation with
scripted orchestration. None of these checks claims provider efficacy, publication,
installed-worker adoption, or live-host acceptance.

Follow-up scope remains explicit: multi-repository resource bindings;
assignment budgets and scoped Stop across independent roots; full capability
grants; expiry/fair scheduling beyond the initial inbox bounds; live-provider,
installed-package and multi-client qualification. These are not marked complete
by the source regressions or the scripted browser journey.

## Collaborative-workspaces candidate

The approved same-host cooperation loop is **qualified for PR review**,
not a completed release or deployed feature. Current adapters support typed reply
declarations sealed against exact checkpointed native terminal evidence,
generation-anchored steering and one stable-ID saved dependency continuation.
Host qualification establishes an attributable agent claim plus observed terminal
proof, not independent artifact correctness. Multiple delivered requests in one
generation can legitimately share that terminal reference; they are not distinct
model answers. Unsealed staged declarations become unknown on restart, never
completed from retained text or anchors.
See [Task coordination implementation and evidence](../TASK-COORDINATION.md)
for exact revisions, earlier failures and acceptance limits. Passing deterministic
surface checks does not ratify replacement execution clauses or qualify later
code from an earlier model run.
Shared-folder operation remains cooperative, without enforced conflict prevention.
Actual-model created-task/adjacent-round/steer checks and Fable delta review
are recorded in the [final PR qualification](../TASK-COORDINATION.md#pr-qualification--2026-10-06),
with the exact revisions, optional skips and pre-existing smoke-test limit.
Durable pending grant proposals retain their exact
source and scope after the transient approval wait/restart and can receive a later
human decision without new chat text. Creation and saved waits fail before
effects unless queue and idle-start authority can admit the required turns.
Existing execution contracts and their decision/candidate evidence are unchanged.

Pre-integration bounded checks on `3b4e188dd03c88f88067d1928c06411ae0625538`:
5,041 Python passes, 111 explicit optional/integration skips, zero failures,
four subtests; all five groups used that same SHA with every collected node
accounted for. The final 265 focused and 440 frontend checks pass, the complete
deterministic browser loop renders the exact terminal, and native scripted/wheel
checks pass. All 95 rebuilt assets match the committed manifest. Exact commands,
skip scopes and the then-pending parent-owned actual-model/Fable gates are in
[the Fable continuation evidence](../TASK-COORDINATION.md#fable-findings-continuation-bounded-fixes-checked-parent-gates-pending).
Earlier failures below and in the linked historical sections remain evidence;
they are not the current source's verdict.

Integrated v0.20.51 source `8003efaf` passed 5,081 unique Python checks with
111 explicit skips and four subtests; 440 frontend checks, the coordination
browser flow and the startup browser checks passed. Rebuilt assets in
`c9c5e3af` reproduce exactly. The additional new-chat smoke's obsolete pre-chat
toggle assertion fails identically on pristine upstream `bbc07023`; its later
assertions are not claimed checked. This qualification does not publish the
replacement execution contract or remove the documented shared-folder limits.

The accepted vision is the target, not a claim that every execution and integration promise is already implemented. This change implements placement, native-history addressing, and the simplified navigation and draft journey. Existing runtime, worktree and handoff mechanisms remain in use.

## Implemented here

- Name-only placement under a per-host/account configurable default root; per-workspace parent override under More options.
- Explicit existing-folder attachment and automatic native-history discovery, with files left in place.
- Canonical location/name collision checks, pinned directory descriptors during allocation, durable plans and receipts, duplicate command and lost-ack retry handling, and explicit interrupted outcomes.
- Global New chat workspace picker, draft-preserving create/attach/cancel, workspace-local New chat, existing model/bundle/attachment controls.
- Pinned, Workspaces and Recent sidebar; empty available folders included, duplicate names qualified, optional visible paths; existing All chats tools retained.
- Native public IDs for newly discovered sessions, project-scoped native-ID actions, legacy-ID aliases and native SessionRef in the history API. Existing presentation and voice records are preserved.
- Shared UI/agent actions for the complete placement flow and setting.

The legacy explicit-path create action remains compatible for existing clients. It is not the name-only allocation path. Native-history catalogs are still derived indexes; this change does not migrate or delete legacy app data.

## Evidence

`tests/test_workspace_experience.py` covers naming, root changes, explicit attachment, existing bytes, symlink substitution, concurrent collision, lost acknowledgment, interrupted allocation, private draft attachments/model/bundle, native ID discovery/read, ambiguous IDs and empty-workspace listing. Existing creation, managed-chat, native-history, ownership, worktree, handoff and agent-control suites provide compatibility coverage.

`tests/test_workspace_placement_recovery.py` exits a subprocess immediately after the created placement receipt is durable and before app registration or its command receipt. On restart it checks changed directories, missing paths, leaf and parent symlinks with both the original and a fresh command ID. Refused recovery preserves registrations and placement receipt bytes/timestamps. An unchanged allocation registers once; an already committed app command returns its saved result without repeating placement.

`frontend/tests/workspace-experience-browser.mjs` uses the actual host, production assets and isolated files with a synthetic runtime. It exercises create, attach, cancel, collision/open, settings, a first send with retained attachment, and 320/390-pixel light/dark layouts. `new-chat-browser.mjs` retains coverage for model/bundle choice, reload, first-message delivery, voice/send/stop controls, history details/export, Canvas and client isolation. `new-chat-recovery-browser.mjs` checks failed first-send recovery and unsent inputs. Browser evidence does not claim real provider or deployed Spark acceptance.

Rendered design references under `mockups/` remain illustrative. They are not deployment evidence or screenshots of implemented future features.

Validation recorded on 2026-09-22:

- Full Python run after integration with main `1b56d652`: 3,183 passed, 130 skipped (environment/integration prerequisites).
- After the directory-identity and malformed-reference guards: 33 placement, agent-control and history tests passed.
- After the allocation-recovery correction: 81 workspace, placement, creation and managed-chat tests passed, including 10 subprocess crash/recovery cases.
- Frontend unit suite: 296 passed, including sidebar grouping, pagination, direct pin reordering in both navigation views, hidden-pin preservation, duplicate requests and rejected-receipt retry.
- Production frontend build passed on the integrated source.
- Workspace browser: 13 scenarios, including desktop, 320/390-pixel layouts, light/dark, draft/attachment preservation, real drag-and-drop and Alt+Up/Down pin reordering, retained keyboard focus and persisted order after reload.
- Existing new-chat and first-send recovery browser checks passed.

## Earlier release-candidate validation

The following historical qualification was performed against canonical main `69294c9d458951966944e8a3db8d33e6a27f4dae` after 0.20.7 publication. The candidate was held; the separate performance release subsequently used v0.20.8, so these workspace changes are not included in that published version. The reviewed pin-ordering and allocation-recovery corrections remain included. At that earlier checkpoint its package metadata used 0.20.8. The integration refresh preserves the published release metadata and stores the future feature notes in `RELEASE-NOTES.md`.

- Full combined Python suite: 3,199 passed, 130 skipped (environment/integration prerequisites), including the reviewed placement recovery cases.
- All 296 frontend unit tests and all 13 workspace browser scenarios passed.
- Existing navigation, mobile navigation, new-chat, first-send recovery, review-during-send, settings, settings completeness and mobile settings browser checks passed. Navigation now explicitly asserts that discovered chats use their native public ID; folder-browsing coverage follows the new attach journey and checks retained bundle choice.
- Compiled desktop readiness, memory personalization, publishing and portability workflow browser checks passed with their synthetic fixtures.
- The large-library browser check passed with 22,915 sessions, 3,932 workspaces, four browsers and one terminal stream. P95 latencies were 171 ms for health, 212 ms for admission and 192 ms for actions; drafts were retained and no model calls occurred. These are local fixture measurements, not Spark service measurements.
- Locked dependency resolution, reproducible production frontend build, wheel/source build and local distribution verification passed.

The shipping owner retains the immutable release qualification, merge, publication and deployed-host acceptance. These local results do not cover private companion-runtime release gates or live Spark adoption.

## Current-main integration refresh

Refreshed onto `7d139997ce9cf77cddbc6de4622dfdc0158661a8` after the separate v0.20.8 performance release. Source merged without conflicts; generated frontend assets were rebuilt from the combined source. Published v0.20.8 release notes remain unchanged, and pending workspace notes are in `RELEASE-NOTES.md`.

- 146 focused backend tests passed across workspace placement/recovery/creation, managed chats, automatic/native history, history queries and state transport.
- 298 frontend unit tests passed.
- Workspace experience, navigation, mobile navigation, new chat, first-send recovery, settings, draft switching, multi-client synchronization and state-transport browser checks passed against the combined production assets and isolated fixtures.
- This refresh does not establish deployed Spark acceptance.

## Existing mechanisms retained

Managed Git worktrees use `amplifier_worktrees`, with original source manifests, exact revision checks, explicit dirty-copy support and preservation-aware cleanup. `amplifier_web/worktrees.py` keeps history home separate from execution directory and records pending/applied/unknown handoffs. Native session ownership is enforced by the shared Foundation session protocol. None of these actions run merely because a folder is attached.

## Remaining contract work

- Workspace home with Files/Directions and repository bindings; attributed, versioned shared directions and instruction provenance.
- Sources, helper reports and preview ownership in the retained Canvas details surface.
- Folder-level conflicting-writer coordination across different sessions and isolation of writing helpers. Native session ownership alone does not prevent two different sessions from editing the same file. The app does not yet claim that guarantee.
- Multi-repository task environment/port bindings, combination validation, one integration/preview owner, and partial publish reconciliation.
- Cross-host transfer remains the separate portability implementation's responsibility.

Those are separate runtime/context conformance gates from the placement journey. Do not infer their completion from a green placement/browser suite. Existing terminal and editor edits remain visible at the chosen folder; no hidden checkout isolation was introduced.
