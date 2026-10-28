# Appearance authoring examples

These are inputs and review cases, not commands to execute automatically. Read
the installed action schemas before adding required client or revision fields.
Companions resolve from the loaded skill directory.

## New complete theme: Quiet study

Read [quiet-study.json](quiet-study.json). Its `name` and `definition` form a
theme input. Both palettes contain the ten required six-digit hex values.
`background` is omitted deliberately: this clears previous artwork rather than
retaining another skin's decoration.

After schema discovery, the dispatch envelope has this shape:

```json
{
  "operation": "dispatch",
  "args": {
    "action": "theme.preview",
    "args": {
      "name": "Quiet study",
      "definition": {
        "version": 1,
        "palette": {
          "light": "...read the complete companion...",
          "dark": "...read the complete companion..."
        }
      }
    }
  }
}
```

The abbreviated palette above is explanatory, not a valid payload. Use the
complete companion unchanged as the starting input, then include any target
fields required by the discovered schema. Preview does not apply or save it.
Use separate supported operations for those requested effects.

## Adapt a complete CSS skin without losing its identity

Read the current saved CSS through the host. Copy it into the requested output,
preserving the original. Change documented custom properties within its existing
light and dark rules, for example:

```css
/* Merge into BOTH existing scheme rules, not a standalone full skin. */
--a-composer-radius: 12px;
--a-card-radius: 10px;
--a-message-radius: 10px;
--a-message-gap: 24px;
--a-panel-gap: 7px;
--a-panel-radius: 10px;
```

Send the complete resulting stylesheet as `css`, not only this fragment. Do not
combine `css`, `tokens` and `definition` in one theme request. Check narrow mode:
host-owned responsive spacing and target sizes take precedence.

For only an accent-color edit, prefer `tokens` and explicitly retain the current
background. Do not claim that a partial palette edit created a complete theme.

## Selectors that preserve the host

Inspect the current DOM; these selectors are examples from the bundled web shell,
not permission to override its layout:

```css
/* Change the bubble, not the row, footer, avatar or timestamp. */
#amp-one .a-message.a-user > .a-message-body {
  background: var(--a-tint);
  color: var(--a-ink);
}
/* The input surface is not the outer form, model selector or status row. */
#amp-one [data-part="composer-surface"] {
  background: var(--a-surface);
}
```

For typography, inspect computed styles on actual message `p`/`span` elements.
Changing only the shell font does not establish that chat text changed. Use a
system mono stack when requested; do not add a network font dependency.

**Do not use** `conversation > * {position:relative; z-index:1}`. This moved the
absolutely positioned navigator into normal flow and created a large gap.
Do not compensate with guessed margins. For a decorative conversation layer:

```css
#amp-one [data-part="conversation"] {
  position: relative;
  isolation: isolate;
}
#amp-one [data-part="conversation"]::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  /* Supply quiet artwork here; leave all child positioning untouched. */
}
#amp-one[data-decorations="off"] [data-part="conversation"]::before {
  display: none;
}
```

For digital rain, three transparent embedded PNG tiles with uneven glyph spacing
give depth without three DOM layers. Animate `background-position` by integer
multiples of each tile's height for seamless loops; use distinct layer speeds.
Keep light-mode glyphs subdued emerald, not neon glow. Tune density separately
from opacity. Respect `prefers-reduced-motion: reduce` with static artwork and
`forced-colors: active` with decoration removed. Custom pseudo-elements need their
own decoration-off rule; the host cannot infer which custom pixels are artwork.
Mask a transcript fade only on `[data-part="messages"]`, never the conversation:
the navigator outside that scroller must remain visible and clickable.

The host resolves Device/system preference into `data-theme-scheme="light"` or
`"dark"`. Supply explicit rules for both. Do not let an unqualified OS dark-mode
media query override the user's explicit Light choice. Check all four combinations
of OS light/dark and explicit Light/Dark, plus Device following each OS mode.

## Small acceptance run

1. Preview Quiet study over a theme with artwork. Confirm flat backgrounds in
   light and dark; check system scheme follows the OS.
2. Use long chats, a multiline unsent draft, code, buttons and an error fixture.
   Check contrast, focus, navigator, bubble/footer bounds, input/model-selector/
   status separation and local feedback at narrow and wide widths plus zoom.
   Static swatches cannot establish these results.
3. Toggle decorations in a disposable client, restore its initial preference,
   and check reduced motion/forced colors. Record unsupported checks as unknown.
4. If applying is authorized, compare committed output with preview and reload.
   Revert through the host and verify the starting appearance returns without
   altering conversations or drafts.
5. Export the saved id, import into disposable library storage, read back exact
   CSS/metadata and preview. Check stale fingerprints reject update/delete and
   included entries remain read-only. A library save is not an apply or render.

Keep evidence separate: package validation, loader discovery, browser rendering,
interaction checks and user acceptance are different results. The example palette
is a starting point, not certified accessibility or whole-app conformance.