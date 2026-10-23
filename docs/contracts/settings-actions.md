# Settings navigation and AI connection drafts

The bundled browser and native app tool use the same public Settings actions.
These actions navigate a client and prepare a connection draft; existing provider
actions remain the only owner of configuration writes, authentication, and model
requests. A connection draft is not a saved or tested connection.

- `settings.open` chooses a named global Settings page and an optional `canvas`
  or `dialog` surface. Reopening reuses the live Settings artifact.
- `settings.close` closes visible global Settings and preserves unrelated Canvas
  tabs. Private unsaved browser fields retain the browser's confirmation flow;
  a host receipt alone does not prove that the browser has closed the surface.
- `settings.connection.services` lists supported service choices. This is a
  catalog, distinct from installed modules and saved connections.
- `settings.connection.start` opens the service choices for an explicit owner.
- `settings.connection.select` chooses a catalog module, creates its connection
  draft, and queues existing `providers.credentials` discovery for exactly that
  configuration owner. It returns the draft identity and exact pending credential
  operation ID. It never saves a provider, accepts a key, or starts authentication.

Connection actions default to `{kind: "global"}` and global scope. A workspace
target requires a registered existing workspace and local or project scope. Its
draft is stored separately from global Settings. Discovery results carry that
same owner; browser forms must never consume another owner's credential result.
Saved chats retain their independent runtime configuration.

Agent navigation binds the originating connected browser, or an explicit
connected browser displaying the calling chat. Ambiguous, detached, or changed
clients are rejected, including a navigation race while waiting for the action
lock. Existing dirty Canvas guards apply before replacement. Command IDs retain
normal exact replay semantics, including returning the same queued discovery ID.

The bundled app tool opts into compact dispatch receipts with `responseMode` set
to `receipt` on the bridge operation. Receipts retain admission, domain outcome,
errors, correlation and relevant scoped readbacks; they omit the unrelated app
overview. Explicit public bridge requests retain their default response contract.
An accepted asynchronous action does not prove completion or browser rendering.
