# Appearance authoring examples

Inputs below are not automatic commands. Discover the installed `appearance.*`
and `theme.*` schemas and intended browser/profile first. See the
[production preservation boundary](../../docs/shell/appearance-contract.md#temporary-rollback-boundary).

<a id="small-reference-delta-quiet-study"></a>

## Small Reference delta: Quiet study

[quiet-study.json](quiet-study.json) is a `name` / `css` preview/save input,
not a ZIP manifest. It uses both color palettes through `light-dark()` over the
bundled Reference. Preview with `appearance.preview` and intended `clientId`;
end with `appearance.preview.cancel`. Save with `theme.save`, inspect its returned
ID/fingerprint, then select that ID only at authorized captured profile/CAS.
Do not use retired complete-theme definitions, token patches or `theme.apply`.

## Geometry and paint

Prefer supported tokens and stable parts. Read exact saved CSS before editing.
For example, a deliberate delta can tune `--a-composer-radius: 12px`,
`--a-message-radius: 10px`, `--a-message-gap: 24px`, `--a-panel-gap: 7px`.
Paint `.a-message-body` or `[data-part="composer-surface"]`, not structural
message/action rows, status or the form/model selectors. Reference still owns
scrolling, masks, focus, control geometry and responsive layout.

Keep independent UI/heading/prose/code type roles and inspect actual fonts.
Artwork disappears with decorations off/forced colors; reduced motion stops
movement without removing operational feedback.

## ZIP with local assets

Root `appearance.json`:

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

Include delta CSS and exact declared asset/notice bytes. `theme.css` can reference
`url("assets/paper.png")`; the host validates and embeds it without network fetch
or extraction. Import actual ZIP bytes as `packageBase64`. Read the saved entry,
preview its exact resolved CSS, and select only within the requested profile.
Export by that ID with `theme.export({id})`; ZIP is the sole envelope. Reimport
actual exported bytes in disposable storage before claiming portability.
Raw CSS import, format-1 ZIP and no-ID export are unsupported. Old files remain
inert/undeleted. Explicit valid format-2 import saves into the current library
without selecting it; an identical existing package may be reused.

ZIP is not a component installer. Preserve fonts/assets/licenses and attribution;
a notice filename alone proves no rights. Package/schema acceptance is not visual
or accessibility certification.

## Review cases

- Preview over a different look, then cancel; retain private editor and other
  documents. Saved/imported is not selected or rendered.
- Check both schemes/system, decorations/accessibility, narrow/zoom, keyboard,
  long chat/code, composer expansion and actual pending/error/recovery controls.
- Select in captured Global or This browser, verify whole-record storage ACK and
  exact composition render ACK. Cross-tab/reload retain confirmed source/profile,
  never previews or inherited editor access. Storage failure never writes Global.
- An explicit accent may be reset with `patch: {accent: null}`. Switching to a
  different appearance drops the old override; same-ID/unrelated changes retain
  it. No hardcoded color is a reset. Stale Undo must not overwrite newer work.
- Seed old theme/profile/cache/format-1 and recovered format-2 records in isolated
  fixtures: startup/listing leaves them inert and byte-preserved. Missing new
  Global reads complete defaults without a preference write; malformed new
  records fail without silent repair or namespace rotation.

Keep validity, installed discovery, source/build identity, observed interaction and
human acceptance distinct. Quiet study is a starting delta, not certification.
