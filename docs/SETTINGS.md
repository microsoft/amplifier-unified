# Settings experience

Settings now has a persistent sidebar with 24 destinations in 11 sections. Appearance, Voice, Notifications and Updates are first-class sections. The navigation is a new presentation of the existing forms and public actions. No configuration migration is required.

## Completeness contract

The [inventory](settings-inventory.csv) tracks all 321 requirements from the settings audit plus six conversation diagnosis/recovery requirements and the Work summary detail preference added to main during implementation (328 requirements across 25 feature areas). Each row has an explicit destination, ownership boundary and validation evidence for its area. Area validation does not imply that every listed backend outcome was separately induced in a browser. The source review verifies that the original controls and handlers remain; automated tests check their contracts and affected workflows.

| Area | Destination | Native implementation |
| --- | --- | --- |
| Appearance (separate top-bar panel) | appearance | main.jsx |
| Setup > Conversation defaults > Voice | voice | settings-personal.jsx |
| Maintenance > Notifications | notifications | maintenance.jsx |
| Setup > Model providers | providers | setup.jsx |
| Setup > Model routing | routing | routing-settings.jsx |
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

## Collections and ordering

Models, bundles, and Smart Tools use a compact collection with a focused detail panel. The full row, including its chevron and padding, is clickable; a checkbox remains a separate action. Narrow screens use a list/detail back button. Provider access is disclosed below the model and provider options. Nonsecret provider drafts survive switching selections, while unsaved private keys remain only in component memory.

Routing profiles display all task roles in a searchable collection, with only one role and candidate editor open. Provider family names and named connections remain distinct; catalog suggestions never restrict explicit model IDs or patterns. Unknown role, candidate, and profile fields survive ordinary edits and are available through JSON. Saving still affects future preparation, not a running session.

Provider priority, routing candidate preference, and bundle composition remain separate orders. Dedicated order screens support pointer/touch dragging, arrow keys, explicit up/down buttons, and a position selector. The lifted row follows the pointer, surrounding rows move, and a line marks the landing gap. Escape, pointer cancellation, or an outside drop restores the draft order. Dropping changes the draft; Save order commits. Reduced-motion preferences disable movement animation. Provider and bundle order writes validate the complete original sequence to reject stale concurrent edits. Only enabled app capabilities enter composition order; disabled entries and standalone registrations remain intact.

## Model catalogs

Private catalogs persist across app restarts and are fresh for 24 hours. Configuration identity includes workspace, provider, source, endpoint, credential environment and credential-file content. Host-owned provider priority is excluded. Other provider options remain part of the key conservatively because arbitrary provider modules can interpret them. An expired catalog is shown immediately while an independent refresh runs; a failed refresh retains its last good values, with a retry cooldown. New configurations do not borrow a previous identity's models. Manual model/pattern entry remains available during discovery. Cache files are atomic, mode 0600, bounded in size, and contain digests rather than raw configuration keys. Tests still probe the saved provider independently of cached model discovery.

## Catalog batches

All matching entries are shown up to 50; larger results use pages of 40. Filtering searches the whole catalog. Selection persists across filters/pages, with page/all-matching/clear controls. Per-package extras are retained for the batch. `smartTools.installBatch` is the shared UI/agent action: it snapshots catalog sources, installs each package, persists individual progress, and retains partial successes. `retryOperationId` retries only unfinished items from that receipt. Restart interrupts work without replay. Installing remains separate from configuring and connecting an MCP server.

`providers.reorder` and `bundles.reorder` accept `ids` and `expectedIds`; the existing single-item move actions remain compatible. Ordering and catalog draft selections use the existing nonsecret editor view state.

The additional `test:settings-collections-browser` gate covers 13 routing roles, unknown fields, provider refresh during editing, drag insertion/preview/cancel/drop/save, private drafts across selection, independent checkboxes, composition exclusions, 51 catalog entries, filtering, cross-page selection, partial installation and retry, and narrow layouts. Its temporary fixture uses an OS-assigned port and performs no external package or account calls.
