# Unified workspaces

A workspace is a named home for related conversations and files. Create one by name, or attach a folder without moving it. Saved chats come from the shared native Amplifier store.

The design direction was accepted on 2026-09-22 after the attachment flow was simplified. The vision describes the intended end state; contracts separate its promises and failure behavior. [Implementation status](IMPLEMENTATION.md) distinguishes code and evidence from remaining work.

- [Vision](docs/VISION.md)
- [Placement](contracts/placement.v1.md): roots, naming, attachment, retry and preservation
- [History](contracts/history.v1.md): native identity, discovery, compatibility and recovery
- [Experience](contracts/experience.v1.md): navigation, details, accessibility and shared actions
- [Context](contracts/context.v1.md): repositories, instructions and context provenance
- [Execution](contracts/execution.v1.md): folders, concurrent work and runtime bindings
- [Handoff](contracts/handoff.v1.md): integration, preview ownership and cleanup
- [Acceptance scenarios](notes/ACCEPTANCE.md)

## Using the workspace flow

In New chat, the Workspace picker offers No workspace, existing workspaces, Create new workspace, and Use an existing folder. Both setup flows return to the same unsent text, attachments, model and bundle. A workspace's own New chat preselects that folder. No saved chat or model turn is created until the first message.

Settings → Workspaces controls `settings.workspaces.defaultRoot`. An empty setting uses `<app-home>/workspaces` (`~/.amplifier-unified/workspaces` in a default installation). A Spark user can set `~/dev`; the server expands it using the server account. This changes future placement only. More options allows a parent folder override for one workspace. Display renames never move files.

Use existing folder takes a server folder path or uses the existing server folder browser. Ordinary work edits those files, visible from a terminal in that folder. Native CLI/TUI chats are discovered automatically. No repository, submodule, clone, branch or execution-mode choice is required.

The sidebar presents Pinned, Workspaces and Recent. All chats retains the existing search, archive, sort, activity and folder-explorer tools. Workspace paths are available in details; duplicate names are qualified, and Settings can show all paths.

## Shared actions

UI and agent callers use `workspace.list`, `workspace.prepare`, `workspace.create`, `workspace.add`, `workspace.select`, `workspace.rename` and `settings.update`. `prepare` returns a reviewed destination and a disposition: create, open, attach or blocked. `create` takes its `planId` and a stable transport command ID. A repeated plan cannot allocate a second folder, including after a lost acknowledgment. An interrupted allocation remains explicit and requires inspecting/attaching the existing destination.

Existing integrations may still use the legacy explicit-path `workspace.create` surface. The new UI uses prepared name-based creation and explicit attachment. `fromDraft: true` returns to the initiating unsent chat. Native IDs are accepted by chat actions, scoped with `nativeProject` when ambiguous. Legacy IDs remain compatibility aliases. History list/read responses expose `sessionRef` and `legacyId`; an unavailable discovery is reported as incomplete.

## Storage boundaries

`$AMPLIFIER_HOME/projects/<native-path-slug>/sessions/<native-session-id>/` remains the conversation source of truth. Workspace registrations contain location and display metadata, not a workspace membership roster. Native catalogs are rebuildable. Existing app presentation, voice, unsent inputs, artifacts and compatibility aliases remain intact; do not remove the app database as part of deployment.

Placement plans and receipts live under `<app-home>/workspace-placement`. They describe filesystem operations, not chat history. Existing worktree and execution-handoff services retain history home independently of the task working directory. Attaching a folder never silently creates or hands off to a worktree.
