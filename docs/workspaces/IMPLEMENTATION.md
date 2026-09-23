# Implementation and evidence

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
