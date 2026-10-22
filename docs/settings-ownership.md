# Settings ownership

Global Settings contains connection preferences, app appearance, workspace defaults
and starters, voice defaults, notifications, global file permissions, host screen
setup, diagnostic capture, saved-work import/cleanup, app/component updates,
installed bundles and registries, tool connections, runtime readiness policy,
backup/repair, reset, and terminal/app installation help.

Controls for an existing conversation are in Chat:

The model chooser retains an explicit next-message choice ahead of discovered
defaults. A fresh ready report may update those defaults after the chooser has
already loaded, but only for the current worker and the settings revision from
which it prepared. Older reports cannot overwrite a newer settings catalog;
in-flight catalog reads recheck the current report before publishing. Background
preparation follows the same rule without changing the last turn's outcome.

| Controls | Destination |
| --- | --- |
| Name, identity, workspace, saved history | Chat info |
| Providers, tools, loaded modules, save/share bundle | Advanced → Model & tools |
| Context/recovery and recorded errors | Advanced → Context & recovery |
| Screen source, consent, worker computer tools | Advanced → Computer access |
| Memory and past-work preferences | Advanced → Memory & past work |
| This chat's write overrides, return to workspace defaults | Advanced → Files this chat can edit |
| Shared project/local write overrides | Advanced → Project file access |
| Transcript export and sharing | Share or save |
| Current outputs and publishing | Canvas → Chat overview |
| Plan | Chat overview, with optional inline/bottom placement |

Old global deep links route to those destinations. They no longer appear as empty
redirect pages. Global host screen setup excludes the selected conversation's
screen source and mounted worker tools. The Updates page's older-source inventory
is host-wide update evidence across saved conversations, not a current-chat editor.

Advanced configuration editors retain explicit scope selection where a shared
setting can be overridden by project/local configuration. Those are labelled scope
choices, not controls that act silently on the selected chat. Global file access
and project file access have separate surfaces and drafts.

Settings in Canvas uses the same native settings components and typed actions as
the standalone dialog. Agents inspect `settingsUI` for routes and `visibleUI` for
rendered controls; private credential fields are not shared in either. Assisted
setup is optional once a chat model is configured. The user chooses demonstrations;
there is no automatic paid image, voice, or external action in the tour.

## Saving preferences

Independent switches, buttons and selections save through their owning typed action
on interaction. A save receipt and any asynchronous operation completion determine
success; a local selection is not proof of persistence. Show pending/error state,
retain recoverable text on failure, and never include unrelated drafts in a write.
Numeric text fields validate and save on blur or Enter. Secret fields stay local.
An explicit save is appropriate for text/code editing or values that must be
validated and committed together; explain that boundary in the UI.

AI connections distinguish loading, failed loading, a confirmed empty catalog, and
configured connections. Only a confirmed empty catalog may show initial setup.
The image generation preference belongs in Advanced. Without an explicit override,
the runtime checks installed capabilities and uses the first eligible provider by
priority; with no eligible provider it exposes no image generation. Reading these
preferences must never generate an image.

### Save-control audit (2026-10-13)

| Surface | Decision and reason |
| --- | --- |
| AI connections | Existing default-model selection saves immediately. First connection setup keeps a completion step for model, scope and optional initial routing. Credentials and endpoint save together. |
| Advanced provider configuration | Keep Save provider: identity/module, credentials, endpoint, scope and JSON describe one validated connection. Preference order commits one complete permutation. |
| Advanced image generation | Provider choice, off/automatic, model and scope save immediately. A rejected save offers Retry; no extra successful-save step. |
| Model rules | Keep Save profile and candidate editor completion: provider/model candidates, role matrix, profile identity and scope must stay coherent. |
| Bundles and registries | Enable controls already apply directly. Keep composition-order save (later entries override earlier settings), module/source configuration validation, and named custom bundle creation. |
| Conversation defaults | Registered bundle selections save immediately. Explicit save remains for manually entered source URLs/paths. Scope selection chooses the editor's target. |
| Smart Tools | Enable/selection controls already act directly. Keep connection setup for command, arguments, environment, URL and authentication; installation is an explicit operation. |
| Appearance | Everyday theme, scheme, layout, detail and background choices already save immediately. Keep named library save and preview/apply for custom CSS editing. Import/export are operations, not preference saves. |
| Workspaces | Path visibility already saves immediately. Keep text/path default save and starter/resource editors: paths, source, setup commands and ownership records need a deliberate completed value. |
| Voice | Model, voice and dictation choices already save directly. Keep voice connection save for source/key replacement; do not submit a partially entered credential. |
| Notifications | Badge, desktop, preview and enabling/disabling an existing phone connection save immediately as single-field patches. Keep phone connection save for server/topic/token and first-time enablement. |
| Diagnostic capture | Capture/recording/stream switches save immediately; numeric limits save on valid blur. Host merges narrow patches so an unfinished destination cannot be activated. |
| Diagnostic destinations | Keep Save destination: URL, authentication reference, routing streams, workspace filter and enabled state form one outbound policy. Test is a separate explicit network operation. |
| Ready conversations | Count/lifetime save on valid blur or Enter. Opening history does not prepare a runtime. Each write changes only that field. |
| File permissions | Keep Save permissions for coordinated allowed/denied paths and scope; deny precedence must be validated on the complete policy. |
| Updates | Preferences already apply on change; checking, installing and rollback are explicit operations. |
| Saved work, backup, repair and reset | Import/export/backup saves are operations. Keep reviewed destructive-action confirmation; these are not preference controls. |
| Adjacent chat controls | Mode selection now applies immediately while idle. Keep explicit edits for conversation name, goal/task text, coordinated budgets/context limits, memory notes, publishing targets, schedule creation, bundle switching and output snapshots. These affect existing work or create records rather than changing a standalone global preference. |
| Extension settings | Extensions own their UI/actions and must follow the same rule; the host cannot safely auto-submit arbitrary extension forms. |

The audit covers native settings destinations and the adjacent editors linked from
them. Existing text editors retain explicit saves rather than guessing when an
unfinished URL, credential, policy or code block is ready to commit.
