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
   Reference appearance delta, discover `appearance.*` and `theme.*`.
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

The accepted production redesign supersedes the PR1916 rollback: Reference plus
intentional deltas and one complete Global or This browser profile. Legacy themes,
`.078` profiles/libraries and experiment records remain inert and undeleted; see the
[preservation boundary](../../docs/shell/appearance-contract.md#temporary-rollback-boundary).

Discover `appearance.*` and `theme.*`. Use `appearance.preview` /
`appearance.preview.cancel` for document-local styling previews, and
`appearance.preferences.update` at the captured scope/CAS to select a library ID
or change preferences. First browser selection copies current Global; later
switches retain independent complete profiles, not field overlays. Storage ACK
and rendered composition ACK are separate; failure never writes Global as fallback.
Strict format-2 Reference ZIPs are the portable envelope; retired complete-theme
actions, raw CSS import and format-1 packages are not supported.

Independent composition retains its revision, dirty-state fences and lifecycle.
Keep temporary previews, committed preferences and private editor drafts distinct.
Ordinary preferences apply quietly without Saved/Undo rituals; Preview Cancel,
explicit accent reset and Archive Undo remain available. Suggested experience
profiles are future design, not implemented package behavior.
Browser/shared-component source changes belong in `amplifier-client-web`, not
Unified's selected snapshot or disposable prepared client; follow the linked
guide's source ownership and two-repository delivery requirements.

Discover actual target schemas before acting: documentation shipped with a newer
build does not upgrade an older host. See [the preference and package guide](
../../docs/shell/themes.md) for current boundaries and package rules.
