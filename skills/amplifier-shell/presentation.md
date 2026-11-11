# Customize an Appearance

[Appearances](../../docs/shell/appearances.md) is the product umbrella for themes,
layout, registered components, viewers and interaction feedback. Follow the
installed contracts below; do not invent a single appearance-package API.

1. Call `shell.inspect` for the target client. Record its revision, composition,
   package availability and browser status. Query affected instances with
   `shell.query`. Use the current schemas rather than copying an old payload.
2. For module order, supported layout, or extension configuration, form
   a complete proposed composition preserving unaffected instance IDs and
   settings. Prepare it with `shell.changes.prepare` and the inspected revision.
   Existing modules can have multiple instances where their contract permits.
3. Preview the returned change, inspect its browser report, then apply within
   the user's requested scope. Preview does not commit a composition. For a
   custom CSS delta, discover the appearance and theme services' current actions.
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

Reference is always loaded; a theme package supplies only its intentional CSS
delta over `reference-v1`. Discover `theme.*` for shared library authoring/import/
export, and `appearance.*` for selection, scope, preview and conditional undo.
Do not submit a complete skin or use library operations to select a look.
Shared Reference/component fixes are authored in the selected `amplifier-client-web`
repository, not Unified's delivery snapshot or disposable prepared client. Follow
the source-ownership section of the linked preference/package guide.

One **Global / This browser** selector chooses the complete active preference
profile and write destination. Inspect `appearance.preferences.read` and preserve
unrelated values. First use copies resolved Global settings; later switches retain
both independent profiles. No saved browser field inherits later Global changes;
`false` and empty collections remain meaningful values. Returning to Global never
publishes or deletes the retained browser profile. A browser-local command is not
committed until that admitted browser generation acknowledges successful
persistence. Storage failure never becomes a Global write.

Composition still has its own revision, dirty-state fences and lifecycle.
Discover which presentation fields the installed host routes to the browser-local
preference owner. Mixed changes must pass those fences before issuing a local
command and activate atomically only on its successful persistence acknowledgement.
Preview/cancel leaves committed preferences and private editor work intact;
conditional undo does not overwrite an intervening edit.

Discover actual target schemas before acting: documentation shipped with a newer
build does not upgrade an older host. See [the preference and package guide](
../../docs/shell/themes.md) for current boundaries and package rules.
