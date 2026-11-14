# Shared Session History Contract — v1 (ACCEPTED DIRECTION)

How Web, CLI, and TUI find and continue the same saved conversations.

## Who builds against this

The shared Amplifier history service owns session identity and saved history.
Web, CLI, TUI, workspace navigation, and agent clients consume its records.
Users depend on seeing the same conversation regardless of which client created it.

## What it is

A saved conversation is a native Amplifier session in its host/account history store.
Workspace chat lists are projections of those sessions, not independently maintained
membership lists. Presentation metadata extends a session without creating another one.

```text
$AMPLIFIER_HOME/projects/<canonical-path-slug>/sessions/<native-session-id>/
  metadata.json
  transcript.jsonl

SessionRef = { hostId, accountScope, nativeProject, sessionId }
sessionId = the native session directory name
presentation[SessionRef] = { pinned, unread, canvasReferences }
```

**Windows exception for the app's own chat folders.** On Windows, a folder that
Unified allocated for a chat (`<app data>\chats\<chat id>\files`) uses the project
name `chat-<first 16 hex digits of the chat id>` instead of the path slug. The
path slug of such a folder repeats the installation path, and its session paths
exceed the Windows path limit while long paths are off, which is the Windows
default. Every folder that a person chooses, and every folder on other
platforms, uses the path slug. `nativeProject` carries the name in use, so
SessionRef is unchanged.

- The short name needs proof that the folder is the app's: its allocation record,
  or, after the chat folder is deleted, saved history under the short name that
  records this exact folder. The folder shape and the chat id alone are not
  proof; a copy of a chat folder in another place uses the path slug.
- Nothing is moved. A chat whose saved history is only under the path slug keeps
  that folder. That includes history longer than the Windows limit, written
  while long paths were on.
- When both names hold saved history for one chat (possible only after an update
  rollback), the chat uses the short name. The other folder is kept and stays
  listed, read-only, as a project without a folder.
- Saved history means a transcript, session metadata or the chat's own settings
  file. Capture and view files alone, such as those of a first message that
  failed, do not keep a chat on the path slug.
- A CLI or TUI started inside such a folder on Windows computes the path slug. It
  lists a chat kept under the path slug and does not list one under the short name.

## The promises

1. **Native sessions define saved conversations.** Existing native records decide
   which chats exist. Rebuilding an app index must rediscover them; requiring a
   separate app registration to reveal a valid CLI/TUI chat breaks the user's list.

2. **Public identity remains native identity.** UI details and agent actions use
   the native session ID within its host/project scope. Legacy app IDs resolve as
   aliases; a caller must not translate two competing conversation identities.

3. **Attachment discovers existing history automatically.** Selecting a folder
   reads its shared history on that host/account. Supported root sessions appear
   without import or copying; child sessions remain accessible under their parent.

4. **Indexes remain rebuildable projections.** Caches accelerate discovery but
   cannot define chat existence. Pins, drafts, receipts, and artifact references
   may persist by SessionRef; losing a cache must not lose a user's native chats.

5. **Clients share history and ownership.** Web, CLI, and TUI use the shared
   reader/writer and session ownership protocol. A second active writer must wait
   or receive an explicit busy result; the transcript must not fork silently.

6. **Workspace views retain native scope.** A display rename changes no native
   project or session path. Task working copies preserve recorded history home;
   changing cwd must not make the user's existing conversation disappear.

7. **Incomplete discovery stays explicit.** Loading, unavailable, unreadable,
   archived, and ambiguous-path states remain distinguishable from an empty list.
   A matching path slug alone cannot silently resolve two folders for the user.

8. **Migration preserves every saved exchange.** Legacy app-only voice, messages,
   and artifacts remain recoverable until losslessly represented in shared history
   or a keyed extension. Deleting the app database is not an acceptable migration.

## Not in v1

- **Cross-host transcript synchronization** is promoted through the portability
  contract after host/account identity and conflict behavior are verified.
- **Arbitrary cross-workspace reassignment** is promoted only with a native-history
  representation. A private app membership list cannot masquerade as that feature.

## How the kit checks it

- P1: rebuild only derived indexes and compare all native roots and their IDs.
- P2: list/read the same session by native ID in each client and by a legacy alias.
- P3: attach a folder containing CLI/TUI sessions; observe roots and child history.
- P4: rebuild caches while retaining keyed metadata; compare chats and saved pins.
- P5: attempt concurrent resumes from two clients; require one acknowledged writer.
- P6: rename a workspace and hand off execution; verify canonical history unchanged.
- P7: inject unavailable roots, corrupt metadata, and colliding native project slugs.
- P8: migrate a fixture with voice, artifacts, and unsaved runtime checkpoints; compare content.

## Open questions

- Which shared-history extensions already cover all legacy voice and Canvas data,
  and which need a versioned addition before the app's old records can be retired?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-09-22 | Make native history and identity authoritative across clients. | User's explicit source-of-truth requirement; Unified history code at fb1fe674a23e6d4957c87deabe156fa30df65a19. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
