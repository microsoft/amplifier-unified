# Conversation library

Archive, restore, managed-chat deletion and sharing use the same validated
actions from the sidebar, conversation settings and `app_control`.

`session.archive` hides a root conversation from the default active list.
`session.restore` returns the same conversation. Neither stops work, starts a
worker, selects a different chat, changes a draft or edits native history.
Archived active work can still produce a response or attention item. Worker
histories stay with their parent and cannot be independently archived.

Workspace conversations support Archive and Restore. Chats created without a
workspace also support permanent Delete after reviewing the exact deletion
scope. `session.deletePreview` supplies that review, and `session.delete`
requires its confirmation token. See [managed chats](MANAGED-CHATS.md) for
ownership checks, independent copies and deletion recovery. The old Remove
action is no longer exposed.

The sidebar exposes active, archived and all-conversation filters. A filter
change does not select or resume a conversation. Collections are retired:
their controls and actions are removed, and legacy stored membership is
preserved but ignored. Explicit `session.pinOrder` preserves a user-defined
pin order; otherwise existing pin recency ordering remains unchanged.

Shell module views use `shell.view.update`; legacy defaults use `view.update`.
App SQLite stores organization separately from shared Foundation metadata and
transcripts. Native archive references remain retained across catalog refresh
and restart. Browser projections include bounded chat pages rather than the
entire library.

## Immutable sharing

1. `session.sharePreview` freezes the readable export: public user/assistant text,
   labelled voice exchanges and artifact/attachment references. Tool payloads,
   hidden instructions and unsent drafts are omitted. Referenced file/interactive
   artifact contents are not embedded. Source text is not automatically redacted.
2. Inspect the preview. `session.shareRead` pages the same immutable bytes and
   content hash. A preview alone creates no publicly reachable link.
3. `session.shareCreate` accepts the exact preview ID/hash and explicit
   `visibility: anyone_with_link`. Expiry defaults to seven days; the schema
   permits 60 seconds through one year. The result is a `/share/<token>` path;
   the browser combines it with the current host origin. This does not upload
   to a cloud sharing service or make an unreachable local host reachable.
4. `session.shareList` reports preview/shared/expired/revoked status.
   `session.shareRevoke` invalidates the link without removing the source or
   replaying any work. Recipients may already have copies; revocation cannot
   recall them. A revoked/expired snapshot cannot be republished accidentally;
   intentionally create a new preview to share again.

Public snapshot routes grant no API or file access. Each link has a random
256-bit bearer token. Every request checks expiry/revocation. The page renders
escaped inert source under a restrictive CSP with no scripts, external images,
forms or framing. Responses use no-store/no-referrer and no-index directives.
Snapshot data is content-addressed and retained by resource cleanup independently
of the source chat's later edits. Snapshot size is capped at 10 MB, with a visible
error instead of silent truncation. Existing app authentication and host/origin
boundaries remain in force outside this deliberately published route.
