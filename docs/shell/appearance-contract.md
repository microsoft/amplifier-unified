# Web appearances: interaction contract

This boundary serves web users, appearance authors, component maintainers and
agents changing presentation. The [vision](appearances.md) defines the destination.
[Themes](themes.md) owns current tokens, package fields and API details. The
user-approved production redesign supersedes the temporary PR1916 rollback:
one Reference foundation, intentional deltas and one active complete profile.
Discover the target installation's current schemas before acting; source changes
and this document alone do not establish installed or browser conformance.

## Temporary rollback boundary

This anchor records the retired boundary, not a second active renderer. Legacy
`theme`, old shell presentation, `reference-v1` / `shared-reference-v1` profiles,
the 0.80.1 experiment and their browser caches/editors/libraries remain **inert and
undeleted**. The new production generation never promotes, converts, overwrites
or reconstructs them. The entire old `themes/` directory, including loose CSS
and both ZIP versions, stays unlisted and unresolved. Explicit format-2 import
copies validated content into the new library; it is not automatic recovery.

Production profiles use `shared-reference-v2` and an independent durable
`appearance-identity/v2` in the authenticated owner's private app database.
The identity is not a preference: absent Global preferences remain virtual
defaults without a write. Its random owner scopes a stable
`amplifier.appearance.v2:<owner>` browser namespace across cold restarts and the
first later Global edit. New preferences use the same owner. Invalid new owner
or profile records fail explicitly, without a replacement owner, overwrite,
default fallback or implicit repair. Restoration uses a valid backup, not
forced revisions. An unavailable selected library item remains selected and
visible as unavailable, with Reference as usable recovery; no preference rewrite.

The new baseline does not preserve the previous look or independent presentation
choices: Amplifier, system scheme, balanced layout, comfortable density,
decorations on, standard interface/execution detail, all execution scopes and ten
metrics, conversation status off, appearance start view, interim replies expanded,
Plan above composer, and no accent override. The release notice must disclose
this impact. Independent compatible component placements and module views survive;
old appearance previews/prepared requests/storage commands do not activate.
Publication, installed qualification and live adoption remain separate evidence.

### Release notice proposal

This is authored guidance for the release owner, not a claim that this checkout
is already published:

> **Appearance preferences start from a new baseline.** This update replaces
> complete themes with Reference-based appearances and independent Global / This
> browser profiles. The initial profile is Amplifier, system color scheme,
> balanced layout (Canvas on the right), comfortable spacing, decorations on,
> standard interface and execution detail, every execution scope and all ten
> metrics, conversation status off, appearance start view, expanded interim
> replies, Plan above the composer, and no accent override. Previous presentation
> choices, complete custom themes and trial profiles remain stored but inactive;
> they are not automatically converted. Choose an appearance and preferences in
> Settings, or explicitly import a valid Reference appearance package. “Use
> appearance accent” removes a color override. No action is needed to continue
> chatting; review Settings if you want different presentation choices.

Independent component placements, module views, conversations and drafts are not
reset by this baseline. Returning to an earlier package reads its separate old
records, not a reconstruction or conversion of new preferences. Back up private
app data before any manual restoration. The publisher must attach this notice to
the actual version carrying the paired client/host change and assess any combined
unpublished changes; this proposal assigns no version or notice acknowledgement.

## What changes and what does not

```text
Reference + selected appearance delta
          + one complete Global OR This browser preference profile
          + document-local temporary preview -> effective web presentation
```

The web client owns presentation; the host owns work, admission and authoritative
facts. Switching presentation preserves conversations, drafts, reading position,
artifact identity/version and required actions. It never changes providers,
tools, permissions or automation. Native and terminal clients have no obligation
to implement web appearances. Server validation/storage is a web capability,
not appearance authority over execution.

## Shared foundation

Reference and shared components supply usable defaults and accessibility behavior.
Appearance packages contain deliberate differences, not copied structural rules.
Common functional fixes belong in shared components, without rewriting saved user
packages. An appearance
may tune supported geometry, not replace scrolling, focus, responsive safety or
control semantics with CSS workarounds.

## One active preference profile

Global and This browser are independent complete profiles, never per-setting
overlays. Source switching retains both. The first explicit browser selection
copies current Global at its captured revision. Browser storage persists the
whole source/record under a generation, mirror revision and fresh storage-version
CAS; commit and rendered acknowledgement are distinct. Failed/unavailable storage
never writes Global or claims activation. Reload/new tabs restore confirmed
records only, not previews. Cross-tab changes retire obsolete pending commands.

## Independent preferences

Scheme, decorations and detail are independent choices within the active complete
profile; OS accessibility constraints remain effective. Appearance selection
changes the captured profile's library ID without adopting suggested settings.
The appearance owns its default accent: changing to a different ID clears the old
override unless that same edit explicitly supplies a new color. Same-ID and
unrelated edits retain it. `patch: {accent: null}` removes only the override at
captured scope/CAS, never stores null or changes the current look/unrelated fields.
Preview is document-local. Library mutations do not select an appearance.
Current selections resolve exact library bytes; deletion is visibly unavailable,
not retained CSS mislabeled as the package.

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

Merely opening or navigating Settings does not change preferences.
Leaving Appearance or closing Settings ends its temporary library preview
without discarding a retained editor draft. Ordinary choices apply directly without
Saved toasts or Undo banners; explicit reset and Preview Cancel remain available.
Archive Undo is unrelated and preserved. Errors stay beside their control.

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

Preview stays local and never changes either profile. Public
`appearance.preferences.undo` remains conditional on its exact whole-profile CAS;
`appearance_undo_stale` refuses newer work without a fresh-revision retry. Ordinary
UI does not expose these Undo tokens. Editor work and dirty component/viewer guards remain
in force. Saved/validated, applied and observed rendering are separate results;
an absent browser has an explicit availability result, not invented activation.

## Appearance editing operations

Strict format-2 Reference ZIPs are supported. A ZIP has a root
`appearance.json` declaring `type: "appearance"`, `formatVersion: 2`,
`base: "reference-v1"`, a delta stylesheet and declared assets/notices.
Validate the complete package before mutation;
failed import preserves library, selection, drafts and preferences. Bound sizes
and members; reject traversal, ambiguous names, links, corrupt/encrypted/special
members, unsupported types/versions and external fetches. Preserve assets,
attribution and notices on edit/export. License presence is not rights certification.
Import does not execute code, grant capabilities or apply presentation. Any later
component packaging retains the separate component trust/activation contract.

Library mutations and selection are separate operations. Saved-entry mutation uses
its exact fingerprint. `theme.save` wraps named delta CSS as ZIP;
`theme.import` accepts `packageBase64`; `theme.export` requires a library ID and
returns that exact ZIP, never a preview or editor draft. Editor targets and drafts survive
unrelated saves, page changes and late results. Failed persistence is explicit.
Package update/export retains assets, attribution and notices.

Old library files remain inert and undeleted in `themes/`; new operations target
only `appearance-library-v1/`. New format-1 imports and raw CSS imports are rejected.

## Acceptance

Check exact paired revisions in a real browser: Reference and bundled appearances,
light/dark/system, decorations on/off, detail levels, reduced motion, forced
colors, narrow layout and zoom. Exercise actual pending/failure/recovery states,
not just thumbnails. Focused production checks discriminate fresh active records
from inert old ones: cold first paint, no Global write on read, stable owner
identity/reload, strict malformed-record failure, format-2 round-trip, old library
nonactivation, local ACK/render ACK, source clone/cross-tab, preview/cancel,
accent reset/switch and draft/Canvas preservation.
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
