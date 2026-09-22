# Workspace Experience Contract — v1 (ACCEPTED DIRECTION)

How users find work, start simply, and inspect deeper details without disturbing a chat.

## Who builds against this

The navigation and conversation interfaces render the workspace services.
Information workers, developers, assistive-technology users, and agent clients
consume the same identities and actions through different levels of detail.

## What it is

The sidebar locates conversations and workspaces. Contextual details locate
outputs, sources, helpers, changes, and previews belonging to the current scope.
Viewing a detail is not an execution or authority-changing action.

```text
New chat       Search
Pinned         selected conversations
Workspaces     workspace → recent conversations
Recent         unpinned conversations without a workspace

Chat details → Outputs · Sources · Agents · Changes · Preview
Workspace details → Files · Chats · Directions · Settings
```

## The promises

1. **First use stays concise.** Global New chat offers No workspace, existing
   workspaces, Create new workspace, and Use existing folder in one picker.
   Creation/attachment returns to that unsent draft with the workspace selected.
   Attachment asks for a folder, never a developer mode or a redundant preview.

2. **Navigation avoids unnecessary duplication.** A conversation has one default home;
   pins take precedence. Helpers stay with their parent chat. Empty groups and
   irrelevant technical sections are absent from an information worker's sidebar.

3. **Details preserve conversation state.** The existing Canvas hosts inspection
   while retaining its tabs. Opening a source, output, helper, or preview leaves
   the user's draft and execution scope unchanged. Sending an inspected item as
   context requires a separate action; passive browsing cannot send a message.

4. **Advanced controls reveal actual state.** Developer details expose full paths,
   repositories, revisions, owners, and bindings from authoritative records.
   The simple summary and detailed view cannot disagree about what is running.

5. **Actions share one authoritative path.** UI, TUI, and agent operations use
   the same service permissions and receipts. Cross-chat steering requires real
   user authority; toggling developer details grants no extra capability.

6. **Attention describes a concrete event.** Working, unread result, needs input,
   failure, and unknown effect have distinct accessible labels. Parent activity
   does not imply a helper finished, and a stopped run does not imply rollback.

7. **Small screens keep essential actions.** Narrow layouts replace side panels
   with a focusable full-width view and a return action. At 320 CSS pixels, create,
   inspect, stop, and return remain reachable without horizontal page scrolling.

8. **Available work stays distinguishable.** Missing folders are absent from the
   normal available-workspace list but reachable through history/recovery. Duplicate
   names gain a host/path qualifier. Full paths remain available in details.

## Not in v1

- **Custom sidebar sections** are promoted after users need organization beyond
  pins and workspace grouping; they remain an advanced extension.
- **Deep nested workspace hierarchies** are promoted by repeated demonstrated
  retrieval failures. Repository and agent trees do not become sidebar defaults.

## How the kit checks it

- P1: choose, create, attach, and cancel from New chat; retain its draft throughout.
- P2: pin/unpin and expand/collapse; compare visible identities for duplication.
- P3: open every details view with an unsent draft and inspect retained state.
- P4: compare simple/advanced projections with the recorded execution snapshot.
- P5: repeat creation and steering through UI and agent surfaces; compare authority.
- P6: inject working, unread, attention, failed, and unknown states; inspect labels.
- P7: keyboard-walk the 320-pixel and desktop layouts, including panel return focus.
- P8: remove/restore a folder and display two same-named workspaces on different hosts.

## Open questions

- Should pinned workspace conversations be suppressed from their expanded workspace
  list by default, with a small link indicating that they appear in Pinned?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-09-22 | Initial navigation and disclosure rules. | Live Spark PWA inspection, supplied sidebar/attachments screenshots, and the user's request for a simpler default experience. |
| 2026-09-22 | Simplify attachment and add the New chat workspace picker. | User's critique of redundant folder confirmation and developer options. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
