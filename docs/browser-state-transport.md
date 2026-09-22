# Browser state transport and idle work

Browsers opt into compact action receipts with `X-Amplifier-State-Transport:
delta-v1` and connect to `/api/events?transport=delta-v1`. Actions retain their
result, effects, idempotency receipt and revision. `stateRevision` and
`hostInstanceId` identify the authoritative state that must arrive before an
optimistic control settles. Shell composition receipts use a separate revision
domain and continue to synchronize through shell events.

Each event stream starts with a complete `state` frame. Later `state-delta`
frames contain a base revision, result revision and changed JSON paths. An array
is replaced when its length or record identities change. The browser applies
patches immutably, keeping references to unchanged branches. A reconnect always
starts with a complete snapshot; a mismatched baseline starts a fresh stream.
If an action's state does not arrive, waiting actions share one recovery fetch
after 1.5 seconds. This does not introduce a polling loop.

The stream has its own baseline: an independently fetched HTTP snapshot may be
ahead of it. Full snapshots remain the default for older clients and agent
callers. Historical retired usage records remain in authoritative storage;
browser projections retain aggregate accounting and omit that internal ledger.

Repeated identical view, visibility and Canvas mount reports acknowledge the
command without republishing the conversation catalog. Canvas renderer evidence
still validates the exact client, generation, resource and edit order. It does
not save unrelated conversation history.

Appearance controls paint an optimistic patch immediately. Saves are serialized
with revision checks; a failed save removes only that edit, preserving any newer
pending choice. Authoritative execution and agent actions keep their existing
admission and confirmation behavior.

Native history uses file notifications to invalidate the affected project.
Idle checks avoid rebuilding the catalog. Metadata/transcript changes and
directory creation or removal matter; runtime event logs and browser checkpoints
do not. A full stat reconciliation runs at least once per minute, manual refresh
forces reconciliation, and unavailable file watches fall back to stat discovery.
Activity projections independently avoid rereading unchanged event files and
rebuild when a canonical association or lifecycle input, file identity, size,
timestamp or child changes. Drafts, streamed text and worker progress labels do
not invalidate the canonical activity cache. Transcript changes are checked as
well as event-file changes.

Tool and worker progress share the 250 ms publication batch used by streamed
text. Delivery acknowledgments, messages, approvals, errors and lifecycle
completion still publish immediately. Diagnostics capture continues normally,
but record counts and oldest/newest timestamps publish in the background only
while a connected client is inspecting Diagnostics. Errors and dropped-record
changes are still published when that panel is closed.

## Acceptance

`npm run test:state-transport-browser` starts a disposable real HTTP/SSE server
and serves the production build. Its synthetic catalog and initial state exceed
3 MB. It measures actual action and stream bytes, checks immediate painting with
500 ms delayed writes, verifies failed-save rollback and reconnects a fresh
browser. It makes no model calls. This is a local browser acceptance gate, not a
claim about a production host's observed CPU or installed build.

The Python transport tests cover a real event stream, duplicate commands and
legacy full-state clients. Native history tests cover real file notifications,
watch failure, late metadata, external workers and missed-event reconciliation.
Event-log tests cover idle reuse, append and atomic replacement. The complete
usage ledger remains intact in the projection regression test.
