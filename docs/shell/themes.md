# Reference appearances and scoped preferences

The [web appearance contract](appearance-contract.md) owns interaction guarantees.
The [November 9 design](../ux/decisions/20261109-appearances.md) explains the
replacement of full-skin snapshots. Discover installed `appearance.*`, `theme.*`
and `shell.*` schemas before acting; a source document does not upgrade a host.

## One foundation, intentional differences

Reference supplies the web client's neutral palette, type roles, controls and
component defaults. It is always loaded, including in previews and thumbnails.
Ordinary gallery entries are small CSS deltas over `reference-v1`; Reference
itself is available through advanced/recovery controls, not an ordinary card.
Omission means use Reference, never retain the previous appearance's CSS.

Appearances own palettes, font-role choices, material, artwork and supported
geometry. Shared components own behavior, scrolling, focus, responsive safety
and accessibility safeguards. A repeated functional repair belongs in that shared
owner, not in every delta. Package fingerprints identify authored bytes; actual
rendering also depends on the foundation/build and effective preferences.

The semantic palette is `--a-bg`, `--a-surface`, `--a-soft`, `--a-ink`,
`--a-muted`, `--a-line`, `--a-accent`, `--a-on-accent`, `--a-tint`, `--a-green`
and `--a-danger`. Set readable pairs in explicit light and dark scheme rules.
Use `--a-font-ui`, `--a-font-heading`, `--a-font-chat` and `--a-font-code`
for distinct roles; `--a-font` is the UI alias for existing consumers.
`--a-background-art` supplies optional artwork above the opaque `--a-bg`
fallback. Prefer these supported roles to copying component selectors.

## Preference scope and preview

One **Global / This browser** selector chooses the complete active profile.
Global belongs to the authenticated host owner on this installation, not a new
multiuser or cross-host synchronization service. The browser profile and active
source cover tabs in the same browser profile under the server-supplied
host/owner namespace. The library stays global regardless of selection source.

`appearance.preferences.read` returns Global values/revision, the target client's
complete browser profile mirror, effective values and one active source. A client ID routes
a request; it does not authenticate its owner. Readbacks exclude private editor
drafts. Discover `appearance.preferences.update` for current fields, scope and
revision requirements. A shared patch changes only its named fields; a stale
edit conflicts rather than replacing unrelated values.

A local patch or source switch is a command to the intended browser, not a
server-persisted browser setting. Admission, local storage and browser acknowledgement are distinct.
`appearance.client.report` supplies the bounded mirror and command result under
the admitted client/generation. `false` and empty supported collections are
values. The complete browser profile never inherits individual Global fields.
First use initializes it from Global; later switches preserve both independent
sets. Storage loss returns to Global; storage failure is explicit and never
falls back to a Global write. Disconnect is not storage loss.

`appearance.preview` validates an exact named CSS delta for an attached client.
It does not select or save a package. `appearance.preview.cancel` leaves
preferences and editor work intact. `appearance.preferences.undo` conditionally
reverses a committed preference edit without overwriting intervening changes.
Browser-wide preference updates must not replace another tab's active preview
or dirty editor. Preview state is not a committed startup preference.

Ordinary selection changes only `appearanceId`. Scheme, decorations, detail and
other choices retain their values in the active profile. Suggested experience
profiles are a future package slice, not implied support in this styling format:
their accepted interaction requires separate explicit preview/apply of field
changes, preserving the active profile and personal/accessibility choices.

## Presentation fields

Discover eligible fields from `appearance.preferences.update`. Scheme accepts
`light`, `dark` or `system`; decorations is a boolean independent of operational
feedback. Existing public shell presentation controls remain client-targeted:
eligible changes use the same local preference owner and persistence result,
not a competing shared composition preference. Mixed composition changes retain
their revision/dirty-instance activation fences: pass those checks before
issuing the local command, then activate the mixed composition atomically only
after the admitted browser generation acknowledges successful persistence.

The defaults below are baseline defaults, not cross-profile inheritance.
An omitted patch field is unchanged in the selected profile.
Explicit `false` and empty supported collections remain values.

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
These are optional delta properties, not package manifest fields.
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
These are optional delta properties, not package manifest fields.
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
| `--a-conversation-art` | Optional quiet conversation artwork; defaults to the top 240px. |
| `--a-conversation-art-size`, `--a-conversation-art-repeat`, `--a-conversation-art-position` | Conversation artwork sizing, repetition and initial position; defaults to `100% 240px`, `no-repeat` and `0 0`. These affect paint only, not panel geometry. |
| `--a-canvas-art` | Canvas header artwork. |

Aurora, Atelier and Graphite demonstrate different frame materials and geometry;
Amplifier keeps its own treatment over the same Reference foundation.
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

Open **Settings → Appearance** to choose a look. One sidebar-style **Global /
This browser** selector chooses which complete settings set to use and edit.
Both choices show the exact same controls; reversible changes autosave to the
selected source with Undo. No per-row scope, exception badge or browser subpage
is needed. Merely opening or leaving Settings writes nothing. Mark the selected
gallery appearance once without a redundant selected-name pill. Advanced appearance
and authoring/library controls belong at the bottom. Authoring/library
controls are separate from selection. Saving, importing, exporting or deleting a
package never publishes preferences. A temporary preview belongs only to its
target tab and can be cancelled independently of the saved entry or editor draft.

| Intent | Settings | Shared agent action |
| --- | --- | --- |
| Create or copy | Name the CSS delta and save a copy. | `theme.save({name, css, description})` |
| Import | Upload an appearance ZIP with manifest, delta and declared assets/notices. | `theme.import({packageBase64})`; discover optional metadata overrides. |
| Edit | Read an entry and retain its fingerprint with the editor draft. | `theme.read({id})`, then `theme.update({id, expectedFingerprint, name, css, description})`. |
| Delete | Select a saved entry, choose **Delete**, then **Confirm delete**. Export first to retain a copy. | `theme.delete({id, expectedFingerprint})` |
| Export | Export a saved entry as ZIP, including assets and notices. | `theme.export({id})`; a saved ID is required. |

`theme.list` returns metadata and fingerprints, without CSS. `theme.read` returns
the resolved CSS delta and a `fingerprint` for that saved version. Keep that fingerprint with
the editor draft; do not silently replace it with one from background refresh.
Settings keeps the target, fingerprint, name, description and CSS together in a
tab-private editor draft. Another client's preference edit or a background library refresh
cannot replace that draft. It survives visiting unrelated Settings sections,
closing/reopening Settings and reloading the same tab through browser session
storage. A storage failure is shown explicitly; keep that tab open and save a
library copy. Previews do not resume automatically. The renderer uses the exact
validated preview CSS, not whatever the editor contains after further edits.
Selecting saved work and previewing unsaved edits are distinct actions; a
preference stores a library ID, never the editor's unsaved CSS. Cancelling a
preview does not discard that draft.
Reloading or duplicating a page ends its inherited preview, without cancelling
another open page's preview or changing either page's saved preferences.
Updates retain the saved ID, including a rename. A stale update or deletion fails
with 409 and leaves the file unchanged: read it again, review differences, then
decide which edits to retain. Included appearances are read-only; copy them instead.
If a selected entry is unavailable, readback must identify that condition rather
than claim an older CSS snapshot is the selected package. Reference supplies the
usable recovery surface. Re-select a valid saved entry explicitly.

Imports never silently overwrite another entry. Identical canonical uploads
reuse the same ID; changed content creates another entry even with the same name.
If an in-place update occupies an older export's content-based filename, importing
that export uses a deterministic unused suffix. Reimporting the identical export
reuses that entry instead of replacing the updated appearance.
The ZIP manifest identifies its base, stylesheet, metadata, assets and notices.
The authoring API's `css` is a delta body, not a second portable full-skin format.
Remote URLs, filesystem URLs, SVG data URLs and external imports are rejected by
the same validator used for preview. Runtime CSS is limited to 1 MB after asset
resolution. Imports are published atomically; symlink files/directories, traversal IDs
and nonregular files are rejected. Cooperating host writers serialize updates.
An external editor should use the shared actions when it needs stale-write
protection; arbitrary filesystem writers do not participate in that lock.

To share a named library entry, export it by ID and upload its ZIP on the
recipient's installation. This transfers an appearance only,
not shell layout, accessibility preferences, credentials or executable components.
An import is validated, not certified accessible: preview both schemes, reduced
motion, decorations off and a narrow layout before choosing it.

Bundled source treatments demonstrate intentional identity, not exceptions to
shared behavior: Digital Rain's optional emerald artwork, Lightline's static
architectural surfaces and warm user bubbles, and Breadbin's casing/violet screen
and paint-only keycaps. All retain shared message-action and transcript mechanics.
For a change, inspect actual packaged rendering in both schemes and narrow layout,
preferences/accessibility, preview/selection/undo and preservation. Packaging or a
reference image is not visual acceptance; record what remains unchecked.

## ZIP appearance packages

Use an ordinary `.zip`, such as `quiet-studio.zip`. Contents define its type, not
the filename. Version 2 transports a `reference-v1` CSS delta, supported local
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
  "formatVersion": 2,
  "base": "reference-v1",
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

`type`, `formatVersion`, `base`, `name` and `stylesheet` are required. Optional descriptive
fields carry attribution, not a verified authorship claim. Every archive member
must belong to the manifest, stylesheet, declared assets or declared notices.
Unsupported versions and unknown manifest fields are rejected. Fonts require a
bundled license reference; the host checks the presence of notices, not whether a
license grants the submitter every right they claim.

Stylesheets reference declared assets relative to the stylesheet, for example
`url("assets/background.png")`. Import rewrites those references into embedded
data URLs before normal delta validation. No remote fetch, extraction to
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
attribution.

Library refresh still reads and hashes each bounded ZIP. A service-local cache
retains at most 128 small validated summaries, keyed by exact archive-byte digest;
unchanged summaries avoid decompression and CSS compilation. Changed bytes and
explicit full reads validate again. This does not bound the total library count
or claim constant-time polling.

Read a saved package before editing or deleting it; its fingerprint protects the
canonical saved package, including assets and notices. Library edits preserve
package assets/notices, and ZIP export preserves them. Saving does not execute
archive contents, change preferences or grant capabilities. There is no legacy
CSS import/export, full-skin compiler, migration table or alternate renderer.

## Source ownership

Reference, component styles and browser preference persistence are authored in the
selected `amplifier-client-web` repository (`src/reference.css`, `src/shell/` and
the client's appearance modules). Unified owns validation, library storage and the
service adapter in `amplifier_web`, the contracts here and host/browser/producer
checks in `tests/web`. Client tests remain with the client; run them from the
prepared `.build/web-client/tests` through the prepared client's test commands.
Follow [the selected-client assembly guide](../../components/README.md) to refresh
the delivery snapshot and build generated assets. `components/web-client` is a
selected snapshot and `.build/web-client` is disposable prepared source, not
another authoring owner; `amplifier_web/static` is generated output, not a
canonical editing path.
