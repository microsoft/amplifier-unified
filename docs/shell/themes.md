# Complete themes and presentation preferences

<a id="reference-appearances-and-scoped-preferences"></a>

The [web appearance contract](appearance-contract.md) owns interaction guarantees.
The user-approved PR1916 rollback restores the complete-theme API and format-1
library after the `.078` visual regression. The
[November 9 design](../ux/decisions/20261109-appearances.md) is superseded/deferred.
Discover installed `theme.*` and `shell.*` schemas before acting; a source
document does not upgrade a host or establish browser conformance.

Reference and Global / This browser profiles are temporarily retired. Preserve
`shared-reference-v1`, browser-local profiles and format-2 saved libraries inert
and undeleted; do not convert them or use them to seed the legacy path. When the
old `theme` value removed by `.078` is absent, use the safe old built-in default,
not a claimed recovery of the lost personal theme. The
[rollback boundary](appearance-contract.md#temporary-rollback-boundary) owns
these preservation and fallback promises.

## One foundation, intentional differences

This anchor is retained from the deferred Reference design. The restored API
uses complete themes rather than Reference deltas. `theme.preview` and
`theme.apply` require `name` and exactly one of:

- `definition`: a complete version-1 theme, starting from the built-in skin,
  never the previous theme's artwork.
- `tokens`: an intentional partial palette edit retaining the existing skin and
  background. On a structured theme, it updates both palettes.
- `css`: a complete self-contained CSS skin, including light/dark styling.

A definition contains `version: 1` and `palette.light` / `palette.dark`, each
with all ten six-digit hex colors: `bg`, `surface`, `soft`, `ink`, `muted`,
`line`, `accent`, `tint`, `green`, `danger`. Their CSS counterparts use `--a-`;
also honor the contrasting on-accent foreground rather than assuming white text.
Shared components retain behavior, scrolling, focus and responsive safety.

Optional `background.light` and `background.dark` are CSS `background-image`
values: `none`, gradients, repeating patterns or embedded PNG/JPEG/WebP accepted
by the validator. Optional `size`, `position`, `repeat` default to `cover`,
`center`, `no-repeat`. No external URLs/imports, SVG data URLs or injected
declarations. `bg` remains the opaque fallback. Omitting `background` gives a
flat background rather than inheriting earlier artwork. A thumbnail is not
whole-shell preview acceptance.

## Preference scope and preview

`theme.preview` is scoped to the intended attached browser client; `theme.apply`
changes the app-wide committed skin without changing conversation selection.
`theme.revert` ends that client's preview first, otherwise conditionally reverses
its last application when no newer theme has replaced it. Library save/import/
update does not apply CSS. Keep app-wide apply explicit when other clients are
attached. The retired `appearance.preferences.*` profile path is not an alternate
way to apply the rollback's themes.

Canvas Apps request the same structured `theme.preview` / `theme.apply` inputs.
The host compiles and validates at request time, so approval uses the exact
reviewed CSS. Request review, stale revisions and client targeting still apply.
Private editor drafts and temporary previews remain independent of committed
styling. On reload, a small per-tab cache colors startup from committed scheme
and colors, never raw CSS, transcript or preview state. Authoritative shell
preferences determine the rendered shell.

## Presentation fields

The shell composition's `presentation` accepts `scheme` (`light`, `dark`,
`system`) and `decorations` (boolean, default true). Use the existing
`shell.changes.prepare` / preview / apply / revert protocol with current
revisions and dirty-instance guards. These client presentation preferences
are separate from the committed skin, not Global / This browser profiles.
Turning decorations off hides artwork without editing the theme; turning it
on restores it. Theme application does not change Reveal or accessibility.

- `planPlacement`: `composer` (default, Above composer), `inline` (Inline in chat),
  `active` (Above composer while working), or `overview` (Chat overview only).
  Placement changes presentation, not the Plan's underlying reports.
- `executionVisibility`: optional `step`, `turn` and `conversation` booleans.
  Explicit entries override Reveal for that scope; baseline presentation applies
  where no value is configured. UI edits preserve entries for other scopes.
- `executionMetrics`: allowlisted array of `elapsedSeconds`, `grossInputTokens`,
  `outputTokens`, `grossTotalTokens`, `cacheReadTokens`, `cacheWriteTokens`,
  `reasoningTokens`, `voiceSeconds`, `costUsd` and `calls`. The baseline selects all
  supported fields; an empty array selects none. Updates replace the selection.
- `conversationStatus`: optional boolean, default false. Its compact status uses
  demand-refreshed usage snapshots, not background accounting polling. Hidden
  conversation totals do not initiate an accounting read.

The [Plan](appearance-contract.md#chat-plan-placement) and
[metrics](appearance-contract.md#execution-metrics) promises own preserved meaning,
truthful availability and bounded export data.

## Window chrome

The compact application header uses `--a-chrome-bg` and `--a-chrome-ink` when
supplied by a CSS appearance, falling back to `--a-bg` and `--a-ink`.
These are optional CSS properties, not required version-1 definition fields.
Native-control clearance and drag exclusions
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
of unsent drafts and conversations. `tests/web/theme-definitions-browser.mjs`
exercises the production browser against a disposable host. It makes no model
calls and does not establish acceptance on a deployed user host.

`--a-header-decoration-height` reserves an optional strip below native PWA
controls (0–24px, default 0). The web shell owns usable titlebar geometry and
control centering. Paint header decoration within that strip and set the token
to zero with decorations off. This does not simulate native OS controls or prove
dragging; browser fixtures qualify only the published geometry API.

## Global presentation hooks

The root `#amp-one` exposes read-only presentation facts, never authorization,
control disabling or acknowledgements:

| Hook | Meaning |
| --- | --- |
| `.a-app-active` / `data-app-activity="active"` | Admitted non-internal chat/delegate execution, preparation/history loading, or a canonical long operation is pending; independent of selected chat or mounted transcript. |
| `.a-app-idle` / `data-app-activity="idle"` | The connected authoritative baseline currently reports no such activity. Unfinished saved tasks alone are not active execution. |
| `.a-app-activity-unknown` / `data-app-activity="unknown"` | Activity unavailable, stream disconnected or baseline missing; an open socket does not qualify retained state. Neither old active nor old idle is a current fact. |
| `.a-app-attention` / `data-app-attention="unread"` | Canonical global unread attention exists; not every unread item requires approval. `none` means no unread items, not all decisions resolved. |
| `.a-app-disconnected` / `data-app-connection="disconnected"` | Host state stream disconnected; no claim about internet or provider credentials. |

The projection consumes the existing incremental session index and canonical
operation/attention owners. It does not hydrate dormant history, scan DOM, poll
for animation or treat metadata/refresh/internal bookkeeping as execution.
Normal authentication/revision fences apply; no second status or acknowledgement
store is introduced. The [interaction contract](appearance-contract.md#visual-rules)
owns restrictions on optional activity artwork.

## Optional shape and spacing tokens

CSS appearances can also supply `--a-composer-radius`, `--a-card-radius`,
`--a-control-radius`, `--a-message-radius`, `--a-message-gap`, `--a-composer-pad`,
`--a-card-pad`, `--a-card-line`, and `--a-surface-shadow`. The refreshed shell
uses these for its composer, message bubbles, output cards, and controls.
Omitted tokens retain the standard geometry. Narrow conversations reduce the
message gap to 18–22px; touch-target sizes and panel layout remain host-owned.
Aurora, Atelier, and Graphite demonstrate small variations in both color modes.

### Shared transcript edges

The shared transcript baseline owns overflow cues, not individual appearances.
It uses the existing scroll owner to fade only an edge with content beyond it:
18px at the top and 24px at the bottom. Short transcripts have no fade; reaching
the bottom removes the bottom fade so the final text and actions remain readable.
Keyboard-visible focus on the scroll pane or a descendant, and focus on an active
editor (`input`, `textarea`, `select`, `[contenteditable]`, or `iframe`), disables
the mask. Pointer/programmatic focus on a non-visible container alone does not.
An active embedded review is detected from the host document's active iframe,
not only CSS focus selectors; no child-document access is required. A mounted
but inactive iframe leaves the fade enabled. Native focus changes and frame
removal clear this host-owned guard without moving the reading position.
Forced colors removes the mask. The scrollbar gutter remains intact. The navigator
and composer are outside the masked scroller; do not mask the conversation panel
or reposition its children.

CSS appearances may tune `--a-transcript-fade-top` and
`--a-transcript-fade-bottom`; omitted values retain the native defaults above.
The host clamps fade lengths to 0–48px and at most half the viewport height.
Classic scrollbar gutters retain opaque mask coverage; overlay scrollbars expose
no layout gutter, so their edge rendering needs platform-specific inspection.
These are optional CSS properties, not version-1 definition fields.
Prefer omission to copying the mask or deriving
overflow from a theme. Never add an unconditional transcript mask, an overlay
that covers the final message, or theme-owned scroll listeners. Remove duplicate
mask rules from bundled skins when the shared fallback is in place; do not
rewrite saved user copies.

Check the installed baseline before relying on these properties. This describes
the shared authoring baseline, not certification that an arbitrary skin preserves
it; check top/middle/bottom, short content, pointer-triggered question review,
keyboard-visible focus, active editors and forced colors.

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

Aurora, Atelier and Graphite demonstrate different frame materials and geometry;
the original Amplifier appearance retains its continuous frame.
Theme changes remeasure panel space immediately, including previews. Narrow
screens use an uninterrupted surface; focused and overlay canvases stay square.
The background-artwork switch hides these panel decorations as well as root
artwork, while retaining shapes, borders and spacing. Prefer static decorations.
Keep decorative lines away from navigation controls and labels; selected-row
indicators are functional feedback, not background artwork.
The user-directed Digital Rain design intentionally uses continuous optional
code-rain artwork. This is not a work-status or busy-region effect: it changes
no layout and conveys no operational information. Such explicitly selected
artwork must be non-flashing, remain behind readable content, stop under reduced
motion, and disappear with decorations off or forced colors. The interaction
contract's restrictions on animated busy-region backgrounds, tint, pulse and
operational feedback remain unchanged.

### Create, edit, delete and share

Open **Settings → Appearance → Save, import & export themes**. Ordinary
Appearance cards choose a shared committed look immediately. In the library,
selecting a card previews it only in the attached browser. Leaving Appearance
or closing Settings clears that preview, not the editor draft. Library operations
never apply CSS. Global / This browser controls are retired during rollback.

| Intent | Settings | Shared agent action |
| --- | --- | --- |
| Create or copy | Expand **Advanced customization**, name the CSS, choose **Save as copy**. | `theme.save({name, css, description})` |
| Import | **Upload appearance** accepts portable `.css` or a format-1 ZIP and keeps embedded metadata. | `theme.import({css})` or `theme.import({packageBase64})`; supply exactly one. Optional `name`/`description` override metadata; `fallbackName` names CSS without an embedded name. |
| Edit | Read an entry and retain its fingerprint with the editor draft. | `theme.read({id})`, then `theme.update({id, expectedFingerprint, name, css, description})`. |
| Delete | Select a saved entry, choose **Delete**, then **Confirm delete**. Export first to retain a copy. | `theme.delete({id, expectedFingerprint})` |
| Export | Export saved CSS or ZIP with assets/notices. Current-appearance export uses committed CSS, never a draft or preview. | `theme.export({id, format: "css"})` (default) or `theme.export({id, format: "zip"})`; omit `id` for the committed appearance. |

`theme.list` returns metadata and fingerprints, without CSS. `theme.read` returns
the self-contained CSS and a `fingerprint` for that saved version. Keep that fingerprint with
the editor draft; do not silently replace it with one from background refresh.
Settings keeps the target, fingerprint, name, description and CSS together in a
tab-private editor draft. Another client's Apply or a background library refresh
cannot replace that draft. It survives visiting unrelated Settings sections,
closing/reopening Settings and reloading the same tab through browser session
storage. A storage failure is shown explicitly; keep that tab open and save a
library copy. Previews do not resume automatically. The renderer uses the exact
validated preview CSS, not whatever the editor contains after further edits.
The preview banner and its confirmation apply that exact snapshot, including
agent-requested previews. **Apply appearance** in the editor is a distinct action
on its retained draft. Cancelling a preview does not discard that draft.
Reloading or duplicating a page ends its inherited preview, without cancelling
another open page's preview or changing either page's saved preferences.
Updates retain the saved ID, including a rename. A stale update or deletion fails
with 409 and leaves the file unchanged: read it again, review differences, then
decide which edits to retain. Included appearances are read-only; copy them instead.
Deleting or updating a library entry leaves already-applied CSS intact across
restarts; apply an edited version separately. If no valid legacy theme is
available, the old built-in default is safe fallback, not a recovered personal
theme or a conversion of a retained Reference selection.

Imports never silently overwrite another entry. Identical canonical uploads
reuse the same ID; changed content creates another entry even with the same name.
If an in-place update occupies an older export's content-based filename, importing
that export uses a deterministic unused suffix. Reimporting the identical export
reuses that entry instead of replacing the updated appearance.
Portable CSS carries one leading metadata comment:

```css
/* amplifier-appearance: {"name":"Quiet Studio","description":"Warm surfaces and calm accents."} */
#amp-one { --a-accent: #316653; }
```

This illustrates the header, not a complete authored skin. Saving/exporting
preserves metadata, including additional author fields, without nesting headers.
Import accepts UTF-8 with/without BOM and LF/CRLF; export writes one canonical
header and retains the body. CSS assets must be embedded. Remote URLs, filesystem
URLs, SVG data URLs and external imports are rejected by the preview/apply
validator. CSS including metadata is limited to 1 MB. Imports are published
atomically; symlink files/directories, traversal IDs
and nonregular files are rejected. Cooperating host writers serialize updates.
An external editor should use the shared actions when it needs stale-write
protection; arbitrary filesystem writers do not participate in that lock.

Export returns downloadable CSS in the result and retains the attached-client
download effect. With no ID it exports the exact committed CSS. To share a named
entry, export by ID and import its CSS or ZIP on the recipient's installation.
This transfers an appearance only,
not shell layout, accessibility preferences, credentials or executable components.
An import is validated, not certified accessible: preview both schemes, reduced
motion, decorations off and a narrow layout before applying it.

Bundled source treatments demonstrate intentional identity, not exceptions to
shared behavior: Digital Rain's optional emerald artwork, Lightline's static
architectural surfaces and warm user bubbles, and Breadbin's casing/violet screen
and paint-only keycaps. All retain shared message-action and transcript mechanics.
For a change, inspect actual packaged rendering in both schemes and narrow layout,
preferences/accessibility, preview/selection/undo and preservation. Packaging or a
reference image is not visual acceptance; record what remains unchecked.

## ZIP appearance packages

Use an ordinary `.zip`, such as `quiet-studio.zip`. Contents define its type, not
the filename. Version 1 transports a complete stylesheet, supported local
assets and license notices. It is not a general archive viewer, plugin installer or
portable shell composition.

```text
quiet-studio.zip
├── appearance.json
├── theme.css
├── assets/
│   ├── background.png
│   └── reading.woff2
└── licenses/
    ├── artwork.txt
    └── OFL.txt
```

```json
{
  "type": "appearance",
  "formatVersion": 1,
  "name": "Quiet studio",
  "description": "Quiet surfaces with locally bundled assets.",
  "author": "Example author",
  "stylesheet": "theme.css",
  "assets": [
    {"path": "assets/background.png", "mediaType": "image/png", "license": "licenses/artwork.txt"},
    {"path": "assets/reading.woff2", "mediaType": "font/woff2", "license": "licenses/OFL.txt"}
  ],
  "licenses": ["licenses/artwork.txt", "licenses/OFL.txt"]
}
```

`type`, `formatVersion`, `name` and `stylesheet` are required. Optional descriptive
fields carry attribution, not a verified authorship claim. Every archive member
must belong to the manifest, stylesheet, declared assets or declared notices.
Unsupported versions and unknown manifest fields are rejected. Fonts require a
bundled license reference; the host checks the presence of notices, not whether a
license grants the submitter every right they claim.

Stylesheets reference declared assets relative to the stylesheet, for example
`url("assets/background.png")`. Import rewrites those references into embedded
data URLs before normal preview/apply validation. No remote fetch, extraction to
arbitrary paths or CSS import is performed. Supported assets are PNG, JPEG, WebP,
WOFF and WOFF2; SVG, HTML, JavaScript and executable content are not package assets.
Keep unmodified upstream font files and their copyright/license notices together.
Pin source revisions and hashes when vendoring. Modified/subset fonts need a fresh
license review, including reserved font names. Different fonts must earn their
cost in rendered, realistic comparisons; do not bundle similar families merely
to increase choice.

The archive reader allows at most 4,000,000 ZIP bytes (5,333,336 base64 transport
characters), 128 members, 2,000,000 expanded bytes per member and 8,000,000 expanded
bytes total. Compression ratios above 200:1 are rejected. These limits are checked
before publishing anything. It rejects path traversal,
absolute/backslash paths, duplicate names, links, special files, encrypted members,
unsupported compression and corrupt streams. Runtime CSS retains the existing
1 MB limit; an otherwise small compressed ZIP can still exceed that limit after
embedding assets. Import is all-or-nothing and does not apply an appearance.
The archive reader accepts ordinary ZIP32 STORED/DEFLATE records, including signed or
unsigned data descriptors. ZIP64 and gaps between member records are unsupported.
Conflicting notice bodies at the same path fail rather than silently replacing
attribution. Package metadata in CSS exports carries complete retained notices.

Library refresh still reads and hashes each bounded ZIP. A service-local cache
retains at most 128 small validated summaries, keyed by exact archive-byte digest;
unchanged summaries avoid decompression and CSS compilation. Changed bytes and
explicit full reads validate again. This does not bound the total library count
or claim constant-time polling.

Read a saved package before editing or deleting it; its fingerprint protects the
canonical saved package, including assets and notices. Library edits preserve
package assets/notices. ZIP export preserves them; CSS export stays self-contained
and carries required notices. Saving or applying does not execute archive content,
change independent preferences or grant capabilities. Deleting a library entry
does not remove already-applied CSS. CSS-only import and default CSS export are
re-enabled alongside format-1 ZIPs. Saved format-2 packages remain untouched and
inactive, not exported or converted as if they were format 1.

## Source ownership

Browser styles, theme controls and shell presentation are authored in the
selected `amplifier-client-web` repository. Unified owns validation, library storage and the
service adapter in `amplifier_web`, the contracts here and host/browser/producer
checks in `tests/web`. Client tests remain with the client; run them from the
prepared `.build/web-client/tests` through the prepared client's test commands.
Follow [the selected-client assembly guide](../../components/README.md) to refresh
the delivery snapshot and build generated assets. `components/web-client` is a
selected snapshot and `.build/web-client` is disposable prepared source, not
another authoring owner; `amplifier_web/static` is generated output, not a
canonical editing path.
