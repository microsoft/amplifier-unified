# Workspace Placement Contract — v1 (ACCEPTED DIRECTION)

How a named workspace acquires a location without claiming someone else's files.

## Who builds against this

The workspace service implements placement. Web, TUI, and agent clients consume
the same resolved destination and receipt. A user choosing a name or attaching
a folder must observe the same result through each client.

## What it is

A workspace has a stable identity, a display name, and explicit host locations.
A creation plan resolves a destination before making files. Names are not paths.
The following action names describe the proposed surface, not today's API.

```json
{"action":"workspace.prepare","name":"Launch plan","hostId":"spark","mode":"create"}
{"planId":"p1","configRevision":4,"name":"Launch plan","path":"/home/user/dev/launch-plan","disposition":"create"}
{"action":"workspace.create","planId":"p1","commandId":"c1"}
{"workspaceId":"w1","revision":1,"locationId":"l1","receiptId":"r1","outcome":"created"}
```

## The promises

1. **A name is sufficient.** Creation needs one name when defaults are usable.
   The host/account default is `<app-home>/workspaces`, overridden by
   `workspaces.defaultRoot`. Requiring an ordinary user to enter a path breaks it.

2. **Host paths stay host scoped.** The selected execution host resolves `~`,
   permissions, and canonical paths. An unavailable root fails visibly without
   falling back to another host or directory, preserving the operator's intent.

3. **Existing folders require explicit attachment.** A known canonical location
   offers its existing workspace. An unregistered folder requires Use existing
   folder. Creation never silently adopts or overwrites either user's files.

4. **Identity survives a rename.** Display-name changes retain workspace and
   history identity and leave paths unchanged. A path-derived legacy identity
   remains an alias; a renamed workspace must not duplicate its saved chats.

5. **Creation retries preserve their result.** The same command and payload
   returns the same receipt. Changed plans, collisions, or uncertain allocation
   produce a refusal or unknown result, never a second folder on blind retry.

6. **Defaults affect future workspaces only.** Saving a new root changes later
   plans, not existing locations. Attach accepts supported existing layouts
   without moving files, initializing Git, or editing remotes for a developer.

7. **Allocation preserves filesystem boundaries.** Name normalization yields a
   child path inside the approved root. Traversal, reserved names, symlink swaps,
   and filesystem-equivalent collisions cannot escape the reviewed destination.

## Not in v1

- **Moving existing workspace folders** is promoted when an explicit relocation
  journey has preservation and live-owner checks, beyond changing a default.
- **Cross-host creation orchestration** is promoted after the portability service
  can prove destination access and account ownership; a selector is not proof.

## How the kit checks it

- P1: submit only a name against unset and overridden roots; inspect the result.
- P2: resolve `~` against a different host account; make that root unavailable.
- P3: test a known workspace, an empty folder, and a populated foreign folder.
- P4: rename and reopen legacy chats; compare identities, paths, and message IDs.
- P5: duplicate a command, race two clients, and stop between mkdir and receipt.
- P6: change the default and attach a dirty Git folder; compare original bytes.
- P7: exercise Unicode/case collisions, traversal, reserved names, and link swaps.

## Open questions

- Is a separate account override needed on multi-user servers in the first cut,
  or is the current installation's host account the only supported principal?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-09-22 | Initial candidate placement rules. | User requested a name-first flow, configurable root, and preservation of existing-folder workflows. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
