# Complete themes and presentation preferences

The [appearance and interaction contract](appearance-contract.md) defines the
whole-app design language, waiting/saving states, detail levels and accessibility.
This document describes the currently supported theme authoring API.

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

## Window chrome

The compact application header uses `--a-chrome-bg` and `--a-chrome-ink` when
supplied by a CSS appearance, falling back to the existing `--a-bg` and
`--a-ink` tokens. These are optional CSS properties, not new required fields
in the version 1 theme schema. Native-control clearance and drag exclusions
remain host-owned; a skin must not position controls under native buttons.

The page's `theme-color` follows the resolved, painted header on preview,
apply, revert and color-mode changes. Transparent header colors fall back to
an opaque application background. A preview affects live chrome but never the
committed startup cache. The cache is invalidated when the stylesheet changes,
so an unvisited mode cannot reuse the previous skin's chrome.

An installed desktop browser that enables Window Controls Overlay can merge
this header into its native title area. Unsupported browsers and disabled
overlay retain the ordinary header and browser-owned title bar. Window-control
placement and dragging require installed-browser/OS verification; CSS geometry
or a page metadata test alone does not establish those outcomes.

Native controls take a single opaque color, not a spatial gradient. In a
non-stacked overlay window, the host blends a matching flat header color into
decorative root artwork over 96px instead of cutting it off at the header edge.
The original background remains intact below that join. Decoration-off mode,
explicit `--a-chrome-bg`, header background images or different header colors
opt out; focused Canvas and stacked narrow layouts retain their own surfaces.
This is a softened join, not a gradient rendered by native window controls.

Acceptance must include preview/apply parity, light/dark/system, decoration
on/off, a new theme clearing earlier artwork, revert, reload, and preservation
of unsent drafts and conversations. `frontend/tests/theme-definitions-browser.mjs`
exercises the production browser against a disposable host. It makes no model
calls and does not establish acceptance on a deployed user host.

## Optional shape and spacing tokens

CSS appearances can also supply `--a-composer-radius`, `--a-card-radius`,
`--a-control-radius`, `--a-message-radius`, `--a-message-gap`, `--a-composer-pad`,
`--a-card-pad`, `--a-card-line`, and `--a-surface-shadow`. The refreshed shell
uses these for its composer, message bubbles, output cards, and controls.
Omitted tokens retain the standard geometry. Narrow conversations reduce the
message gap to 18–22px; touch-target sizes and panel layout remain host-owned.
Aurora, Atelier, and Graphite demonstrate small variations in both color modes.

### Main panel surfaces

The main screen also consumes these optional CSS tokens:

| Token | Use |
| --- | --- |
| `--a-panel-gap` | Horizontal panel gaps and space below the header; clamped to 0–16px. |
| `--a-panel-radius` | Interior upper corners. Screen-side and bottom corners stay square. |
| `--a-panel-border-width`, `--a-panel-line` | Panel outlines; default 0px with the standard separators retained. |
| `--a-panel-shadow` | Subtle depth on navigation, conversation and canvas. |
| `--a-navigation-pad` | Navigation's top padding; control target sizes stay unchanged. |
| `--a-navigation-art` | Navigation background image/gradient. |
| `--a-navigation-art-size`, `--a-navigation-art-repeat` | Artwork sizing and repetition. |
| `--a-conversation-art` | Optional quiet artwork across the top 240px of the conversation. |
| `--a-canvas-art` | Canvas header artwork. |

Aurora uses 10px gaps, 22px interior corners and a faint glow; Atelier uses
7px gaps, 10px corners and paper-like edges; Graphite uses 4px gaps, 4px corners
and a fine grid. The original Amplifier appearance retains its continuous frame.
Theme changes remeasure panel space immediately, including previews. Narrow
screens use an uninterrupted surface; focused and overlay canvases stay square.
The background-artwork switch hides these panel decorations as well as root
artwork, while retaining shapes, borders and spacing. Keep decorations static.

### Save, import and export

Open **Settings → Appearance → Save, import & export themes**. Upload a portable
`.css` file to add it to the library, or expand **Advanced customization**, name
your CSS and choose **Save to library**. Saving does not apply a theme. Preview
or apply it separately. **Export current appearance** downloads the applied CSS;
unsaved editor changes and temporary previews are not exported. Upload that file
on another installation to import it, then preview and apply it there.

Agents use the same path: `theme.list`, `theme.read({id})`,
`theme.save({name, css, description})`, `theme.preview({name, css})`,
`theme.apply({name, css})`, and `theme.export({})`. Saved CSS includes optional
surface tokens and embedded artwork; no separate asset installation is needed.
Export retains the existing download effect for the targeted attached client.
Edited custom themes are never silently replaced when bundled presets change.
