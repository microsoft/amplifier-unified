# Appearances

Appearances is Amplifier's umbrella term for the way people experience the app:
its visual identity, arrangement, information hierarchy, components, artifact
viewers and interaction feedback. A theme is one part of an Appearance.

People should be able to describe the experience they want in ordinary language,
choose an existing one, or ask an agent to create or adapt one. Examples include
a quiet writing workspace, a compact research workspace with references nearby,
and a visual workspace for comparing generated images. Each must remain a
usable Amplifier experience, with the same underlying work and recovery paths.

## Contract

An Appearance may compose:

- Themes: light/dark palettes, typography, spacing, surfaces and optional artwork.
- Layout and registered components: navigation, supported shell slots and viewers.
- Information presentation: hierarchy, density, progressive disclosure and Reveal.
- Interaction presentation: loading, saving, success, failure and attention states.

Accessibility and user preferences constrain every Appearance. Applying one
must not silently change reduced motion, contrast, Reveal, account access, tool
permissions or automation. Required decisions and recovery remain discoverable.
See the [interaction contract](appearance-contract.md) for observable behavior.

The host owns conversation data, execution, security, drafts, artifact versions,
account status and action semantics. Customization consumes those owners through
the published contracts; it must not recreate competing stores or bypass guards.
Inline previews, galleries, fullscreen views and Canvas refer to the same saved
artifact identity and version. A view change does not create or regenerate work.

## User and agent authoring

The supported workflow is discover, propose, preview, verify, apply, and retain
a way back. An agent targets the intended client, discovers the installed schemas
and registered slots, preserves unaffected components and user drafts, and
previews the requested change. Browser evidence must match the proposed revision.
A successful schema check alone does not prove a working or accessible design.
Changes use existing revision and dirty-edit guards. Revert restores presentation
without rolling back conversation content or repeating an action.

Users and agents use the same host operations. A user can ask for a different
experience without learning CSS or component APIs; agents use those APIs on their
behalf. Imported executable components retain their trust and capability rules.

## Shipped building blocks and remaining work

Current contracts cover complete themes, preview/revert, client presentation,
registered navigation modules, named component contributions and Canvas renderers.
See [themes](themes.md), [components](components.md), and the [shell API](README.md).
The [authoring skill](../../skills/amplifier-shell/SKILL.md) explains how agents use
them. Internal `theme.*`, `shell.*` and `canvas.views.*` names remain API names.

A single portable Appearance package that atomically bundles all of these,
a unified experience gallery, cross-device preference synchronization, arbitrary
replacement of the transcript/composer, and universal conformance tooling are
future work. Do not describe them as installed features. Each new customization
surface needs explicit ownership, capabilities, persistence, accessibility,
compatibility, failure recovery, preview and revert contracts before exposure.
