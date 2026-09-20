# Build an interactive surface for this conversation

Read [the surface contract](../../docs/shell/conversation-surfaces.md) before
authoring. Discover `canvas.apps.*` action schemas from the running app.

1. Create self-contained HTML, a version-1 manifest, and meaningful initial
   state. Put user choices and editable values in typed shared state. Declare
   semantic events with host-side field mappings so both user and agent can
   invoke the same interactions.
2. Call `canvas.apps.create` once. Save its returned ID. Use that ID for all
   later reads, events, patches, revisions and restoration. Do not repeatedly
   call `canvas.show` or `create` when improving this interface.
3. Bind controls through `canvasApp.ready`, `subscribe`, `emit` and `patch`.
   Show errors and retain unfinished input. Use semantic labels, keyboard
   controls and inherited theme/accessibility context. Use `setDirty` for
   unsaved work beyond ordinary form inputs.
4. Inspect current state and both revisions before acting as the user or
   refining the design. Use `canvas.apps.event` for the same interaction as a
   click. Revision conflicts require reconciliation, not blind overwrite.
5. Refine with `canvas.apps.revise`. Preserve compatible values. If migration
   is needed, retain user work in `migratedState` and submit both expected
   revisions. Never clear a dirty view on the user's behalf. Restoration
   changes the design while preserving current compatible inputs.
6. For a host change, declare and queue a supported request. Preview/apply/
   revert themes use the host's normal validated action path. Prefer small
   `{name, tokens}` palette requests; the host preserves the existing skin, so
   do not copy its entire stylesheet into the surface. Pending requests
   do nothing until reviewed and resolved outside the sandbox. Agents can
   resolve already-authorized changes on the explicit target client; the
   surface itself cannot grant permission.
7. Verify the actual running view: one tab after refinement, user/agent parity,
   visible theme effects, retained state on refresh, intact chat draft/history,
   and a usable revert path. Distinguish deterministic tests from an actual
   model-generated demo.

These surfaces serve the session's task. A custom theme chooser is a useful
example to generate on demand, not a built-in app to ship. Publishing,
downloads and app catalogs are separate future concerns.
