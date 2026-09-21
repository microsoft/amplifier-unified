# Complete themes and presentation preferences

Discover `theme.*` and `shell.*` schemas from the running host before authoring.
`theme.preview` and `theme.apply` accept exactly one of:

- `definition`: a complete version 1 theme. It starts from the host's built-in
  skin, never the previous theme's decoration.
- `tokens`: a partial palette edit preserving the current skin and background.
  On a structured theme, it updates both light and dark palettes.
- `css`: a complete existing-style CSS skin. This remains supported.

All three require a `name`. A definition contains `version: 1` and a `palette`
with `light` and `dark` objects. Each palette must supply all ten six-digit hex
colors: `bg`, `surface`, `soft`, `ink`, `muted`, `line`, `accent`, `tint`, `green`
and `danger`. Choose readable foreground/surface combinations in both modes.

The optional `background` object contains `light` and `dark` CSS
`background-image` values. These can be `none`, gradients, repeating patterns,
or embedded PNG/JPEG/WebP data URLs accepted by the skin validator. Optional
`size`, `position`, and `repeat` values default to `cover`, `center` and
`no-repeat`. Remote URLs, SVG data URLs, extra declarations and imports are
rejected. The palette's `bg` is the fallback when embedded artwork cannot load.
Omitting `background` explicitly gives a flat background. A chooser should use
these same values in its swatches and use the actual host preview to confirm
whole-shell rendering; a hand-drawn thumbnail alone is not preview acceptance.

Preview is scoped to the targeted attached browser client. Applying a theme
changes the existing app-wide committed skin. `theme.revert` restores the
previous committed theme. Keep that scope explicit when multiple clients are
attached; changing a theme does not change their conversation selection.

The shell composition's `presentation` accepts `scheme` (`light`, `dark`, or
`system`) and `decorations` (boolean, default true). Change these with the
existing prepare/preview/apply/revert protocol. Turning decorations off hides
background artwork without editing the theme, so turning it on restores it.
These are client presentation preferences, separate from the committed skin.
Appearance settings expose the same decoration preference to the user.

Canvas Apps can request the same structured `theme.preview` / `theme.apply`
payloads through their declared host actions. The host compiles and validates
at request time, so approval applies the exact reviewed stylesheet. Existing
request review, cancellation, stale revisions and client targeting still apply.

On reload, a small per-tab cache colors the loading screen using the last
committed scheme and colors. It contains no transcript or raw stylesheet. The
app waits for its authoritative shell preferences before rendering the shell;
previews are never saved into this loading-screen cache.

Acceptance must include preview/apply parity, light/dark/system, decoration
on/off, a new theme clearing earlier artwork, revert, reload, and preservation
of unsent drafts and conversations. `frontend/tests/theme-definitions-browser.mjs`
exercises the production browser against a disposable host. It makes no model
calls and does not establish acceptance on a deployed user host.
