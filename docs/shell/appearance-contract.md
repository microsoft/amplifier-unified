# Web appearances: interaction contract

This boundary serves web users, appearance authors, component maintainers and
agents changing presentation. The [vision](appearances.md) defines the destination.
[Themes](themes.md) owns current tokens, package fields and API details; the
[design note](../ux/decisions/20261109-appearances.md) retains the superseded
November 9 redesign. These promises govern the user-approved PR1916 rollback
to complete themes and independent shell presentation preferences.
Discover the target installation's current schemas before acting; source changes
and this document alone do not establish installed or browser conformance.

## Temporary rollback boundary

After the `.078` visual regression, Reference and Global / This browser profiles
are temporarily retired. Restore the pre-cutover behavior at
`7cfb76cbbadbfdf19d6a142e2b6e910abe16ed07`: legacy `theme.*` preview/apply/revert,
complete theme definitions and CSS skins, and format-1 library/import/export.
Reference deltas and format-2 packages are not active authoring inputs.

Retain saved `shared-reference-v1`, browser-local profiles/source choices and
format-2 library data **inert and undeleted**. Do not activate, convert, seed
legacy values from, or overwrite those records as part of this rollback.
Preservation is not migration. The `.078` cutover removed the old `theme`
setting; where it is absent, use the safe old built-in default. Do not claim
automatic recovery of a lost personal theme. A remaining valid legacy theme
continues through its existing path; selecting or reimporting an available
legacy copy is an explicit action, not evidence of automatic recovery.

The retained redesign is deferred, not an alternative renderer, an active
profile contract or a rollback release prerequisite. This boundary does not
authorize deleting unrelated data or weakening package/revision/dirty-state
guards. Publication and live adoption require their own evidence.

## What changes and what does not

```text
complete theme -> app-wide committed skin
               + independent shell presentation preferences
               + temporary preview on the intended browser
               -> effective web presentation
```

The web client owns presentation; the host owns work, admission and authoritative
facts. Switching presentation preserves conversations, drafts, reading position,
artifact identity/version and required actions. It never changes providers,
tools, permissions or automation. Native and terminal clients have no obligation
to implement web appearances. Server validation/storage is a web capability,
not appearance authority over execution.

## Shared foundation

The restored built-in skin and shared components supply usable defaults and
accessibility behavior. Complete themes and custom CSS remain supported; no
Reference foundation or delta composition is required. Common functional fixes
belong in shared components, without rewriting saved user skins. An appearance
may tune supported geometry, not replace scrolling, focus, responsive safety or
control semantics with CSS workarounds.

## One active preference profile

This section's anchor is retained for existing links. The whole-profile promise
is temporarily retired; see [the rollback boundary](#temporary-rollback-boundary).
There is no active Global / This browser switch or per-setting replacement.

## Independent preferences

Theme definitions, shell presentation and operating-system accessibility
preferences remain separate inputs. Applying a theme changes the app-wide
committed look, not personal scheme, decorations, detail or required actions.
Preview is client-scoped and does not commit that look. Saving/importing/updating
a library entry is separate from applying it; existing committed CSS survives
library update or deletion. No profile copying or suggested-settings adoption
occurs as a side effect of selection.

- **Scheme:** light, dark or system; every appearance works in both palettes.
- **Decorations:** artwork on/off, independent of operational feedback.
- **Detail/Reveal:** supplementary information varies; pending work, errors,
  required decisions and cancel/recovery remain available at every level.
- **Accessibility:** honor OS reduced motion, forced colors, keyboard, touch,
  zoom and narrow layout. Static feedback carries the same meaning as motion.
  A separate global app motion preference is not part of this baseline.

The [work-discovery contract](work-discovery.v1.md) owns arrival/default-view
behavior; explicit view selection and responsive layout remain independent.

## Interaction economy

Follow [CX8](../../contracts/client-experience.v1.md). Reversible visual choices
apply directly with a clear selected state; do not add ritual submission steps.
Drafts, credentials, destructive operations and external effects retain their
submission/authority boundaries. Small common option sets can be visible;
disclosure should reduce overload, not hide required actions. Compare complete
keyboard and pointer journeys, not click count alone.

The rollback removes the profile source switch, not the consumer-usability
baseline. Merely opening or navigating Settings does not apply a theme.
Leaving Appearance or closing Settings ends its temporary library preview
without discarding a retained editor draft. Completion, revert and errors stay
beside the affected control; make client-local preview and app-wide apply clear.

Keep the ordinary screen focused on visual choices. Gallery previews use the
available content width rather than sharing half a row with persistence controls.
Related options form readable groups; advanced appearance options and package
authoring stay secondary to ordinary selection.
The selected appearance needs one visible selection indicator with accessible
single-selection semantics, not a selected-name pill plus radio, check and outline.
Checkbox collections are compact labeled
choices, not oversized tiles that split short labels across lines. This is a
shared settings/component responsibility, not a repair copied into each skin.
Consumer copy omits implementation terms and format versions; technical authoring
details stay in their own controls.

## Waiting and saving

| Situation | Required feedback and interaction |
| --- | --- |
| User starts an action | Immediate local busy feedback, meaningful label, duplicate pointer/keyboard suppression and retained focus. Existing icon slots keep their size; text-only controls own working labels or adjacent status. |
| Accepted long operation | Local status persists until the owning operation settles; an HTTP receipt alone is not completion. |
| Background refresh | Preserve usable content silently, without routine announcements or layout shifts; initial loading and failures remain explicit. |
| Conflicting save | Lock only inputs/actions that conflict; retain values and show status outside disabled fields. Close, cancel, help and unrelated editing remain usable. |
| Failure or uncertainty | Keep the draft and a local explanation with supported retry/check-status; never silently resend an uncertain effect. |
| Completion | Release the local lock, preserve focus/geometry and show brief local confirmation when useful. Inactive-page completion still releases its lock and retains any failure. |

`aria-busy` and `data-region-pending` describe activity; they do not authorize
subtree locking, opacity or pointer blocking. Components own precise native
input locking and long-operation lifetimes. Shared request suppression retains
keyboard focus; backend idempotency remains a separate boundary.

## Visual rules

- No shared busy-region tint, traveling stripe, page-wide pulse or success flash.
  Pending regions retain background, border and shadow. Optional explicitly
  selected activity artwork must stop under reduced motion, disappear with
  decorations off/forced colors and go dark when activity is idle or unknown.
- Busy icons inherit the control foreground, including contrasting on-accent ink.
  Reduced motion keeps a static equivalent; forced colors uses system colors.
- Keep one clear local status rather than spinners on every child. Preserve label
  meaning and focus indicators; do not fabricate text inside React-owned labels.
- Decorative paint cannot obscure controls, resize pressed/busy slots or convey
  the sole meaning of a state. A new frame must not shift geometry or cover focus.

## User and agent changes

Discover supported clients, operations and schemas. Target the intended client
and scope, read current revisions, preview the exact proposal and apply through
the same admitted operations as UI controls. Client identity routes; it does not
authenticate. Bounded presentation readback does not reveal private editor drafts.
Stale changes conflict without forcing old revisions or replacing newer work.

Preview stays local and never changes the app-wide committed skin. `theme.revert`
ends a local preview first; otherwise it conditionally reverses that client's
last application only when no newer theme has replaced it. Editor work remains
intact. Dirty component/viewer guards remain
in force. Saved/validated, applied and observed rendering are separate results;
an absent browser has an explicit availability result, not invented activation.

## Appearance editing operations

Legacy portable CSS and format-1 ZIP are supported. A ZIP has a root
`appearance.json` declaring `type: "appearance"` and `formatVersion: 1`, a
complete stylesheet and optional declared assets/notices. Validate the complete package before mutation;
failed import preserves library, selection, drafts and preferences. Bound sizes
and members; reject traversal, ambiguous names, links, corrupt/encrypted/special
members, unsupported types/versions and external fetches. Preserve assets,
attribution and notices on edit/export. License presence is not rights certification.
Import does not execute code, grant capabilities or apply presentation. Any later
component packaging retains the separate component trust/activation contract.

Library mutations and apply are separate operations. Saved-entry mutation uses
its exact fingerprint. `theme.import` accepts exactly one of `css` or
`packageBase64`; `theme.export` defaults to CSS, with explicit `format: "zip"`
for ZIP. A library ID exports that saved entry; omission exports the committed
appearance, never a preview or editor draft. Editor targets and drafts survive
unrelated saves, page changes and late results. Failed persistence is explicit.
Package update/export retains assets, attribution and notices.

The redesign's clean break is superseded by the rollback. Format-2 saved
packages remain inert and undeleted, not silently converted into format 1.

## Acceptance

Check exact revisions in a real browser: the restored built-in and bundled appearances,
light/dark/system, decorations on/off, detail levels, reduced motion, forced
colors, narrow layout and zoom. Exercise actual pending/failure/recovery states,
not just thumbnails. Focused rollback checks discriminate active legacy values
from inert newer records: valid legacy theme retention, absent-theme default,
format-1 round-trip, rejected format-2 activation without data deletion, and
preview/apply/revert with independent presentation and drafts preserved.
Use the same content for visual A/B review. Package/schema validation alone
proves neither readability nor accessibility; this document is not that evidence.
Exercise stale edits, failed saves, reload and disconnect without replaying
uncertain effects. Inspect the complete settings page for readable
labels, useful grouping and bounded scrolling at narrow widths and enlarged text;
absence of viewport overflow alone is not usable-layout evidence.

### Chat Plan placement

Supported placements consume one read-only projection of the selected chat's
latest successful todo receipt. Child plans never replace a root's. Placement
changes neither plan nor execution. Canonical events own reports independently
of transcript windows/activity pages. Show completion count/current step and an
expandable checklist. Working requires an active owning turn; paused/failed is
unfinished. Completed items are agent reports, not proof of deliverables. Empty
reports clear the Plan; unreadable/oversized current reports show unavailable,
not an older substitute. Essential Plan information remains available at every
detail level without requiring animation.

### Transcript overflow cues

Only edges with continuing content fade; short content and the final bottom edge
remain opaque. Preserve anchor/follow/Latest/chat-return behavior and scrollbar
gutter. Mask the transcript alone, not composer, navigator or Latest control.
Suppress masks for keyboard-visible focus and active input/textarea/select/
contenteditable/iframe editing; forced colors removes them. Pointer/programmatic
focus on a non-visible container alone does not suppress fades. The host document's
active iframe is authoritative without reading its child; inactive/removed frames
must not suppress fades. Decorations-off does not remove this functional cue.
Appearances tune documented lengths, never add masks/listeners or reposition children.

### Credential saving and file selection

Credential saves lock the conflicting source/key fields, clear secrets only after
confirmed acceptance and retain input on rejection/missing receipt. Drafts remain
private. File selectors show an operation-specific working label and reject
concurrent selection/drop while processing; preserve button size and dismissal.

### Folder browsing

Lookups show immediate local working status while retaining prior results. Lock
path/competing navigation only; keep Close/unrelated fields available. Failure
retains path/list with retry. Busy state does not restyle the listing surface.

### Saving file access settings

Lock only the selected scope and conflicting path editors/pickers. Keep drafts,
local status, stable action geometry and dismissal. Rejection retains scope and
paths; correction/retry cannot display an older success as the new result.

### Shared surface corners

Shared surfaces consume appearance geometry consistently. Full-screen outer
edges stay square; circular status/send controls retain functional shapes.

### Execution metrics

Allowlisted metric selection and visibility are display preferences, not execution
or accounting changes. Explicit scope choices override Reveal; omission uses its
defaults and an empty metric selection shows none. Preserve other scope entries.
Keep known, partial, estimated, live/pending and unavailable distinguishable.
Exports contain only selected accounting fields and bounded identity, never
reasoning text or request/response captures. Do not fabricate missing telemetry.
Wrap on narrow layouts; hidden totals do not initiate accounting reads. Current
field/default details live in [Themes](themes.md#presentation-fields).

## Reference mapping

<a id="global-presentation-hooks"></a>

**Global presentation hooks** and header geometry now live in
[Themes](themes.md#global-presentation-hooks).

<a id="what-is-implemented-and-what-still-needs-work"></a>

**What is implemented and what still needs work** is described by current API
documentation and focused tests, not conformance claims in this contract.
Other established section names above retain their observable promises;
test filenames/history no longer live here. The
[October 20 decision](../ux/decisions/20261020.md) remains the basis for quiet refresh.
