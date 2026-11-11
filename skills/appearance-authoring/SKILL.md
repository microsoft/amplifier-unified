---
name: appearance-authoring
description: "Create, revise or package Amplifier Unified web appearances with light/dark support, scoped preview and recovery. Load for appearance authoring, not merely choosing a look. Component/layout authoring belongs to amplifier-shell."
---

# Author an appearance

Work inline so the person can review and steer. Appearances change web
presentation, never execution, permissions or the meaning of work.

Resolve companions from the returned `skill_directory`:
- [Appearance direction](../../docs/shell/appearances.md)
- [Interaction contract](../../docs/shell/appearance-contract.md)
- [Current API, tokens and package format](../../docs/shell/themes.md)
- [Examples](examples.md), only when constructing an input or checking a result

## Inspect scope and installed support

Read app state and discover current schemas for `appearance.*` and `theme.*`; load `amplifier-shell`
for supported layout/components. Identify the intended browser, starting saved
entry/fingerprint, effective presentation and revert path. Read bounded CSS and
actual DOM/computed styles before overriding; never reconstruct truncated CSS.
Use supported actions, not filesystem settings or database edits.

Reference is always the foundation; a package contains styling differences over
`reference-v1`, never a complete copy of the application. ZIP is the portable
envelope. Read the linked API for the installed package version and limits.
Library save/import/update do not choose an appearance. One **Global / This
browser** choice selects the complete active preference profile.

Inspect the active profile, effective values and current revision.
Local writes never publish Global values; selecting Global retains the browser
profile for later use. Do not infer the source from the selected package or switch it
as a side effect of authoring. Preserve unrelated presentation, Canvas work,
drafts, providers, bundles and the person's selected skill policy.

## Author intentional differences

Author a small CSS delta over Reference, not copied base/component CSS. Omitted
properties use the foundation, never the previous appearance. Repeated functional
repairs belong in shared components. Do not use full-skin definitions, palette
compiler inputs, legacy CSS imports or compatibility conversion.

For source changes, Reference and browser modules belong to the selected
`amplifier-client-web` repository. Follow the linked API's source-ownership section
and Unified's `components/README.md`; delivery snapshots and disposable prepared
source are not another client authoring owner.

Prefer semantic tokens, contrasting on-accent ink and stable parts over label,
child-position or broad descendant selectors. Separate font roles and painted
message bodies/composer surfaces from structural rows/actions. Keep scrolling,
responsive safety, control sizing, focus, overflow cues and lifecycle semantics
with their shared owners; do not add masks, listeners or positioning workarounds.

Author both palettes deliberately. Keep artwork readable and separate from
functional feedback. Honor decorations off, reduced motion and forced colors.
Use authorized, self-contained assets and neutral names; preserve attribution
and font licenses. Prefer system stacks; additional fonts must earn their cost
in realistic comparisons. No external URLs/imports or executable package content.
Use supported ZIP assets/notices rather than duplicating the application.

## Preview, verify and deliver

Preview the exact proposal on the intended browser before committing within the
requested scope. A preview, validated package or thumbnail is not visual proof.
Check both schemes/system, narrow/zoom, decoration/accessibility preferences,
keyboard focus, long chat/code, composer and actual busy/error/recovery states.
Preserve unsent work without inspecting its private contents unless requested.
This browser means the same browser profile, host and authenticated preference
owner, not a single tab. When changing preference or persistence mechanisms,
test Global clients, multiple tabs, complete independent browser values and
source switching. Storage failure must stay a failure, never become a Global write.

Retain exact entry/revision/fingerprint identities. Keep library mutations,
selection and preview distinct; use stale/dirty guards and conditional recovery.
Cancel preview separately from conditional undo of a preference change. Ordinary
selection changes only the look. Suggested experience profiles, if supported by
the installed schema, require their own explicit review/apply and retain the
active profile; do not infer support from an appearance's metadata.
Do not export a preview/editor draft as saved work. Round-trip ZIP bytes through
import before claiming portability. Discover action schemas rather than copying
payloads from a different build. An uncertain effect needs its saved receipt and
current state, not a new request identity.

Report the deliverable, write scope, observed checks, limits and recovery path.
Offline authoring is valid; label application and browser review unverified.