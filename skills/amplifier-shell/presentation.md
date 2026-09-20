# Change existing presentation

1. Call `shell.inspect` for the target client. Record its revision, composition,
   package availability and browser status. Query affected instances with
   `shell.query`. Use the current schemas rather than copying an old payload.
2. For module order, visibility, supported layout, or presentation tokens, form
   a complete proposed composition preserving unaffected instance IDs and
   settings. Prepare it with `shell.changes.prepare` and the inspected revision.
   Existing modules can have multiple instances where their contract permits.
3. Preview the returned change, inspect its browser report, then apply within
   the user's requested scope. Preview does not commit a composition. For a
   custom CSS skin, discover the theme service's current actions and use those.
4. Reinspect after activation. An `awaiting-browser` receipt is not completion.
   Confirm the matching revision mounted, and verify the intended interaction.
   If stale, inspect and prepare again; do not repeatedly force old revisions.
5. Exercise unsent drafts, chat switching, selected conversations and any dirty
   editor/viewer affected by the change. Retain the change ID and current
   revision for `shell.changes.revert`.

**Success criteria:** The intended client shows the change, host-owned
conversation/draft data is preserved, and revert restores the prior composition.

For canvas work, use `canvas.views.inspect` to obtain exact view/resource IDs,
resource revision and generation. Activate a compatible viewer through
`canvas.views.renderer`, not the navigation composition. Include the current
target fields required by each action. The host currently supports up to two
artifact views; live MCP App bindings have a separate primary-view constraint.
Read the canvas sections of [the shell guide](../../docs/shell/README.md) before
changing viewers, dirty state, or recovery.

Do not promise arbitrary new slots, replacement chat composers, or full
conversation decomposition: those need host implementation unless the current
action schemas and SDK explicitly expose them.
