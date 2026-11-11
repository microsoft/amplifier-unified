# Appearance authoring examples

These are authoring inputs, not automatic commands. Discover installed
`appearance.*` and `theme.*` schemas and the intended browser/scope first.

## Small Reference delta: Quiet study

Read [quiet-study.json](quiet-study.json) and [quiet-study.css](quiet-study.css).
The JSON is a package manifest: put those bytes at root `appearance.json` in a
ZIP beside `quiet-study.css`. It declares `formatVersion: 2` and
`base: "reference-v1"`. The stylesheet changes the two palettes and contrasting
on-accent ink, leaving structure and typography to Reference.

For an attached browser preview, discover `appearance.preview` and send its
`clientId`, `name` and exact CSS delta in the `app_control` dispatch envelope.
Cancel with `appearance.preview.cancel`. Preview neither saves a package nor
changes preferences. To retain the work, import the ZIP, or save a named delta
with `theme.save`. Read the returned entry and fingerprint before editing.
Choosing that saved ID uses `appearance.preferences.update` with an explicit
scope and current revision, not a separate full-CSS apply action.

## Geometry and paint

Read the saved delta before editing. Add only demonstrated differences using
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
Font roles (`--a-font-ui`, `--a-font-heading`, `--a-font-chat`, `--a-font-code`)
must reach actual text without indiscriminately changing UI labels.
Explicit Light/Dark choices override OS mode. Match scheme-selector specificity
in forced-color resets, including any optional logo filter reset.

## ZIP with local assets

At root `appearance.json`:

```json
{
  "type": "appearance",
  "formatVersion": 2,
  "base": "reference-v1",
  "name": "Quiet studio",
  "stylesheet": "theme.css",
  "assets": [
    {"path": "assets/paper.png", "mediaType": "image/png", "license": "licenses/artwork.txt"}
  ],
  "licenses": ["licenses/artwork.txt"]
}
```

Include exact declared asset/notice bytes. `theme.css` references
`url("assets/paper.png")`; no remote fetch is performed. Discover `theme.import`
and send actual ZIP bytes as `packageBase64`. Read the
saved entry, preview its resolved delta, and select it only within the requested scope.
Export that saved ID and reimport downloaded ZIP bytes in disposable
storage to verify portability. Export does not accept an unsaved draft. The manifest
and limits live in [Themes](../../docs/shell/themes.md#zip-appearance-packages).

ZIP is not a component installer. Preserve original asset/font licenses and
attribution; a notice filename alone proves no rights. Compare realistic text
before adding fonts. Reduced motion stops decorative movement; forced colors and
decorations-off remove artwork without removing operational feedback.

## Review cases

- Preview over a different appearance; verify no inherited artwork in either
  scheme, explicit mode vs OS mode, and narrow/zoom readability.
- Use long chat/code and actual pending/error controls. Check focus, message/action
  separation, composer expansion and top/middle/bottom/short transcript overflow.
  Final actions stay readable; keyboard/editor/iframe focus suppresses fades.
- Check decorations off, reduced motion and forced colors. Restore any temporary
  preferences and preserve unsent/dirty work without reading private contents.
- Compare exact preview/selection, reload and conditional undo; keep unrelated clients and
  selection intact within the current documented scope. Stale library writes fail
  without replacing editor work. A saved package is not an applied/rendered result.
- When changing preference or persistence mechanisms, exercise Global versus a
  complete browser profile: first-use copying, independent values after Global
  updates, switching back and forth retaining both sets, storage loss/failure,
  and same-profile cross-tab source updates that preserve a tab's preview.

Keep package validity, installed skill discovery, observed browser interaction and
human acceptance separate. Quiet study is a starting input, not certification.