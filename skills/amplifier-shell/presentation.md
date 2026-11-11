# Customize an Appearance

[Appearances](../../docs/shell/appearances.md) is the product umbrella for themes,
layout, registered components, viewers and interaction feedback. Follow the
installed contracts below; do not invent a single appearance-package API.

1. Call `shell.inspect` for the target client. Record its revision, composition,
   package availability and browser status. Query affected instances with
   `shell.query`. Use the current schemas rather than copying an old payload.
2. For module order, visibility, supported layout, presentation tokens or extension configuration, form
   a complete proposed composition preserving unaffected instance IDs and
   settings. Prepare it with `shell.changes.prepare` and the inspected revision.
   Existing modules can have multiple instances where their contract permits.
3. Preview the returned change, inspect its browser report, then apply within
   the user's requested scope. Preview does not commit a composition. For a
   custom CSS skin, discover the theme service's current actions.
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

Discover registered contribution points in `shell.inspect.slots`; see
[component contributions](../../docs/shell/components.md). Do not promise
replacement chat composers or whole conversation decomposition unless the
installed schemas and SDK explicitly expose them.

## Themes

Load [appearance-authoring](../appearance-authoring/SKILL.md) for visual treatment
and portable assets. It links the interaction contract, current theme API and
examples; do not duplicate its styling procedures here. Components still own
focus, scrolling, responsive safety and action semantics. Theme selection does
not authorize other preference or runtime changes.

The user-approved PR1916 rollback temporarily retires Reference and Global /
This browser profiles after the `.078` visual regression. Follow the
[rollback boundary](../../docs/shell/appearance-contract.md#temporary-rollback-boundary):
newer saved data stays inert and undeleted, never converted or used to seed the
legacy path. Missing legacy themes use the safe old default, not a claimed
automatic recovery of the theme removed by `.078`.

Discover `theme.*`. `theme.preview` / `theme.apply` take a name and exactly one
of `definition` (a complete version-1 theme), `tokens` (an intentional partial
palette edit retaining the skin/artwork), or `css` (a complete portable skin).
Preview is client-scoped; apply commits app-wide. `theme.revert` ends a preview
first, otherwise conditionally reverses the client's last apply. Saving or
importing a library entry does not apply it. Portable CSS and format-1 ZIP
import/export are restored; format-2 Reference deltas are not active inputs.

Composition retains its revision, dirty-state fences and lifecycle. Change
scheme, decorations and other supported `presentation` fields through the
existing shell prepare/preview/apply/revert protocol, independently of the theme.
Keep previews, committed styling and private editor drafts distinct.
Browser/shared-component source changes belong in `amplifier-client-web`, not
Unified's selected snapshot or disposable prepared client; follow the linked
guide's source ownership and two-repository delivery requirements.

Discover actual target schemas before acting: documentation shipped with a newer
build does not upgrade an older host. See [the preference and package guide](
../../docs/shell/themes.md) for current boundaries and package rules.
