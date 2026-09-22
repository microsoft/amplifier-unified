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

## Existing mechanisms retained

Managed Git worktrees use `amplifier_worktrees`, with original source manifests, exact revision checks, explicit dirty-copy support and preservation-aware cleanup. `amplifier_web/worktrees.py` keeps history home separate from execution directory and records pending/applied/unknown handoffs. Native session ownership is enforced by the shared Foundation session protocol. None of these actions run merely because a folder is attached.

## Remaining contract work

- Workspace home with Files/Directions and repository bindings; attributed, versioned shared directions and instruction provenance.
- Sources, helper reports and preview ownership in the retained Canvas details surface.
- Folder-level conflicting-writer coordination across different sessions and isolation of writing helpers. Native session ownership alone does not prevent two different sessions from editing the same file. The app does not yet claim that guarantee.
- Multi-repository task environment/port bindings, combination validation, one integration/preview owner, and partial publish reconciliation.
- Cross-host transfer remains the separate portability implementation's responsibility.

Those are separate runtime/context conformance gates from the placement journey. Do not infer their completion from a green placement/browser suite. Existing terminal and editor edits remain visible at the chosen folder; no hidden checkout isolation was introduced.
