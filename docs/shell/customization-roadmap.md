# Shell customization: status and next milestones

Source audit: 2026-09-21 UTC, Microsoft Unified `e2dd77d1` (package 0.19.15).
This is a code and contract audit, not new browser acceptance or a deployment
claim. The milestones below are proposals; they are not additional SDK APIs.

## What can be replaced today

| Surface | Current contract | Remaining boundary |
| --- | --- | --- |
| Workspace manager and chat list | Registered navigation modules, including custom modules and up to 12 instances with stable IDs | `navigation` is the only composition slot; snapshots and actions are bounded to navigation |
| Artifact content viewers | Validated renderer packages selected per canvas view; primary and pinned secondary views | A renderer does not replace canvas tabs, toolbar, view routing or the whole canvas container |
| Interactive conversation surfaces | Sandboxed HTML/CSS/JS, typed state and events, stable identity, revisions, restore and shared user/agent interaction | Conversation artifacts, not installable shell modules; privileged requests currently allow only theme preview/apply/revert |
| Appearance and layout | Complete CSS skins, palette changes, scheme, density, accent and three layout presets | No complete structured theme/background contract or extensible layout registry |
| Conversation header, messages, execution display, composer, app toolbar, voice controls, Settings and status/notifications | Built-in React components using existing host actions | Separate source files do not make these registered, hot-swappable modules |

The existing loader is real: stage, host validation, prepare, preview/apply,
browser activation evidence, and revert/recovery. Dirty instances defer unsafe
replacement; compatible host-owned view state can survive replacement. Native
modules are trusted same-origin code. Compatibility validation does not provide
an isolation boundary for untrusted code.

Evidence: [`shell_modules.py`](../../amplifier_web/shell_modules.py),
[`shell-sdk`](../../packages/shell-sdk/index.d.ts),
[`shell runtime`](../../frontend/src/shell/runtime.jsx),
[`main.jsx`](../../frontend/src/main.jsx), and
[conversation surfaces](conversation-surfaces.md). The navigation SDK deliberately
does not expose transcripts, composer drafts, credentials or runtime plans.

## Current issue map

Use Microsoft issue numbers for new work. Original items remain historical
discussion links.

| Microsoft issue | Original | Relationship to customization |
| --- | --- | --- |
| [#28: theme backgrounds](https://github.com/microsoft/amplifier-unified/issues/28) | bkrabach #157 | Direct theme gap: palette application retains the old decorative background; requested preview parity and artwork toggle are absent |
| [#43: Canvas Apps](https://github.com/microsoft/amplifier-unified/issues/43) | bkrabach #83 | Core surface/state/event/revision/observation behavior exists; reconcile acceptance against current code before closing. General host capability requests and common semantic controls remain bounded or incomplete |
| [#29: sidebar shortcuts](https://github.com/microsoft/amplifier-unified/issues/29) | bkrabach #154 | Keep keyboard navigation on the shared selection path and preserve dirty-view/draft guards; coordinate with sidebar work |
| [#39: execution observability](https://github.com/microsoft/amplifier-unified/issues/39) | bkrabach #88 | A useful consumer of execution/status component contracts; current summary detail levels do not fulfill all requested scopes and metrics |
| [#38: voice catalog](https://github.com/microsoft/amplifier-unified/issues/38) | bkrabach #89 | A future voice/settings contribution using provider capabilities and shared actions |

[Work parity #31](https://github.com/microsoft/amplifier-unified/issues/31) and
[integration PR #19](https://github.com/microsoft/amplifier-unified/pull/19) contain
related pending work. Presence on a migrated branch is not evidence of release
or of an extension contract. Check their current state separately.

## First milestone: a complete theme definition

`canvas_apps.theme_input` appends palette declarations to the existing CSS skin.
Its ten permitted palette keys do not include `--a-gradient-top` or
`--a-gradient-bottom`, which drive the default shell background in `unified.css`.
A new palette therefore retains those old gradient values. Full CSS can already
change the background, but the smaller theme API cannot describe a complete one.
This supports the mechanism reported in #28; it is not a replay of the original
Quiet Water conversation.

Introduce a versioned theme definition with palette, light/dark variants and
an explicit background treatment, including gradients, patterns and managed
artwork references. Keep the user's decorative-background enabled/disabled
preference distinct from the theme definition. The preview and shell should
render that same definition rather than independently recreating its appearance.
Define the difference between patching a palette and applying a whole theme;
complete application must not accidentally inherit a previous theme's artwork.
Preserve existing full-CSS skins and avoid repeatedly appending generated CSS.

Use the same preview/apply/revert actions from UI controls, agents and Canvas
App requests. Verify light/dark/system modes, background on/off, failed or
unavailable assets, preview cancellation, refresh, and unchanged conversations
and unsent drafts. An agent-created theme chooser remains a demonstration of
these capabilities, not a bundled app that must ship with the product.

## Then extend the component contracts

1. **Define named contribution points and a discoverable catalog.** Start with
   app and conversation header actions, status widgets, canvas toolbar actions,
   and Settings sections. Specify cardinality, ordering, responsive placement,
   supported inputs/actions and accessible labels. Agents must be able to query
   the installed host's actual catalog and schemas rather than infer support
   from examples or prose.
2. **Move the built-ins onto those same contracts.** Keep one shared host action
   implementation for each operation. Extract the fixed canvas container into
   a replaceable presentation adapter over host-owned view identity, selection,
   resource binding and dirty-navigation guards. Preserve mounted views when
   changing their placement. Coordinate extraction with responsive sidebar and
   Settings work instead of doing competing rewrites.
3. **Add conversation contracts deliberately.** Separate message renderers,
   execution renderers, conversation header, composer attachments/actions, then
   replacement composers and the conversation pane. Expose bounded, revisioned
   projections and shared send/cancel/edit/fork/upload actions. Keep canonical
   history, streaming, delivery receipts, drafts and runtime ownership in the
   host. A replacement composer must preserve attachment and input-method state
   and submit exactly once through the existing admission path.
4. **Add layout profiles and coherent presentation packages.** A package may
   contribute modules, compatible slots, a layout and a theme. Validate the
   complete composition before activation, with declared compatibility and
   fallbacks. Extend the existing preview/revert protocol across those changes;
   do not add a second persistence or activation system. Keep per-client preview
   targeting and explicitly define the scope of committed theme preferences.
5. **Provide isolated execution before accepting untrusted shell modules.** The
   current trusted-native profile remains explicit. Sandboxed Canvas Apps are
   useful prior art, but they do not automatically isolate shell modules.

For every new contract, require UI/agent parity, stale-revision handling,
idempotent mutation receipts, dirty-state handling, unload cleanup, accessible
fallbacks, and keyboard/mobile acceptance. Keep a host-owned recovery path so a
broken replacement can be removed without loading it again. Schema changes need
an explicit state-migration policy; arbitrary React local state is not durable.

Preserve economical agent observation: scoped subscriptions and compact revision
or dirty notices, followed by explicit reads when needed. Large content and
images should not be pushed into every request, and edits alone should not
start model turns. Reuse the existing surface observation contract where it fits.

## Authoring and canonical sources

The authoritative skill remains in `skills/amplifier-shell/`, loaded by
`behaviors/unified-shell.yaml` and the Microsoft skills behavior. Update the
SDK, action discovery, skill, examples and validation fixtures together as each
contract actually lands. Do not teach proposed slots as available capabilities.

Current Unified, Work, loop-live, tool-exec and optional TUI sources are under
`microsoft/*`. Runtime manifests, built-in behaviors, feedback and update defaults
must continue to use those destinations. Historical evidence links and legacy
feedback receipt recognition can retain `bkrabach/*`; saved session snapshots
must not be rewritten merely to normalize repository names.
