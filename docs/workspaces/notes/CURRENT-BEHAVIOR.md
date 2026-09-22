# Verified current behavior

These findings are from Unified main `172fee399ba0b35a78e3c5b914d38bb1457530a6`, queried on September 22, 2026. They are not a claim that every deployed Mac/Spark instance runs this revision.

## Folder-based creation: confirmed, with timing details

The New chat form offers **No workspace** and **Workspace**. Workspace mode presents a directory field. Merely opening or editing the draft does not create a folder or save a chat.

On first submission, `session.create` prepares the explicit workspace path with `_create_workspace_folder`. That helper resolves the path, creates missing directories with `exist_ok=True`, rejects an existing file, and never removes pre-existing files. `_new_session` then records that directory. Selecting the resulting session calls `select_session_workspace`, which creates a registration for that canonical path if no matching registration exists.

There are also separate `workspace.add` and `workspace.create` actions. Add requires an existing directory. Create makes or chooses a directory and registers it; it selects an existing chat there or opens an unsaved draft when none exists.

Consequently, the user's description is correct for the New chat path. Creating a workspace through that path happens on first submission, not when the directory text is typed. Existing directories are currently reused directly.

Source pointers at the audited revision `172fee399ba0b35a78e3c5b914d38bb1457530a6`: `frontend/src/chat-location.jsx`; `amplifier_web/service.py` around lines 946–985 and 1187–1205; `amplifier_web/workspace_canvas.py` functions `_registration`, `select_session_workspace`, `_create_workspace_folder`, and `workspace_command`.

## App-owned chat storage: confirmed

Managed chats use `<app-data-dir>/chats/<UUID>/files`. Their allocation record is alongside `files`, in `managed-chat.json`. Retry identity is tied to the creation command; existing allocations must match the expected record. These locations are storage ownership boundaries, not security sandboxes.

The normal app home is `~/.amplifier-unified`. `AMPLIFIER_WEB_HOME`, the legacy `AMPLIFIER_WEB_DATA_DIR`, or the launcher's explicit `--data-dir` can change it. The normal managed-chat file path is therefore `~/.amplifier-unified/chats/<UUID>/files`.

Managed chats remain outside the workspace navigation catalog. The UI tells the user: files created in this chat are saved by Amplifier. This provides a good precedent for a managed default location for named workspaces, but the inspected creation path does not implement name-to-folder allocation or a default workspace root.

Source pointers: `amplifier_web/managed_chats.py` (`allocate`, `creation_identity`); `amplifier_web/host/config.py` (`app_home`); `amplifier_web/cli.py` (`_data_dir`); `amplifier_web/new_chat.py`.

## Existing foundations to preserve

- Local worktree handoff separates canonical history home from the execution directory, uses durable receipts, and blocks unresolved transfers. Cross-host transfer remains outside that feature.
- Coordination already has scoped list/wait/follow-up/interrupt actions, stable result IDs, and explicit unknown-effect handling. It does not automatically authorize unrelated conversations to command one another.
- Existing folder registrations derive identity from canonical path. A new stable workspace identity therefore needs migration aliases; rewriting historical native session locations would be a regression.
- Workspace removal unregisters without deleting folders or chats. Missing locations remain recoverable through retained history.

The proposed contracts extend these foundations. They do not replace existing authority or lifecycle journals with another competing system.

## How the screenshots inform the proposal

The supplied images show a restrained sidebar with pinned chats, expandable projects, recent chats, and quiet attention indicators. A small session attachments surface exposes outputs, sources, pull requests, and subagents. Selecting subagents opens a right-hand panel; selecting an individual agent drills into its own report without leaving the parent chat.

The proposal retains this progressive disclosure. It avoids copying the unrelated top-level product catalog or showing Git details for ordinary document work. Text appearing inside the supplied screenshots was treated as example content, not authorization to resume, deploy, or change other work.

## Live PWA appearance, inspected separately

After the user identified the actual design target, the running **Amplifier** PWA on this Mac was inspected at `spark-1:8443/`. Its source revision was not inferred from its appearance.

Observed: a light grid shell, teal accents, monospace headings, rounded chat/composer surfaces, a Your work sidebar with Workspaces/All chats toggles, archive/location/search/activity filters, pinned and recent lists, and a right-hand Canvas. Chat details and Subagent history currently open as centered modals. The new-chat draft showed No workspace and Workspace; Workspace revealed a path and Browse control. The composer retained model, Work bundle, attachment, and voice controls.

The proposal's mockups were revised against these observed surfaces. The ChatGPT/Codex screenshots inform organization and inspection behavior rather than the target app's styling. Moving routine details into the existing Canvas region is a proposed change; it was not observed as current behavior.

Only navigation and inspection were performed. No message was submitted, folder created, setting saved, or active worker interrupted. The original conversation was restored with Canvas closed.

## Follow-up: native session authority

The later history/identity review at `fb1fe674a23e6d4957c87deabe156fa30df65a19` is recorded in [NATIVE-HISTORY.md](NATIVE-HISTORY.md). It confirms native CLI-compatible paths and automatic discovery, while identifying the remaining app-facing ID alias gap. The revised proposal makes the native store authoritative and removes execution-mode choices from folder attachment.
