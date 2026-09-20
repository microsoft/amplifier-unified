# Settings experience

Settings now has a persistent sidebar with 24 destinations in 11 sections. Appearance, Voice, Notifications and Updates are first-class sections. The navigation is a new presentation of the existing forms and public actions. No configuration migration is required.

## Completeness contract

The [inventory](settings-inventory.csv) tracks all 321 requirements from the settings audit plus six conversation diagnosis/recovery requirements added to main during implementation (327 requirements across 25 feature areas). Each row has an explicit destination, ownership boundary and validation evidence for its area. Area validation does not imply that every listed backend outcome was separately induced in a browser. The source review verifies that the original controls and handlers remain; automated tests check their contracts and affected workflows.

| Area | Destination | Native implementation |
| --- | --- | --- |
| Appearance (separate top-bar panel) | appearance | main.jsx |
| Setup > Conversation defaults > Voice | voice | settings-personal.jsx |
| Maintenance > Notifications | notifications | maintenance.jsx |
| Setup > Model providers | providers | setup.jsx |
| Setup > Model routing | routing | setup.jsx |
| Capabilities > App bundles | app-bundles | bundles.jsx |
| Capabilities > Add capabilities | add-bundles | bundles.jsx |
| Setup > Conversation defaults > Bundle | defaults | bundle-controls.jsx |
| Setup / Capabilities > Loaded session modules | loaded-modules | bundles.jsx |
| Capabilities > Save and share | share-bundle | bundles.jsx |
| Capabilities > Smart Tools | smart-tools | smart-tools.jsx |
| Maintenance > Updates | updates | updates.jsx |
| Maintenance > Diagnostics & Context Intelligence | diagnostics | diagnostics.jsx |
| Maintenance > Conversation history | history | maintenance.jsx |
| Setup > Current conversation | conversation | conversation-controls.jsx; conversation-export.jsx |
| Maintenance > Backup and repair | repair | maintenance.jsx |
| Maintenance > Advanced recovery | reset | maintenance.jsx |
| Maintenance > Ready conversations | ready-conversations | worker-retention.jsx |
| Capabilities > Advanced module & source registries | registries | registry.jsx |
| Maintenance > File access | permissions | maintenance.jsx |
| Maintenance > Terminal and automation | automation | maintenance.jsx |
| Setup > Install Amplifier | install-app | settings-personal.jsx |
| Shared settings controls | shared | settings-ui.jsx; list-filter.jsx; attention.jsx |
| Adjacent: conversation bundle popup | conversation | bundle-controls.jsx |
| Adjacent: Session controls | runtime | runtime-settings.jsx |

## Boundaries and state

- Unified-specific screens depend on integrations the app explicitly consumes. Loaded bundle modules are inspected and configured generically; their names never become hard-coded navigation entries. This follows [Amplifier repository rules](https://github.com/microsoft/amplifier/blob/main/docs/REPOSITORY_RULES.md).
- Diagnostics configures the app’s Context Intelligence client. Optional bundle hooks remain generic module configuration. File access edits the existing scoped write overrides through Unified’s shared policy API. The editor does not claim to display every effective restriction inherited from settings or bundles, and navigation does not add module-specific assumptions.
- `settingsSection` and `settingsExpanded` stay the public navigation contract. Old saved pages and agent actions resolve into the new structure. `panel: appearance` remains supported.
- Visited editors remain mounted until the dialog closes. Ordinary drafts use the existing shared view state; private provider keys and notification credentials stay only in component memory until explicitly saved. Closing a dialog never saves a draft.
- The shared activity and outside-dismissal contracts remain in effect. Pending receipts are distinct from completed background work, and navigation remains usable during independent operations.
- Existing skin tokens control color and typography. Structural layout supports mobile widths down to 320 pixels, independently scrolling navigation/content, and keyboard access.

## Release validation

- Full Python suite; frontend unit suite; reproducible production build.
- `test:settings-browser`: real isolated backend, provider reordering/model/routing updates, module apply/toggle, unread notices, source filtering, registered bundles, path selection, file drops and mobile layout.
- `test:settings-completeness-browser`: all 24 destinations at 320/390/736/1280 pixels in light/dark (192 layouts), notification validation/private credential persistence, unsaved secret drafts across navigation, voice and appearance persistence, catalog → inspect → install → connection workflow. Catalog/package execution uses deterministic fixture methods; operation lifecycle and connection storage are real. No third-party package is downloaded or executed by that fixture.
- Existing bundle, Smart Tools MCP Apps, export, history, readiness, delayed-navigation, multi-client, bootstrap, activity and library performance browser suites remain release evidence for their native behavior.
- The normal release workflow additionally checks empty storage, pending send, source cache reporting, ownership, live history, conversation drafts and canvas preservation. Settings suites are added to that workflow so subsequent releases cannot omit this coverage.

## Validation boundary

Browser acceptance runs against isolated app instances with synthetic configuration and temporary storage. Access to the user’s already-running personally configured app was blocked by browser policy verification during the design audit, so this release does not claim live personal-configuration acceptance. The prior sanitized design fixture and inventory informed completeness; no simulator or personal configuration is shipped.
