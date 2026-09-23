# Shared native history: verified behavior and design correction

Verified September 22, 2026 against `microsoft/amplifier-unified` main `fb1fe674a23e6d4957c87deabe156fa30df65a19`. This is source verification, not a claim that the currently running Spark PWA uses that exact revision. Source pointers below refer to that audited revision.

## The native path is already shared

`session_files.py` implements the CLI-compatible location:

```text
<AMPLIFIER_HOME>/projects/<project_slug(canonical-workspace-path)>/sessions/<native-session-id>/
```

`AMPLIFIER_HOME` defaults to `~/.amplifier` on the execution host/account. It is distinct from Unified's app data directory. `project_slug` resolves the path, replaces slash/backslash with a hyphen, removes colons, and ensures a leading hyphen. Spaces, periods, and underscores remain significant. This is the existing CLI algorithm, not the friendlier name-to-folder normalization used when creating a new workspace.

For `/home/bkrabach/dev/example`, the normal directory is `~/.amplifier/projects/-home-bkrabach-dev-example/sessions/<session-id>/`. Do not reverse-engineer the workspace path by replacing hyphens: different real paths can produce the same slug. The native index cross-checks metadata and known canonical paths and keeps ambiguity explicit.

Sources: [path and slug functions](https://github.com/microsoft/amplifier-unified/blob/fb1fe674a23e6d4957c87deabe156fa30df65a19/amplifier_web/session_files.py), [native history index](https://github.com/microsoft/amplifier-unified/blob/fb1fe674a23e6d4957c87deabe156fa30df65a19/amplifier_web/native_history.py).

## Existing CLI/TUI chats are discoverable

`NativeHistoryIndex` scans native project/session directories. `AutomaticHistory.refresh` reconciles the discovered sessions into navigation by `(nativeProject, nativeIdentity)` and reads their saved transcripts rather than copying a second history corpus. Known workspace paths help resolve older metadata.

The main chat list represents saved root conversations. Child/worker sessions belong under their parent. Empty diagnostic-only directories are not necessarily conversations. Archived/hidden state, unavailable folders, unreadable metadata, and ambiguous path mappings remain relevant; attaching a folder is not proof that every possible old format can be resumed.

For normal supported sessions written by CLI/TUI to the same host/account history store, the expected experience is automatic appearance. There should be no “import chats” step and no second workspace-membership list that decides whether those native chats exist.

Source: [automatic native-history reconciliation](https://github.com/microsoft/amplifier-unified/blob/fb1fe674a23e6d4957c87deabe156fa30df65a19/amplifier_web/automatic_history.py).

## Current public identity still has a gap

The native index creates a deterministic app row ID from the project and native session ID while retaining `nativeIdentity` and `runtimeSessionId`. Web-created rows can also begin with an app-generated UUID. The code explicitly describes UI IDs as aliases.

However, `history_query.py` currently exposes `id` in its list response and resolves `read` against that app row ID. It searches sessions already admitted to the app catalog. It does not expose the native ID through that response's identity projection. Consequently, saying “there is already only one public ID everywhere” would be inaccurate.

The proposed correction is: expose the native ID to users and agents, accept it within an explicit host/project scope, preserve old app IDs as compatibility aliases, and use one native-derived history service for all clients. A caller should not need to discover and translate an app UUID to read a known CLI session.

Source: [current agent history API](https://github.com/microsoft/amplifier-unified/blob/fb1fe674a23e6d4957c87deabe156fa30df65a19/amplifier_web/history_query.py).

## What app storage is still for

A rebuildable index is useful for fast navigation. UI state also needs storage: pins, unread markers, unsent drafts, Canvas references, receipts, and presentation preferences. These can be keyed to the canonical native SessionRef without becoming a competing session authority.

Existing web-only messages, voice exchanges, and artifact references must survive migration. The current reader can combine native messages with unmatched web/voice messages that have not reached a checkpoint. This is evidence that deleting or ignoring the app database today would lose information. The transition needs lossless shared-history representations or versioned extensions keyed to native identity, with old data retained until verified.

New unsent drafts may have transient client IDs. They are not saved native sessions and should not appear as empty saved chats. A command receipt can have its own ID without becoming another conversation ID.

The [history contract](../contracts/history.v1.md) makes these boundaries explicit. The [execution contract](../contracts/execution.v1.md) separately governs file editing: ordinary workspace chats use their selected folder; independent task work can have an observable separate working copy without relocating native conversation history.
