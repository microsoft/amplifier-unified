---
name: appearance-authoring
description: "Create, revise or package Amplifier Unified Reference appearance deltas with light/dark support, scoped profiles and safe preview/cancel. Load for authoring, not merely choosing a look. Component/layout authoring belongs to amplifier-shell."
---

# Author an appearance

Work inline so the person can review and steer. Presentation never changes
execution, permissions or work meaning. Resolve companions from this returned
`skill_directory`: [direction](../../docs/shell/appearances.md),
[contract](../../docs/shell/appearance-contract.md),
[API/tokens/packages](../../docs/shell/themes.md), [examples](examples.md).

## Inspect scope and installed support

Discover `appearance.*`, `theme.*` and relevant `shell.*` schemas. Identify the
intended browser, active complete Global / This browser profile, selected library
ID/fingerprint and CAS dimensions. Client identity routes; it does not authenticate.
Read bounded exact CSS and actual DOM/computed/platform fonts before overriding.
Use admitted operations, never settings/database edits or private draft reads.

The production generation uses new records and a new library. Legacy complete
skins, `.078` profiles/libraries and 0.80.1 experiment browser records remain inert
and undeleted, not automatic migration or recovery inputs. Missing Global reads as
explicit complete defaults; malformed new data fails without overwrite/fallback.
Do not claim preservation of an old personal look or recover a removed theme.

## Author intentional differences

Reference owns geometry, focus, scrolling, native controls, responsive behavior and
functional cues. Author small intentional deltas, not a copied complete base.
Preview `{name, css, clientId}` through `appearance.preview`; cancel through
`appearance.preview.cancel`. Preview is document-local and distinct from saved
library/selected profile. `theme.save` wraps named delta CSS into format-2 ZIP;
`theme.import` accepts only `packageBase64`, and export requires a saved/included
library ID. The root manifest requires `type: appearance`, `formatVersion: 2`,
`base: reference-v1`, name and stylesheet. Keep declared assets/notices and bounds.
No external URLs/imports, executable content, SVG assets, raw CSS import or format-1
promotion. An explicit valid format-2 import is a new copy, not automatic adoption.

For source changes, browser source/tests belong to canonical `amplifier-client-web`;
follow the linked ownership/whole-snapshot assembly workflow. Generated static and
disposable prepared source are not authoring owners. Common repairs belong in
shared components, not repeated per appearance.

Use semantic tokens, contrasting on-accent ink and stable parts. Paint message
bodies/inner composer surfaces, not structural rows/actions. Keep independent UI,
heading, prose and code font roles. Prefer system stacks; additional fonts must earn
their cost and preserve licenses. Both palettes, decorations off, reduced motion,
forced colors, zoom/narrow and keyboard/touch must remain usable. Artwork cannot
convey the sole meaning of activity, error or required recovery.

## Preview, select, verify and deliver

Use exact named preview bytes and inspect actual rendered outcomes. A package,
thumbnail, successful save, storage ACK and render ACK are distinct facts.
Select a library ID with `appearance.preferences.update` at the captured scope/CAS.
Global and This browser are complete independent profiles, not field overlays.
The first browser selection explicitly copies current Global at its revision;
subsequent switches retain both. Browser writes require generation, mirror revision,
fresh storage-version CAS and ACK; failure never writes Global as fallback.

The selected look owns its default accent. Different-ID edits drop the old override
unless a new color is explicit in that patch; same-ID/unrelated edits retain it.
`patch: {accent: null}` removes only the override, restoring the current look's own
color without storing null. Ordinary UI choices apply directly without Saved/Undo
rituals. Public Undo remains exact whole-profile CAS; stale Undo is not a reset and
must never be retried at a newer revision. Preview Cancel and Archive Undo remain.

Preserve conversations, unsent/dirty work, Canvas identity, unrelated preferences,
providers, bundles and selected skill policy. Private editors remain private.
Library updates/deletion use `expectedFingerprint` and never rewrite selection;
a missing selected package is visibly unavailable with Reference recovery.
Save a copy of read-only included appearances. Keep editor ID/fingerprint/draft
unchanged by background refresh. Export by exact library ID as ZIP and round-trip
actual bytes before claiming portability. No-id/current/draft export is unsupported.

Check realistic long chat/code, composer, pending/error/recovery, both schemes/system,
narrow/zoom, focus, accessibility, preview/cancel, selection/reset, reload/cross-tab,
source clone and byte preservation. Installed discovery, source/build identity,
observed rendering and human acceptance remain separate. An uncertain effect needs
its original receipt/current state, never a new request identity. Report exact
scope, deliverable, observed checks, limits and recovery. Offline authoring is valid
when application/browser review are explicitly unverified.
