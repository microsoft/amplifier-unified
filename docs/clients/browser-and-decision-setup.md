# Browser tasks and optional decision services

Work supplies browser tasks using the chat model you already use. A connection
to your browser can run without a decision service or a native companion app.
Installing a support package never enables a decision model, sends observations,
or grants control of a device.

## Connect your browser

Open **Browser & computer → Connect a browser or device**. Install the extension,
connect it to this service, and choose the tab you want to use. Enable tasks
across pages if the task requires navigation, then select that browser in your
chat. Your ordinary chat model remains the default when no decision service is
configured. Stop in either the extension or the chat controls to pause access.

The current extension is an unpacked development extension for Chrome and Edge.
The service's HTTPS certificate must be trusted for background connections;
continuing through a certificate warning is not sufficient. The extension
controls web pages, not native applications on the device.

## Add an Amplifier browser

The optional **Amplifier browser** support installs Playwright and its compatible
Chromium binary into this service's own browser cache. Installation uses the
normal application updater, waits for idle before replacing the app, and
preserves existing optional features. It installs no operating-system packages.
If the host is missing browser system libraries, setup reports that limitation;
the service owner can provision them or you can connect your existing browser.

Setup verifies a headless launch and closes an empty page before reporting
success. It opens no websites and uses no saved browsing profile. The browser
is available only when both the support package and its executable remain
present. A missing binary can be downloaded again through the same setup action.
The live view lets you sign in and take over. It does not supply the managed-device
identity that some workplace sign-ins require.

## Add decision support when wanted

Choose an optional support package for OpenAI, Microsoft Decision-1, Jev, or a
local decision service. The package adds its setup metadata to the service and
its HTTP adapter to new conversation-component declarations. Use **Updates** to
prepare the conversation components after adding a package to an existing
installation. Existing qualified worker environments remain unchanged until
that normal update is verified and activated.

Then choose a saved connection in **AI connections & decision models**, select
the decision model for browser or computer tasks, and approve the observation
transfer described there. OpenAI and Azure reuse their existing account
configuration through a reference to the same saved account. Jev uses a TypeSafe connection. A local service needs its address
and access token; this package does not install model weights or a model server.
Start a new chat for a changed decision configuration.

Decision-only connections keep the same saved account setup. Their support
packages provide hooks for the existing Core runtime; users do not need a new
Core lifecycle feature or a second credential store. Component updates must
include those hook entrypoints before a new conversation can use them. An optional
account that fails preparation, or reports unavailability after complete cleanup,
can use the configured ordinary fallback. Missing lifecycle confirmation,
cancellation, or uncertain cleanup stops initialization rather than leaving a
rejected backend available.

An explicitly configured decision model is preferred. Ordinary selection remains
available as an override and as the configured fallback. Package presence and
saved keys alone do not activate decision inference.

### Shared settings and the CLI

Decision service connections and backend options belong to
`decision_models.connections`, beside the existing `decision_models.selection`.
They are not LLM provider declarations. The CLI continues to read only ordinary
`config.providers`; adding a decision-only connection does not change its model
provider list. The Settings connection inventory still manages both kinds.

A decision-only entry keeps its original connection declaration, including its
ID, module, source, credential references, enabled state and opaque options. A
shared chat account keeps its credentials and ordinary options in
`config.providers`; its decision entry contains a `provider` reference using that
settings identity and only `config.decisions`. Runtime assembly reconstructs the
same logical account before the existing credential/source/hook binding. No new
credential store, backend ID, model choice or observation consent is created.

Settings identities use `id`, otherwise the full module name, matching the
shared loader. Public/runtime identities still use `id`, then `instance_id`,
then the short provider name. `decision_models.connection_order` preserves
pre-migration declaration order where equal-priority account sources depend on
it; it does not select a new preferred chat provider.

Startup and explicit Settings writes migrate recognized installed-adapter
declarations in their existing global/project/local owner. Reads never migrate
or import adapters. Scope fragments stay in their scope: mappings overlay in
global → project → local → session order, with the same ordinary provider
identity merge and disabled tombstones. Existing session files are not swept;
new session-scoped writes use the dedicated owner. Existing saved runtime plans
are not rewritten or rebound.

Each settings file uses the shared lock and atomic replacement. Repeating a
successful migration is a no-op, and interruption can leave earlier files
committed and later files unchanged; composition accepts that known intermediate
form. This is not a multi-file transaction. Unknown or unavailable adapter
metadata leaves declarations untouched. Duplicate identities, conflicting old
and new declarations, malformed options, read-only files, and scope changes
between chat-capable and decision-only roles require review rather than guessing.
Preflight refuses an ambiguous known scope lineage before its first write;
startup reports an incomplete migration without blocking unrelated chat setup.
Until that migration is repaired, retained legacy rows can still affect the CLI.
Removing a connection disables it in the chosen scope; it does not erase shared
credentials or silently bind an existing selected backend to another account.

## Deployment ownership

The base Unified package and worker manifest declare the lightweight UI-use
library. Neither requires Playwright, a decision SDK, or a native desktop SDK.
The Work behavior supplies the tools; adding a package does not replace the
chosen bundle, orchestrator, context manager, or model.

Deployment owners can install the corresponding Unified extras:
`managed-browser`, `decisions-openai`, `decisions-azure`, `decisions-jev`, and
`decisions-local`. These are retained by application updates. The updater's
managed-browser preparation uses `<application data directory>/browsers`; an
owner-provided `AMPLIFIER_UI_BROWSER` executable takes precedence at runtime.
Qualification records that explicit choice and the launch flags, and checks
that they still match before activating a staged application. The download
remains in the owned cache; it does not overwrite the configured executable.
Manual installation of the Python browser extra alone does not download Chromium.

Desktop execution remains an optional canonical computer-use behavior. Its
target may be the service host. A browser extension or chat-only companion pairing
does not grant native desktop access or change that target.
