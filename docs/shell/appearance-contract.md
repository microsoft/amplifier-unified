# Web appearances: interaction contract

This boundary serves web users, appearance authors, component maintainers and
agents changing presentation. The [vision](appearances.md) defines the destination.
[Themes](themes.md) owns current tokens, package fields and API details; the
[design note](../ux/decisions/20261109-appearances.md) records the November 9 direction.
These promises govern the coordinated Reference and scoped-preference cutover.
Discover the target installation's current schemas before acting; source changes
and this document alone do not establish installed or browser conformance.

## What changes and what does not

```text
shared library -> one complete profile: Global OR This browser
                                      + temporary document preview
                                      -> effective web presentation
```

The web client owns presentation; the host owns work, admission and authoritative
facts. Switching presentation preserves conversations, drafts, reading position,
artifact identity/version and required actions. It never changes providers,
tools, permissions or automation. Native and terminal clients have no obligation
to implement web appearances. Server validation/storage is a web capability,
not appearance authority over execution.

## Shared foundation

A neutral Reference foundation always supplies the common usable components and
accessibility fallbacks. Ordinary appearances contain only intentional differences;
compatible shared fixes propagate without copying the base or updating each skin.
Reference is available through advanced controls and recovery, not the ordinary
gallery. An appearance may tune supported geometry, not replace scrolling, focus,
responsive safety or control semantics with CSS workarounds.

## One active preference profile

One **Global / This browser** selector chooses the complete profile used by this
browser and the destination of edits. Global is stored in the service for the
authenticated host owner; This browser is stored in this browser profile under
that owner's service namespace. Both choices show the exact same settings
controls and layout. There are no per-setting scopes or partially inherited
browser values.

Global is the initial choice. On the first explicit switch to This browser,
initialize its complete profile from the current Global settings, including
resolved defaults and nested choices. Later switches retain both profiles.
Returning to Global never publishes or deletes the saved browser profile.
Global changes reach browsers using Global; no field of a saved browser profile
follows subsequent Global changes. Browser storage loss returns the browser to
Global without changing the service record. A storage failure leaves the last
confirmed source and values intact and never falls back to a service write.
Tabs in one browser profile share the selected source and saved browser profile,
not temporary previews or private editor drafts.

UI and agent readbacks identify the single active source, effective values and
write destination. Changes use current revisions; source switches and local
edits become committed only after the admitted browser acknowledges storage.
Authentication, not a client ID, determines the owner; Global is not a promise
of cross-host synchronization. Package storage remains global independently of
profile selection. Previews/editor drafts are not committed profiles.
Package import does not select it. Ordinary appearance
selection changes the look, not scheme, decorations, detail or other personal
settings. Adopting suggested experience settings is a separate explicit action:
preview the intended field changes and active profile before applying.
Neither action switches the active profile. Package updates do not reapply
suggestions over personal choices.

- **Scheme:** light, dark or system; every appearance works in both palettes.
- **Decorations:** artwork on/off, independent of operational feedback.
- **Detail/Reveal:** supplementary information varies; pending work, errors,
  required decisions and cancel/recovery remain available at every level.
- **Accessibility:** reduced motion, forced colors, keyboard, touch, zoom and
  narrow layout remain usable. Static feedback carries the same meaning as motion.

The [work-discovery contract](work-discovery.v1.md) owns arrival/default-view
behavior; explicit view selection and responsive layout remain independent.

## Interaction economy

Follow [CX8](../../contracts/client-experience.v1.md). Reversible visual choices
apply directly with a clear selected state; do not add ritual submission steps.
Drafts, credentials, destructive operations and external effects retain their
submission/authority boundaries. Small common option sets can be visible;
disclosure should reduce overload, not hide required actions. Compare complete
keyboard and pointer journeys, not click count alone.

Use the familiar two-choice selector used for Browse/Status in the sidebar.
It is a source switch, not an editor-only tab: selecting a choice applies that
profile and subsequent edits write there directly. Explain the two destinations
once in plain language. Merely opening, leaving or navigating Settings changes
neither source nor values. No secondary browser-settings page, setting picker,
per-row scope badges, clear/share actions or extra Save step is needed.
Completion, Undo and errors stay beside the affected control; source-switch
feedback stays beside the source selector.

Keep the ordinary screen focused on visual choices. Gallery previews use the
available content width rather than sharing half a row with persistence controls.
Related options form readable groups; advanced appearance options and package
authoring belong at the bottom of the same page in either source.
The selected appearance needs one visible selection indicator with accessible
single-selection semantics, not a selected-name pill plus radio, check and outline.
Checkbox collections are compact labeled
choices, not oversized tiles that split short labels across lines. This is a
shared settings/component responsibility, not a repair copied into each skin.
Consumer copy omits implementation terms such as sparse overrides, Reference
deltas and format versions; technical authoring details stay in their own controls.

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

Preview stays local and never publishes shared preferences. Cancelling it leaves
saved settings and editor work intact. Undo checks intervening changes and cannot
roll back someone else's newer preferences. Dirty component/viewer guards remain
in force. Saved/validated, applied and observed rendering are separate results;
an absent browser has an explicit availability result, not invented activation.

## Appearance editing operations

ZIP is the portable envelope. Validate the complete package before mutation;
failed import preserves library, selection, drafts and preferences. Bound sizes
and members; reject traversal, ambiguous names, links, corrupt/encrypted/special
members, unsupported types/versions and external fetches. Preserve assets,
attribution and notices on edit/export. License presence is not rights certification.
Import does not execute code, grant capabilities or apply presentation. Any later
component packaging retains the separate component trust/activation contract.

Library mutations and selection/preferences are separate operations. Saved-entry
mutation uses its exact revision/fingerprint; export requires a library ID and
produces a ZIP, never an unsaved draft, preview or implicit applied snapshot. Editor targets and drafts
survive unrelated saves, page changes and late results. Failed persistence is
explicit. Scope changes do not silently promote local values to shared ones.

The November 9 redesign replaces shipped appearance representations together,
without legacy custom-CSS compatibility paths. This supersedes old appearance
format/preservation guarantees at the coordinated cutover, not unrelated data
or public component contracts. Until then, installed behavior is in [Themes](themes.md).

## Acceptance

Check exact revisions in a real browser: Reference and bundled appearances,
light/dark/system, decorations on/off, detail levels, reduced motion, forced
colors, narrow layout and zoom. Exercise actual pending/failure/recovery states,
not just thumbnails. Test Global and complete browser profiles, first-use copying,
repeated switches retaining both profiles, Global changes while local is active,
storage loss/failure, stale edits, previews, reload and disconnect. Test cross-tab
source changes without leaking previews or drafts. Use the same content for visual A/B review. Package/schema
validation alone proves neither readability nor accessibility.
Also exercise the ordinary preference journey, selecting This browser once and
editing multiple fields without any Global write, then returning to Global and
back to the retained browser settings. Compare the same control layout in both
sources. Inspect the complete settings page for readable
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
