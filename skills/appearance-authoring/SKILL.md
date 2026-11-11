---
name: appearance-authoring
description: "Create, revise or package Amplifier Unified complete themes and CSS appearances with light/dark support and safe preview/revert. Load for appearance authoring, not merely choosing a look. Component/layout authoring belongs to amplifier-shell."
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

Read app state and discover current schemas for `theme.*`; load `amplifier-shell`
for supported layout/components. Identify the intended browser, starting saved
entry/fingerprint, effective presentation and revert path. Read bounded CSS and
actual DOM/computed styles before overriding; never reconstruct truncated CSS.
Use supported actions, not filesystem settings or database edits.

The user-approved PR1916 rollback temporarily retires Reference and Global /
This browser profiles after the `.078` visual regression. Read the linked
contract's rollback boundary: preserve `shared-reference-v1`, browser-local
profiles and format-2 saved libraries inert and undeleted. Do not convert them,
seed legacy preferences from them, or claim automatic recovery of the theme
removed by `.078`. An absent legacy theme uses the safe old built-in default.

Inspect the committed theme, client presentation and current revision. Preview
is client-scoped; apply changes the app-wide committed skin. Library save/import/
update does not apply it. Preserve unrelated presentation, Canvas work, drafts,
providers, bundles and the person's selected skill policy.

## Author intentional differences

Choose exactly one input to `theme.preview` / `theme.apply`, with a name:
- `definition`: a new version-1 theme with all ten colors in both palettes. It
  starts from the built-in skin rather than inheriting prior artwork.
- `tokens`: an intentional partial palette edit retaining the existing skin and
  background; structured themes update both palettes.
- `css`: a complete self-contained skin with light/dark styling.

Do not apply a palette-only edit and claim to have replaced the previous artwork.
Portable CSS and format-1 ZIP styling packages are re-enabled. Reference deltas
and format-2 packages are not current authoring inputs. Common functional repairs
still belong in shared components, not duplicated theme workarounds.

For source changes, styles and browser modules belong to the selected
`amplifier-client-web` repository. Follow the linked API's source-ownership section
and Unified's `components/README.md`; delivery snapshots and disposable prepared
source are not another client authoring owner.

Prefer semantic tokens, contrasting on-accent ink and stable parts over label,
child-position or broad descendant selectors. Inspect actual font inheritance.
Paint message bodies and inner composer surfaces, not structural rows or actions. Keep scrolling,
responsive safety, control sizing, focus, overflow cues and lifecycle semantics
with their shared owners; do not add masks, listeners or positioning workarounds.

Author both palettes deliberately. Keep artwork readable and separate from
functional feedback. Honor decorations off, reduced motion and forced colors.
Use authorized, self-contained assets and neutral names; preserve attribution
and font licenses. Prefer system stacks; additional fonts must earn their cost
in realistic comparisons. No external URLs/imports or executable package content.
For format-1 ZIPs use a root `appearance.json`, complete local CSS and declared
assets/notices. ZIP styling does not install components or shell composition.

## Preview, verify and deliver

Preview the exact proposal on the intended browser before committing within the
requested scope. A preview, validated package or thumbnail is not visual proof.
Check both schemes/system, narrow/zoom, decoration/accessibility preferences,
keyboard focus, long chat/code, composer and actual busy/error/recovery states.
Preserve unsent work without inspecting its private contents unless requested.
Verify that applying a theme leaves scheme, decorations, Reveal and accessibility
preferences unchanged. Preserve other clients' conversation selection and make
the app-wide apply scope explicit. When changing persistence for this rollback,
check retained valid legacy themes, safe absent-theme defaults and inert newer
data separately; a default is not proof of personal-theme recovery.

Retain exact entry/revision/fingerprint identities. Keep library mutations,
application and preview distinct; use stale/dirty guards and conditional recovery.
`theme.revert` ends a preview first, otherwise reverses the client's last apply
only if no newer theme replaced it. Saved-entry update/delete requires
`expectedFingerprint` from `theme.read`; included themes are read-only, so copy
them. Library updates/deletion leave committed CSS intact.
Export by saved ID for that entry; omission means committed CSS, never the
preview/editor draft. Default export is CSS; request `format: "zip"` for a
format-1 package. Round-trip exported bytes through import before claiming
portability. Discover action schemas rather than copying
payloads from a different build. An uncertain effect needs its saved receipt and
current state, not a new request identity.

Report the deliverable, write scope, observed checks, limits and recovery path.
Offline authoring is valid; label application and browser review unverified.