# Reference appearances and complete profiles

<a id="reference-appearances-and-scoped-preferences"></a>

The [appearance contract](appearance-contract.md) owns observable promises;
[appearance direction](appearances.md) owns intent. This is the production API
proposal paired with the canonical client, not evidence of publication or live
adoption. Discover installed `appearance.*`, `theme.*` and `shell.*` schemas.

## One foundation, intentional differences

Reference (`reference-v1`) supplies shared geometry, native control behavior,
responsive layout, scroll/focus safety and accessible defaults. Included and saved
appearances supply intentional CSS differences over it. Appearance packages never
install components, execute code, grant permissions or change agent work.

The seven included IDs are `builtin:default` (Amplifier), `builtin:aurora`,
`builtin:atelier`, `builtin:graphite`, `builtin:digital-rain`, `builtin:lightline`
and `builtin:ready` (Breadbin). `reference` is an explicit neutral recovery choice,
not a gallery package. Every look supports light/dark/system, decorations off,
reduced motion and forced colors. Keep common functional fixes in Reference, not
copied into each package. A validated package or thumbnail is not visual proof.

## Preference scope and preview

Exactly one complete profile is active: Global or This browser. There are no
per-field source overlays. First explicit selection of This browser copies current
Global at `expectedSharedRevision`; subsequent switches retain both profiles.
Read `appearance.preferences.read` to capture the destination, revisions,
generation and storage version. `appearance.preferences.update` patches that
captured profile, not whichever source is active when a late response arrives.
Browser changes commit only after the originating browser confirms whole-record
storage; a separate `renderedCompositionId` reports the actual composition.
Failure, disconnection and uncertainty never write Global as fallback.

`appearance.preview({clientId, name, css})` validates an exact delta for one
attached document; `appearance.preview.cancel` ends only that preview. Leaving
Appearance or closing Settings cancels the preview, not a retained editor draft.
Previews are never resumed on reload or duplicated into another document.
Library save/import/update is separate from preference selection.

Ordinary settings apply directly without Saved toasts or Undo banners. The public
`appearance.preferences.undo({undoId})` remains an exact conditional whole-profile
operation; stale changes reject with 409 `appearance_undo_stale` and cannot be
retried at a fresh revision to overwrite newer work. Preview Cancel, explicit
accent reset, library editing and Archive Undo remain separate operations.

An appearance owns its default accent. A different `appearanceId` edit removes
the old override unless that same patch explicitly supplies a new color. Same-ID
and unrelated edits retain it. `patch: {accent: null}` removes only the override
at captured scope/CAS. Absence uses the current look's own color; null is not a
stored or reported profile value. This does not rewrite either palette/package.

## Presentation fields

The complete baseline is Amplifier, system scheme, balanced layout (Canvas to the
right), comfortable density, decorations on, standard execution and interface
detail, `executionVisibility: {}` (all three scopes visible), all ten metrics,
conversation status off, `workStartView: appearance`, interim replies expanded,
Plan above composer and no accent override. These are new baseline choices, not
preservation or reconstruction of old independent preferences.

- `appearanceId`: included or saved ID, or `reference`.
- `scheme`: `light`, `dark`, `system`; `layout`: `balanced`, `conversation`, `work`.
- `density`: `comfortable`, `compact`; `decorations`: boolean.
- `interfaceDetail`, `executionDetail`: `minimal`, `standard`, `detailed`.
- `executionVisibility`: optional `step`, `turn`, `conversation` booleans, merged
  within the selected profile. Empty means the profile's intrinsic baseline.
- `executionMetrics`: replacement array selected from `elapsedSeconds`,
  `grossInputTokens`, `outputTokens`, `grossTotalTokens`, `cacheReadTokens`,
  `cacheWriteTokens`, `reasoningTokens`, `voiceSeconds`, `costUsd`, `calls`.
  Empty selects none. Missing accounting remains unavailable, never invented.
- `conversationStatus`: boolean. Hidden totals do not initiate accounting reads.
- `workStartView`: `appearance`, `focus`, `brief`, `studio`.
- `collapseInterimReplies`: boolean; `planPlacement`: `composer`, `inline`,
  `active`, `overview`.
- Optional `accent`: six-digit hex override, not a copied appearance palette.

These presentation choices never change Plan reports, execution, providers,
permissions, conversation content, artifact identity or required recovery.

## Persistence and upgrade boundary

Global preferences use `shell_records/preferences/shared-reference-v2`.
An independent `appearance-identity/v2` record supplies a stable random owner
inside the authenticated owner's private app database. The namespace is
`amplifier.appearance.v2:<owner>`; never clientId, PID or hostInstanceId.
Missing Global preferences read as complete virtual defaults without writing a
preference record. The first explicit Global edit uses that same owner, so local
browser profiles survive cold restarts and later Global creation.

Browser localStorage under that namespace stores `{version, activeScope, values}`,
where version is a fresh unique write token (or `missing` for no record).
The document mirror revision is separate. Generation, revision and version
participate in CAS and ACK. The host does not persist local browser values.
The tab-only first-paint cache is `amplifier.appearance-composition.v4` with namespace
identity; the private editor key is `amplifier.appearance-editor.v3:<namespace>`.
Malformed new identity/Global/browser records fail explicitly and remain untouched;
there is no default/new-owner/Global-write fallback. Restore valid backup data or
use the presented recovery path rather than silently overwriting it.

All old theme/profile/cache/editor data, including the recovered `.078` records
and 0.80.1 experiment, stays inert and undeleted. The entire old `themes/`
directory is unlisted/unresolved; only `appearance-library-v1/` supplies new saved
entries. Explicit valid format-2 import copies content into that library. Nothing
automatically promotes an old file or claims recovery of an already lost theme.
Compatible validated independent shell placements and module views survive via
new `appearance-client-v2` records without adopting old presentation, previews,
prepared requests or pending storage commands. Canvas and private drafts keep
their existing owners. Code rollback is not preference conversion or permission
to discard newer work; use retained independent checkpoints and qualified code.

## Global presentation hooks

The root `#amp-one` exposes read-only presentation facts, never authority:
`data-app-activity` (`active`, `idle`, `unknown`), `data-app-attention`
(`unread`, `none`) and `data-app-connection` (`connected`, `disconnected`). These
consume canonical current projections, not DOM/history scans or animation polling.
Unknown/disconnected is not idle. Decorations never convey the sole meaning of
operational state; reduced motion/forced colors preserve static feedback.

## Window chrome

`--a-chrome-bg` and `--a-chrome-ink` paint the compact header, falling back to
`--a-bg`/`--a-ink`. Theme-color follows resolved opaque header paint, including
preview; only confirmed committed composition colors enter the startup cache.
Native-control clearance/drag exclusions remain shared owners. Window Controls
Overlay needs installed-browser/OS checks; CSS/source evidence is not native
acceptance. `--a-header-decoration-height` optionally reserves 0–24px below native
controls and becomes zero with decorations off.

## Optional shape and spacing tokens

Packages may set `--a-composer-radius`, `--a-card-radius`, `--a-control-radius`,
`--a-message-radius`, `--a-message-gap`, `--a-composer-pad`, `--a-card-pad`,
`--a-card-line`, `--a-surface-shadow`, `--a-panel-gap`, `--a-panel-radius`,
`--a-panel-border-width`, `--a-panel-line`, `--a-panel-shadow`,
`--a-navigation-pad`, and navigation/conversation/Canvas artwork tokens.
Shared owners clamp geometry and retain touch targets, overflow and focused Canvas.
Font roles are `--a-font-ui`, `--a-font-heading`, `--a-font-chat`, `--a-font-code`.
Inspect actual inherited and platform fonts; computed family alone is not glyph
proof. Prefer system families and preserve font licenses.

### Shared transcript edges

Only overflowing edges fade. Shared code owns anchor/follow/Latest/chat-return,
measured scrollbar gutters and mask suppression during keyboard-visible focus or
active input/textarea/select/contenteditable/iframe editing. Forced colors removes
the mask. Packages may tune `--a-transcript-fade-top`/`--a-transcript-fade-bottom`
(default 18/24px, shared clamp 0–48px and half viewport). Never duplicate masks,
scroll listeners or fixed overlays in a look. Decorations-off keeps functional cues.

### Main panel surfaces

Root and panel artwork uses shared paint consumers and disappears with decorations
off/forced colors. Narrow/focused Canvas retains its own geometry. Digital Rain's
explicit optional code rain is artwork, not a busy indicator; it stops under
reduced motion, remains behind readable content and never covers controls.
Breadbin's violet screen and Lightline's crisp surfaces retain their identities
without replacing scrolling, focus or status semantics.

### Create, edit, delete and share

- `theme.list`: bounded metadata/fingerprints, never all CSS bodies.
- `theme.read({id})`: exact delta CSS and current fingerprint.
- `theme.save({name, css, description})`: validate named delta and save ZIP; no selection.
- `theme.import({packageBase64, name?, description?})`: strict format-2 ZIP, no selection.
- `theme.update({id, expectedFingerprint, name, css, description?})`: saved entry CAS;
  retains assets/notices. Included entries are read-only; copy them.
- `theme.delete({id, expectedFingerprint})`: saved entry CAS, no profile rewrite.
- `theme.export({id})`: exact library ZIP only, never editor draft/preview/unspecified current look.

In-place updates retain saved ID. Selection resolves exact current package bytes;
an unavailable/deleted ID stays visibly unavailable with Reference recovery and
unchanged preferences. Editor targets/fingerprints/drafts survive background
refresh and unrelated changes; do not replace them with a newer read automatically.
Identical imports reuse canonical content identity; filename collision families
preserve updated entries and do not overwrite different bytes.

## ZIP appearance packages

A root `appearance.json` requires `type: "appearance"`, `formatVersion: 2`,
`base: "reference-v1"`, `name`, and `stylesheet`. Optional description/author,
assets and licenses retain attribution, not rights certification. Every member
must be declared. A delta CSS file references declared local assets; import embeds
validated PNG/JPEG/WebP/WOFF/WOFF2 URLs without network fetch or extraction.
Fonts require declared licenses. No SVG, executable content, external imports or
URL fetches. Raw CSS import and format-1 ZIP are not new authoring inputs.

Limits: 4,000,000 raw ZIP bytes, 5,333,336 base64 characters, 128 members,
2,000,000 expanded bytes per member, 8,000,000 total, compression ratio ≤200:1,
1 MB compiled CSS. Reject traversal, ambiguous/duplicate paths, links, special
files, corrupt/encrypted/unsupported streams, ZIP64 and undeclared gaps before
publication. Ordinary ZIP32 STORED/DEFLATE and bounded data descriptors work.
Export round-trips package assets/notices; it is not accessibility certification.

Library listing still reads/hashes bounded ZIP bytes. A service-local cache holds
at most 128 small validated summaries keyed by exact digest; changed/full reads
validate again. Selected appearance resolution has an eight-entry exact-byte
cache and never scans the library or history. These bounds do not cap library
count or claim constant-time refresh.

## Source ownership

Browser source/tests belong to canonical `amplifier-client-web`; Unified owns
host validation/storage/contracts and host/browser/producer checks. Follow
[component assembly](../../components/README.md) for whole selected-copy refresh
and normal build provenance. `components/web-client`, `.build/web-client` and
`amplifier_web/static` are not additional authoring owners.
