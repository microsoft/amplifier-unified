# Everyday Settings test build

This build starts from main c792930887597f23b2835cb6226ff097029a54eb (v0.20.13), including the ordered updater. It changes presentation and adds a guided AI connection flow; existing provider, bundle, module, permission, notification and routing configuration is not migrated.

## Navigation and retained capabilities

The everyday index contains AI connections, Smart Tools, Appearance, Voice, Notifications, Privacy & files, and Updates. Advanced is a separate destination at the bottom, not a global mode switch. All 29 prior destination IDs remain valid; five new IDs provide the new homes. Contributed settings sections are listed generically in Advanced.

| Existing destination | Current home and retained behavior |
| --- | --- |
| overview | AI connections; provider status and next steps, with full configuration links |
| providers, routing | Advanced / AI configuration; all fields, private keys, schema forms, model catalogs, custom profiles, ordered fallback lists, arbitrary roles and JSON remain |
| smart-tools | Everyday Browse / Installed, selection across filters/pages, review before batch installation, per-item results and retry; installed packages remain distinct from connected tools |
| appearance | Appearance gallery, previews, apply/reset, color mode, background, layout and work summary; CSS/import/export now in Advanced / Custom appearance |
| voice | Voice / Advanced voice options; existing provider/session controls retained |
| notifications | Desktop permission and notification preference, with existing server/topic/token/push/preview controls under Send notifications to another device |
| workspaces, permissions, desktop | Privacy & files; workspace location/path display, effective folder policies, screen/browser permission and readiness; technical environment details collapsed |
| recall | Privacy & files / Memory & past work; independent save/use choices, exclusions, memory CRUD/provenance, search, consolidation and limits retained |
| diagnostics, history | Privacy & files; app capture/forwarding, history import/export and cleanup retained; technical import override collapsed |
| updates | Overall status, check/install controls, automatic installation, details, release notices, older conversations and changelog |
| app-bundles, add-bundles, defaults, loaded-modules, share-bundle, registries | Advanced / Bundles & modules; all existing editors and generic discovery contracts |
| conversation, outputs, publishing | Advanced / Conversation & work compatibility destinations; existing context actions remain |
| ready-conversations, runtime, repair, reset, automation, install-app | Advanced / Performance & support; all existing controls |

The original field inventory is in `docs/settings-inventory.csv`. Its validation entries describe the previous implementation; current evidence is recorded below. New destinations are `ai-connections`, `privacy`, `advanced`, `tool-connections`, and `custom-appearance`.

## Guided AI setup

Select a supported service, save its key or start the existing ChatGPT sign-in, then select a cached model. Private key drafts stay in component memory and are cleared on save/workspace change. They are excluded from shared view/history state. Operation receipts are correlated to authoritative action state so stale completions cannot advance the wizard. Failed submissions retain choices; duplicate submissions are blocked.

`providers.finishSetup` is a shared user/agent action. It preserves opaque fields and credential references while setting a default model. It initializes general/fast routing only for the first enabled connection when no explicit/custom profile exists. Editing an existing setup never replaces custom rules. This does not test a billable model turn or invent authentication support for a provider.

## Updates

`updates.check` remains read-only. One `updates.install` request completes the existing app → included components → other sources sequence, across idle waits and verified app restarts. The automatic-install preference uses that same sequence. The overall status never calls the whole installation current based only on the app version. Required-component errors, rejected/uncertain restarts and incomplete replacement retain actionable state. Details open when attention is needed; preferences and source inventory otherwise stay collapsed. Release notices, older-conversation settings and changelog are retained below.

No updater orchestration was forked for this UI. See `ordered-updates.md` for the underlying implementation and integration evidence. Dev instances have automatic checks/install disabled to preserve their owned checkout; this test build does not prove a live release replacement.

## Validation

- Frontend build and unit suite.
- Python setup, shared actions, provider catalog/environment/publication, management, ordered sequence and idle-update tests.
- Existing settings collections browser suite: routing roundtrip with unknown fields, insertion-line drag/cancel/save, private drafts, bundle composition, catalog pagination and partial-batch retry.
- Mobile browser suite: Back/Forward and reload, nested editors, touch drag, scrolling, form/footer/soft-keyboard handling, all 34 destinations across five widths.
- Everyday browser suite: guided provider save/model completion, private draft retention, existing routing preservation, review-before-install, partial batch retry, installed-versus-ready messaging, collapsed notifications, all 34 destinations across four widths.

Browser scenarios use the real built app and shared action service with disposable synthetic provider/catalog/runtime fixtures; they make no external account calls. Spark-2 live validation and exact commit/URL/isolation evidence are kept with the task handoff, separately from these fixture results.
