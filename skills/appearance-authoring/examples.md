# Appearance authoring examples

These are authoring inputs, not automatic commands. Discover installed
`theme.*` schemas and the intended browser/scope first. The PR1916 rollback
temporarily retires Reference and Global / This browser profiles; see the
[rollback boundary](../../docs/shell/appearance-contract.md#temporary-rollback-boundary).

<a id="small-reference-delta-quiet-study"></a>

## Complete theme: Quiet study

Read [quiet-study.json](quiet-study.json). It is a `name` / `definition` action
input, **not a ZIP manifest**. Its version-1 definition supplies all ten colors
in both palettes and omits background for a flat treatment. It starts from the
built-in skin, not earlier artwork. The former Reference-delta example is retired.

Discover `theme.preview` and send this input with the intended `clientId` in the
`app_control` dispatch envelope. Use `theme.revert` to end the preview. Preview
does not save or apply it; authorized `theme.apply` commits app-wide. For an
intentional partial palette change, use `{name, tokens}` instead and expect the
existing skin/artwork to remain. Supply exactly one of definition, tokens or CSS.
Use `theme.save` with complete CSS to retain a library copy; read its returned
ID/fingerprint before editing.

## Geometry and paint

Read the complete saved skin before editing. Preserve its unchanged body and
make only demonstrated changes using
documented tokens, for example:

```css
#amp-one {
  --a-composer-radius: 12px;
  --a-message-radius: 10px;
  --a-message-gap: 24px;
  --a-panel-gap: 7px;
}
```

Inspect actual computed styles. Paint the message body, not its row/footer;
paint the inner composer surface, not the form/model-selector/status:

```css
#amp-one .a-message.a-user > .a-message-body {
  background: var(--a-tint);
  color: var(--a-ink);
}
#amp-one [data-part="composer-surface"] {
  background: var(--a-surface);
}
```

Prefer supported artwork tokens. Do not reposition descendants to create a
background layer or copy transcript masks; those are shared mechanics.
Check actual font inheritance so authored text styles reach their intended
content without indiscriminately changing UI labels.
Explicit Light/Dark choices override OS mode. Match scheme-selector specificity
in forced-color resets, including any optional logo filter reset.

## ZIP with local assets

At root `appearance.json`:

```json
{
  "type": "appearance",
  "formatVersion": 1,
  "name": "Quiet studio",
  "stylesheet": "theme.css",
  "assets": [
    {"path": "assets/paper.png", "mediaType": "image/png", "license": "licenses/artwork.txt"}
  ],
  "licenses": ["licenses/artwork.txt"]
}
```

Include a complete self-contained light/dark skin, not a Reference delta, plus
the exact declared asset/notice bytes. `theme.css` references
`url("assets/paper.png")`; no remote fetch is performed. Discover `theme.import`
and send actual ZIP bytes as `packageBase64`. Read the
saved entry, preview its resolved CSS, and apply only within the requested scope.
Export that saved ID with `format: "zip"` and reimport downloaded bytes in disposable
storage to verify portability. Export defaults to CSS; omitting ID exports the
committed appearance, not an unsaved draft or preview. The manifest
and limits live in [Themes](../../docs/shell/themes.md#zip-appearance-packages).

ZIP is not a component installer. Preserve original asset/font licenses and
attribution; a notice filename alone proves no rights. Compare realistic text
before adding fonts. Reduced motion stops decorative movement; forced colors and
decorations-off remove artwork without removing operational feedback.

## Review cases

- Preview over a different appearance; verify no inherited artwork in either
  scheme for a complete definition; intentional token-only edits retain existing
  artwork. Check explicit mode vs OS mode and narrow/zoom readability.
- Use long chat/code and actual pending/error controls. Check focus, message/action
  separation, composer expansion and top/middle/bottom/short transcript overflow.
  Final actions stay readable; keyboard/editor/iframe focus suppresses fades.
- Check decorations off, reduced motion and forced colors. Restore any temporary
  preferences and preserve unsent/dirty work without reading private contents.
- Compare exact preview/apply, reload and conditional revert; keep other clients'
  conversation selection intact. Stale library writes fail
  without replacing editor work. A saved package is not an applied/rendered result.
- For rollback persistence changes, exercise a valid legacy theme and an absent
  one separately. The latter uses the safe old default, not an automatic recovery.
  Retain `shared-reference-v1`, browser-local profiles and format-2 library data
  unchanged and inactive; reject unsupported imports without library mutation.

Keep package validity, installed skill discovery, observed browser interaction and
human acceptance separate. Quiet study is a starting input, not certification.