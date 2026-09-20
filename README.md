# Amplifier Unified

A local Python host serving a bundled React interface. One conversation supports typed chat, notification-oriented text, and real-time voice. Community bundles and tools execute through AmplifierSession, Foundation and loop-live in isolated worker processes.

## Run this checkout

```sh
uv run --project /path/to/amplifier-web amplifier-unified --workspace /path/to/your/project
```

Then open http://127.0.0.1:8941 and sign in with the system account that runs the host. Start a conversation, use the `work` default or choose another standalone bundle, and send a message. The runtime prepares its branch-tracking environment on first use. Work currently requires GitHub access to its private bundle repository; Anchors remains available in the bundle picker. Explicit app, workspace, and shared bundle choices still take precedence, and existing conversations keep their saved bundle. Unified reads the shared Amplifier settings and credentials on every session activation, including workspace overrides. Its runtime registry/cache remains app-owned. Missing credentials or unavailable providers are reported as errors, never simulated responses.

The first message may take several minutes while the runtime environment and configured modules are prepared. The conversation shows the current preparation phase and elapsed time; your message remains queued until preparation finishes. A preparation timeout reports an error instead of silently resending it.

Install from a local checkout:

```sh
uv tool install /path/to/amplifier-web
amplifier-unified
```

Install the private release (GitHub repository access is required):

```sh
gh auth login
gh auth setup-git
uv tool install git+https://github.com/bkrabach/amplifier-unified
amplifier-unified
```

End users need no Node installation: compiled React assets ship in the Python package. **Settings → Updates** checks community sources and this private release channel. Automatic checking is on daily while the host is open; automatic installation is opt-in. App releases restart the host when idle. See [the update design](docs/UPDATES.md).

## Smart Tools and collaborative canvas apps

**Settings → Smart Tools** browses the community catalog, inspects Git sources, installs Python tools into isolated environments, and connects standard MCP stdio servers. Tools advertising MCP Apps can open a durable canvas tab. Users and agents call the same tool APIs through the shared action surface; there are no tool-specific dependencies in the host. See [supported capabilities and setup](docs/SMART-TOOLS.md).

## Shell customization and agent guidance

The [Unified shell behavior](behaviors/unified-shell.yaml) provides the
[amplifier-shell skill](skills/amplifier-shell/SKILL.md) through Microsoft's
skills tool. It covers layouts, themes, navigation modules, and artifact viewers.
See [installation and behavior composition](docs/shell/behavior.md) and the
[shell SDK guide](docs/shell/README.md).

## Development

```sh
cd frontend
npm install
npm run build
cd ..
uv sync --group dev
uv run pytest
uv build
```

For authentication changes, also run the real Chromium login smoke:
`uv run --with playwright pytest tests/test_browser_auth.py`
(install Chromium once with `uv run --with playwright playwright install chromium`).
It checks external-link navigation and the browser's actual form `Origin`;
handwritten HTTP headers alone miss these failures. PAM is stubbed only in the
isolated test, which never uses real credentials or changes system certificate trust.

The runtime dependencies follow branches declared under `amplifier_web/runtime_deps/`. The launcher prepares them through uv in a writable user cache. The outer host remains small and independent of provider import dependencies.

## Deployment, PAM, and HTTPS

The host has native, independent authentication. It does not trust a reverse
proxy, shared cookies, or another application's session. Every browser,
including `localhost`, must authenticate using PAM's `login` service with the
exact operating-system user that owns the host process. Browser sessions use an
app-specific, signed, expiring `amplifier_unified_session` cookie; automation
uses the separately generated control bearer in the private app data directory.

By default Unified only listens at `127.0.0.1:8941`. To publish it on a LAN or
tailnet, configure exact HTTPS origins and create the app-owned local CA:

```sh
amplifier-unified config set public_origins '["https://host.example:8941", "https://192.0.2.5:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["192.168.1.5", "127.0.0.1"]'
amplifier-unified service install
```

`config/server.yaml` is private (`0600`) under the selected data directory,
normally `~/.amplifier-unified`. Non-loopback binds fail to start unless TLS and
at least one exact HTTPS public origin are configured. `setup-tls` creates a
private CA and leaf key under `config/tls`, and later runs reuse that leaf.
Run `setup-tls force` after changing an address that must appear in the
certificate; this keeps the existing CA and rotates only the leaf.

**First client, before opening the browser:** HSTS can block the HTTPS setup
page until its CA is trusted. Use a verified SSH connection to the account that
runs Unified, or a trusted local console. On that host:

```sh
amplifier-unified setup-tls export > amplifier-unified-ca.crt
amplifier-unified doctor
```

Use the same `--data-dir` before `setup-tls`/`doctor` if the service uses a
non-default data directory. Export prints only the existing public CA and never
creates, rotates, or exports a private key. From the client, transfer that file
over already-verified SSH (replace the placeholders, including the absolute
path where you exported it):

```sh
scp '<owner>@<host>:/absolute/path/amplifier-unified-ca.crt' .
openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256
```

Compare the fingerprint with the trusted-host `doctor` result before importing.
On macOS, use Keychain Access's **login** keychain and trust for SSL; on Windows,
use **Manage user certificates → Trusted Root Certification Authorities**.
No administrator trust store is necessary for normal per-user setup. For iOS,
install the verified profile and enable **Certificate Trust Settings**; on
Android, install it explicitly as a **CA certificate**. Use approved management
on managed devices. Restart the browser afterward.

Once TLS is trusted, `/setup` contains the platform-specific instructions and
an existing-CA download link. Anonymous downloads and their displayed fingerprint
are not independent proof of authenticity. Do not disable HSTS or certificate
checks, or enter login credentials through an untrusted TLS connection.
The only public bootstrap paths remain `/login`, `/setup`, `/api/ca`, `/ca.crt`,
and `/api/health`.

Use `amplifier-unified doctor` to check PAM, TLS, and deployment settings.
Linux systemd user-service lifecycle is available through
`amplifier-unified service install|start|stop|restart|status|logs|uninstall`.
The generated service has no credential values and reads its private
configuration at runtime. Replacing an existing generated unit requires
`amplifier-unified service install --replace`; its prior contents are saved as
a private timestamped backup. Installation captures the invoking shell's `PATH`
so the runtime can find `uv` in Snap or custom locations, and starts/restarts
the service to apply it. After changing tool locations, rerun installation from
the shell where `uv --version` works (keep your `--workspace` selection).
`uv tool install git+https://github.com/bkrabach/amplifier-unified`
installs the same CLI and service support.

## Standalone host and session flow

Provider environment references remain in saved settings unchanged. At runtime,
an unset exact `${NAME}` resolves to an empty string only for a provider-declared
optional nonsecret field. Unknown fields/schemas and embedded references remain
strict. An explicitly referenced required secret must be present and nonempty;
another ambient provider credential cannot substitute for it. The same rule
applies to root/agent providers and model-list/connection tests.

No `amplifier-app-cli`, `amplifier-loop-live-cli`, or `amplifier-workspace` host libraries are installed or imported. The application owns configuration, approvals, native history saves and child-session lifecycle, using Foundation's public bundle preparation and session APIs.

```text
React chat / text / Live voice / Realtime fallback
    → shared app actions and identified input
    → main AmplifierSession + loop-live
    → configured providers, tools, delegated AmplifierSession workers
    → identified completed manager turns and worker reports
    → one conversation, notifications and voice narration
```

Live and Realtime request reasoning and app operations through `amplifier_delegate`. Only the Amplifier session executes tools, including `app_control`. Live retains its own audio transport and conversational speech generation; the session owns delegated work. Ending audio leaves accepted work running. A manager response reports pending background workers separately from completed work.

Configuration is shared with amplifier-app-cli and future hosts: `~/.amplifier/settings.yaml`, `<workspace>/.amplifier/settings.yaml`, then `settings.local.yaml`, with native session `settings.yaml` as the most specific scope. `AMPLIFIER_HOME` relocates the shared root. Keys live in `~/.amplifier/keys.env` and custom routing matrices in `~/.amplifier/routing/`. Voice preferences use the common `voice` section. Unified's settings editors write these shared files under the CLI's per-file locks. Foundation keeps an app-owned runtime registry/cache; the server, browser state and Smart Tool connections remain app-owned. See [shared configuration and migration](docs/SHARED-CONFIGURATION.md) and [session storage](docs/STORAGE.md). Explicit OAuth token paths remain supported; newly created token files use the shared root.

Community bundles retain their providers, tools, hooks and agents. The supported streaming orchestrator is explicitly overlaid with loop-live and the original/adapted mount plans are recorded privately. Custom incompatible root orchestrators fail with an actionable error. The app currently includes a reviewed local loop-live change for delivered-input correlation and manager-turn completion; [the upstream contribution](docs/loop-live-upstream/README.md) is prepared but not published.

## Shared control and appearance

### Automatic CLI workspaces and chats

Unified uses Foundation's native `session.history` reader/writer and
`session.shared_state` ownership lock. CLI lock participation
requires the upgraded shared-root adapter; restart older CLI processes after
updating. Native Windows CLI persistence remains available, but shared-session
locking is supported only on POSIX local filesystems.

CLI projects appear automatically as workspaces in the sidebar, with their
top-level sessions listed as chats. Independent forks stay in the chat list;
subagent histories are available through **Session details → Subagent history**
on their parent conversation. Select a resumable root chat to continue it; there
is no import or sharing option to configure. The list refreshes every 15 seconds in the background,
and the refresh button beside the chat list checks immediately. Opening a
chat creates a browser view of the same root ID and, by default, prepares its
runtime in the background. It does not duplicate the transcript or submit input.
The view starts with the latest 100 visible messages; scrolling toward the top
loads older history, and **Load earlier messages** remains available.
Execution restores the complete
native transcript, including tool results and provider replay fields. Removing a workspace or chat from the
list keeps its shared files on disk and leaves it hidden from later discovery.
Worker sessions and legacy chats with missing folders, unsupported IDs, or unknown
bundles remain readable, with an explanation when continuation is unavailable.
Metadata is cached; unopened histories are indexed without reading transcripts
or starting a runtime. Navigation renders 100 chats at a time; search covers all
chats in the selected view. Agents use the same `history.refresh`, `session.select`,
and `session.history` actions as the interface.

The workspace explorer shows existing folders with top-level chats and the
ancestor folders needed to reach them. Click a workspace name to select its
chats; a chevron appears only when that folder contains deeper workspaces.
Browsing folders leaves the current conversation open. Search accepts full paths,
custom workspace names, and case-insensitive fnmatch patterns. The selected
workspace's full path remains visible, and unread activity rolls up through its
parent folders. There is no optional filter for empty or unrelated folders.

Switch between **Workspaces** and **All chats** above the explorer. All chats
combines top-level conversations from existing workspace folders and shows each
folder's full path. Both views place pinned chats first, then sort conversations
by recent activity. Opening, renaming, or pinning a chat does not count as new
conversation activity. Search matches chat titles, descriptions, workspace names,
and full paths, including fnmatch patterns such as `*/playground`.

Use a chat's pin button to keep it at the top in either view. Pins and the chosen
view survive restarts; they are app preferences and do not change the shared
transcript. **New chat** uses the currently selected workspace in either view.
Agents can use `session.pin {id, pinned}`, switch views with
`view.update {patch: {navChatScope: "workspace" | "all"}}`, and read
`/chatNavigation` for the same ordered, filtered page shown in the sidebar.

**New workspace** accepts an existing folder or a path to create, opens its first
chat, and makes the workspace immediately visible. Existing folders and chats
are reused. Creating a workspace does not start a runtime or model call; its
first chat initializes when you send a message or explicitly open model controls.
Agents have the same `workspace.create` and `workspace.select` actions. Their
state includes `/workspaceExplorer`; `view.update` accepts `navWorkspacePath`,
`navWorkspaceFilter`, `navWorkspacePage` (1-based), and
`navWorkspaceAncestorsOpen` for the same browsing controls.

CLI keeps the writer lock until exit. Web releases automatically after accepted
work, delegated jobs, approvals, and saving settle, even if the page stays open.
On the next action it acquires the lock and compares native transcript, metadata,
backup and configuration file stamps. Unchanged valid state reuses the mounted session; changed state
reloads. A busy owner rejects execution with diagnostics and retains the draft.
Takeover remains an explicit request to the current owner; background preparation
never requests it. No lock expiry, force-unlock, or automatic work replay is provided.

### Conversation Markdown export

**Settings → History & recovery → Current conversation** offers **Copy Markdown** and
**Download Markdown** for the entire conversation, including native history
outside the loaded page. The export preserves message Markdown and code,
labels spoken exchanges, and includes attachment and saved-artifact references.
It omits system/developer instructions, hidden reasoning, and tool payloads.
References identify host-owned files and artifacts; their contents are not
embedded. Existing **Export JSON** remains available.

Both controls use `session.export {id, format:"markdown", destination:"clipboard"|"download"}`.
Agents can use `destination:"none"` to create the same immutable snapshot and
read its returned `statePath` through `get_state`, following `nextOffset` for
long exports. Reusing a command ID returns the original snapshot. Browser
delivery is reported separately in `view.conversationExport`; a download-started
report does not prove the user saved the file. Export never resumes conversation
work or changes the native transcript.

Active work is labelled as in progress. Missing, rewritten, changing, or damaged
native history causes an error rather than a silently incomplete export. Older
native-only voice references without original message positions are labelled
as recovered and retained at their saved reference position; the export does
not invent their original chronology. Text intentionally included in a user's
visible messages is exported unchanged; this is not a public-feedback redaction
or sharing feature.

### Warm conversations and fast navigation

The browser displays a recently visited conversation from a bounded local cache
while its selection request completes. Uncached chats show their own loading
state. Drafts remain attached to their conversation, and dirty canvas edits still
complete their navigation guard first. Unchanged native history is not reread
when returning to a chat. The initial native-history read still scans its saved
transcript; this is not an indexed disk-paging implementation.

Selecting a resumable chat immediately schedules background preparation, with no
debounce. Preparation runs separately from display, does not submit a model turn,
and does not take over another app's lock. Already warm chats stay loaded without
reacquiring their writer lock just for navigation. On real input, the worker
reacquires ownership and validates saved history and configuration as usual.

By default, the host keeps up to **32 settled workers** for **12 idle hours**.
These are idle-retention limits, not a cap on running work. The oldest settled
workers retire first. A worker must confirm it is still parked, with no admitted
commands, approvals, bridges, naming work, pending children or configuration
transaction, before retirement. It resumes from saved state on the next command;
earlier inputs and tool effects are not replayed.

Use **Settings → Advanced → Readiness** to change the idle count,
hours, and preparation-on-selection policy. The shared
`runtime.retention.update {patch: {...}}` action saves and applies these settings
without a restart. They live under `runtime` in the host's `config/server.yaml`:

```yaml
runtime:
  max_warm_workers: 32
  idle_timeout_hours: 12
  prewarm_on_select: true
  max_background_starts: 2
```

Setting either idle limit to zero disables background preparation and retires
settled workers promptly. Active work stays protected. The idle clock starts
when a worker parks; merely browsing an already warm chat does not reset it.
`max_background_starts` bounds concurrent preparations and requires a host
restart to change. Edits through `amplifier-unified config set runtime.<name>`
are read on startup; use the shared action or settings page for live changes.
See [validation and memory findings](docs/validation/warm-conversations.md).

Both applications must run as the same user on the same POSIX host, use the
same canonical workspace, and share `AMPLIFIER_SESSION_STATE_HOME` (default:
`${XDG_STATE_HOME:-~/.local/state}/amplifier/sessions`). Close older CLI processes
before using shared sessions: an upgrade cannot retrofit their missing locks.
Generated user services pin the installing shell's resolved state root explicitly.
`amplifier-unified doctor` prints that location; use the same path in the CLI
and any third participant. For TUI integration, see Foundation's
[participant guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/SHARED_SESSION_STATE.md).
Metadata stamps are a local cache check, not proof against arbitrary external
file modification. This coordinates app-owned conversation writes, not
untracked external jobs started by arbitrary plugins.

Developer regression: test the actual parked worker **after another CLI write**,
not only a second unchanged web turn. Completion events must carry the producing
task's activation token; a persistent inbox loop's startup token expires on park.
Runtime admission also runs outside the HTTP app state lock, because its progress
callbacks need that lock.

`GET /api/state` exposes the browser's current session/application view. Library
lists are paged on the server, so a settings change does not download every saved
chat. Selected and active conversations retain their live details. The complete
catalog and device observations remain available to agents and API clients via
`GET /api/state/detail?path=/sessions&offset=0&limit=50`; follow `nextOffset` and
use the returned revision when reading subsequent pages. JSON Pointer indexes
refer to that complete catalog, not positions in the browser's filtered arrays.
Terminal integrations can request `/api/state?sessionId=<id>` to retain one
explicit conversation's live details while the browser browses another chat;
this read does not change the shared selection.

`GET /api/actions` lists action schemas. `POST /api/actions` accepts
`{action,args,id?,expectedRevision?}`; UI controls and the runtime's app-control
tool use the same handlers. `GET /api/events` streams the same bounded browser
view. Menus respond locally while their shared actions are confirmed; rejected
view changes roll back without discarding newer choices. State includes
`attention.items`, unread counts, and section/page destinations. `attention.read`
accepts item IDs to acknowledge review; it does not dismiss the underlying update
or issue. Changed facts become unread again, and acknowledgements survive
restarts. SQLite stores app settings, accepted command IDs and presentation
overrides. The native history catalog is rebuilt from community session files;
routine UI saves do not rewrite the entire discovered catalog. Conversation
transcripts and Context Intelligence events stay in those community files.
Interrupted work is marked rather than silently replayed.

For a reproducible large-library benchmark, see the
[performance fixture](tests/fixtures/library-performance.md). It measures real
HTTP actions, state size, SSE traffic and settings click latency using disposable
history, without starting model work or reading personal conversations.

Skins are complete self-contained CSS files. The Appearance panel imports, edits and exports skins. The supplied Amplifier Unified skin includes the Amplifier logo and blue/lilac surfaces. Device permission dialogs are still handled by the browser. Tool actions that specifically request human approval remain human approvals.

The server accepts only configured Hosts and exact public origins, rejects
cross-site requests, and does not use `X-Forwarded-*` headers. Remote hosting
requires native HTTPS and PAM as described above.

Provider setup detects standard environment variables (such as `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`) and allows a custom variable name. Availability checks expose only names and presence; Save stores an environment reference. The backend reads its launch environment and private key file. Restart it after changing variables in a terminal. Private pasted keys remain available as an alternative; ChatGPT uses account sign-in. Copilot supports one credential per session process because its SDK shares authentication.

## Everyday controls

Settings groups provider setup and routing, community bundles, effective module configuration, file permissions, history, notifications, updates and recovery. Module/source registries offer scoped advanced overrides. Per-conversation controls expose modes, goals, budgets, skills, tools and context reset. A custom bundle can be saved and used immediately or exported as a single portable Markdown file with credentials represented by environment references.

When the bundle enables Foundation’s `hooks-session-naming`, conversations receive a short generated title after the second completed user turn. The hook refreshes the searchable description every five turns while keeping the title stable. Manual names are preserved. Naming uses the bundle’s configured providers and fast-model routing, and its model usage appears in the originating turn.

Tools and delegated workers appear as collapsed lines inside their conversation turn. Work summaries retain a saved position where work began, including voice delegations without a visible input ID; completion and later messages do not move them. Expand them to inspect nested actions and model calls. Token usage and provider-reported or estimated costs roll up once through each parent and the whole turn; missing prices remain unavailable.

For automation, use the same host from the terminal:

```sh
amplifier-unified run "Summarize this project"
printf 'Summarize this input' | amplifier-unified run --output-format json
amplifier-unified continue "Continue the previous task"
amplifier-unified tool bash --args '{"command":"pwd"}'
amplifier-unified completion zsh
```

Connection options (`--port`, `--workspace`, `--data-dir`, repeatable
`--bind`, `--host`, repeatable `--public-origin`, `--tls-cert`, `--tls-key`,
and `--session-ttl`) precede the subcommand. One-shot commands attach the
private control bearer and trust the configured app CA; they first connect to
an existing configured origin with the matching data identity, or start a
temporary loopback-only host. Noninteractive tools cannot grant human approval
automatically.

See the [CLI parity audit](docs/CLI-PARITY.md) for implementation evidence and limits. Native notification replies, multi-device synchronization and STT/agent/TTS fallback remain follow-ups. Microphone/audio and real provider account login require a device/account trial. Custom root orchestrators incompatible with the live adapter produce an explicit error.

The command is named `amplifier-unified` to coexist with the already installed `amplifier-web` application. Its conversation database lives in `~/.amplifier-unified`.


### Conversation workspace

The chat shell fits the viewport; conversation history, navigation, and canvas
scroll independently. New chats center the composer. Voice calls, response
notifications, attachments, and the main conversation's model preference live
in the composer. Open the model control to choose a mounted provider/model and
its advertised reasoning effort. Pinning applies to the main session; worker
routing remains owned by the bundle. Returning to the bundle default preserves
session token limits.

Attach up to eight files (8 MB each) with the picker, drag and drop, or paste.
Images become native multimodal content for vision-capable providers. Small text
files are included as reference content with a total 100 KB inline limit; PDFs,
binary files, and larger text files are available through their local paths to
session tools. Attachments persist with conversation history in private app
storage. Removing a draft attachment detaches it without deleting historical
files.

The left rail expands on hover; its sidebar icon toggles whether it stays pinned. It explores workspace
folders and their conversations and can create new workspaces. Drag either pane divider to resize; chat can
shrink to 360 pixels. The canvas focus button fills the app frame without reloading
its content. Its compact header reveals viewer controls on hover or click, with
a pin to keep them open. The right canvas has an adjustable width and
previews interactive HTML, Babylon.js 3D scenes, Markdown, Mermaid, Graphviz, code, JSON/JSONL, images,
and declarative agent UI. The agent receives explicit canvas guidance on every
turn and can inspect render results and operate standard HTML controls. See
[canvas actions, viewer controls and the A2UI subset](docs/canvas.md). User canvas
interactions are visible to the agent; they do not automatically start a new turn.


Provider metadata and model catalogs load in the background at startup. Catalogs
are shared by the composer, provider settings, and routing selectors and cached
for the backend process. Provider configuration, credential/environment, and
source changes invalidate only the affected saved entries. Mounted sessions use
their actual provider configuration and reuse unchanged entries across remounts.
Refresh models retries just the selected provider. A provider-supplied list is
shown as a selector; manual model IDs are offered only when no list is available.
Saved model IDs remain visible even if absent from the current list.

### Message controls and saved artifacts

Each chat entry has **Copy as Markdown**. Completed assistant turns also offer
**Fork a new chat from here**. Edit your own message with the pencil, then choose
**Save & regenerate**: a new selected branch keeps the full model transcript up
to that input and submits the revised text with its original attachments. The
original chat stays available. Later messages are excluded from the new branch;
files and external actions already performed by tools are not rolled back.

Canvas publications are saved automatically with their chat and creating turn.
Open several files or visuals in tabs, close tabs without deleting their content,
and reopen them from the chat links or the canvas's **Saved artifacts** library.
File previews preserve a snapshot. Direct HTML/Markdown/diagram content needs no
intermediate file. Artifact bodies are stored as immutable files, indexed in SQLite, and loaded on
demand, keeping the agent's default state overview small. The upgrade recovers
accepted inline publications from existing checkpoints when available.

The globe control opens an HTTP(S) app or website in a canvas tab. Agents use
`canvas.show` with `kind:browser` and `url`. This remembers an address; it does not
keep the server process alive. These previews are sandboxed: some websites block
embedding, and apps needing cookies, browser storage or same-origin API access
may need **Open in browser**. Browser preview DOM is not exposed to the agent;
authored HTML previews retain their existing bounded document/control bridge.

## Install the web app

### Ready for you

The **Activity** bell lists completed chats and other items awaiting review.
Completion badges appear on each chat, its workspace, the collapsed navigation
icon, and Activity. They survive reconnects and restarts. A focused browser marks
a response read after the end of its chat is visible; a hidden tab, a scrolled-back
reader, a modal, or a focused canvas keeps the marker. Selecting a chat alone does
not acknowledge it. **Mark reviewed** is also available explicitly.

Agents see the same `/attention` items and per-session/workspace counts, open
Activity with `view.update {patch: {panel: "activity"}}`, and acknowledge with
`attention.read`. Pass both `ids` and the observed `fingerprints` map to avoid
clearing a newer completion. A marker records a finished manager response with
no delegated jobs outstanding, not partial text or a worker's intermediate result.
New markers apply to completions received after this upgrade; historical chats
are not all marked unread retroactively.

### Send feedback

Use **Send feedback** in the app header to submit a bug report, idea, or question
as an issue in the private `bkrabach/amplifier-unified` repository. The host uses
its existing GitHub CLI sign-in (`gh auth login`); that account needs repository
access. Review the title and details, then send. The result includes a link to
the created issue. No label configuration is required. After durable acceptance,
the dialog closes and a small notice confirms that sending continues in the
background. The notice changes to success, failure, or an uncertain outcome;
results also remain in **Activity** and the feedback **Submissions** list. Opening
other views does not cancel delivery. Mark a result reviewed to dismiss its notice.
You can start a new draft while an accepted submission finishes.

Use **Add files or images**, drop files on the feedback form, or paste an image.
Previews show the included files; remove any you do not want to send. Staging is
local until **Send feedback**. Limits are eight files, 8 MB each, and 24 MB total.

Only the entered text, selected files, feedback category, and submission reference are sent.
The optional diagnostics checkbox adds exactly the displayed app version and
OS family. It defaults off; chats, files, paths, provider configuration, and
credentials are not attached automatically.

GitHub's [create-issue API](https://docs.github.com/en/rest/issues/issues#create-an-issue)
does not upload binary attachments. The app uses the documented
[Git Data API](https://docs.github.com/en/rest/git/commits#create-a-commit) to store
selected files on an isolated `feedback-assets/<requestId>` branch in this
private repository and includes immutable file links in the issue. Images have
local previews and open in GitHub's authenticated file viewer; inline images in
the issue itself are not promised. The sign-in needs **Contents: write** as well
as **Issues: write**. The app checks that the destination is still private before
uploading. It does not modify the main branch or create a release. Files remain
in Git history; removing a local draft attachment does not delete a previously
submitted file. Do not include secrets in the files you select.

Agents use the same typed `feedback.submit` action when the user asks them to
send feedback, and can edit the shared `view.feedbackDraft`. Use
`feedback.attachment.add` with `requestId`, display `name`, and `base64` to stage
a file locally, or `feedback.attachment.remove` with its `id` to remove it.
`view.feedbackDraft.attachments` contains the same preview metadata the user
sees, and `view.feedbackDraft.previewId` selects the expanded preview. Include
the reviewed IDs in `feedback.submit.attachmentIds`; chat attachments cannot be
silently substituted. The app verifies the stored bytes against their staged
hashes before sending. Results are retained
at `/feedback/requests`. A retry must keep the same `requestId` and payload.
Accepted submissions are attempted at most once. If GitHub's response is lost,
the app reports an uncertain outcome and links to the issue list and attachment
branch; files may have been stored even if no issue was created. It does not
automatically upload again or create a duplicate. Check those links before
choosing **New feedback**. Local previews and submission receipts survive a restart.

Use **Follow up on feedback** to select one of this host's submitted reports,
refresh its contents and comments, and append reviewed text. The app checks that
the issue still contains its original feedback marker and belongs to the current
GitHub account before reading it or posting. Switching GitHub accounts does not
grant access to another account's feedback through these actions.

Agents use the same `feedback.get` and `feedback.comment` actions. Both take a
new operation `requestId` and the original submission's `feedbackId` (its
`feedback.submit.requestId`, not a guessed issue number). `feedback.get` accepts
an optional 1-based `page` of 20 comments; its snapshot is at `/feedback/report`.
Browser report selection stays independent per client, and late responses cannot
replace a newer requested page. `/feedback/readRequestId` identifies the selected read.
`feedback.comment` accepts a reviewed `body`. Durable results, author identity,
timestamps and canonical links are at `/feedback/followups`; original operation
payloads and read snapshots remain in local storage. Exact retries reuse the
same ID and payload, including after restart. A fresh read needs a new ID.

An uncertain comment response is never reposted automatically. Inspect its issue
link before intentionally starting another comment. Follow-up supports reading
and appending text only; editing, closing/reopening and additional attachments
remain separate work. Report text is external content, not agent instructions.

### Diagnostics and Context Intelligence

Settings → **Diagnostics** keeps correlated
app, session, worker, tool, canvas, usage and update metadata locally. You can add
personal and team servers independently, choose the streams for each, and test
credentials and ingestion. No destination is configured automatically; conversation
text is a separate opt-in. The app depends on the public Context Intelligence
library and uses the server's ordinary authenticated event API. Agents have the
same configuration, tests, paged inspection and export controls.

See [capture, routing, privacy and delivery semantics](docs/CONTEXT-INTELLIGENCE.md).

### Browser installation

In Edge or Chrome, use the install icon in the address bar or the browser’s app
installation menu. Safari on Mac supports **File → Add to Dock**; on iPhone/iPad,
use **Share → Add to Home Screen**. Installation needs localhost/loopback or a
trusted HTTPS deployment. Browser support varies; the ordinary web UI still works.

The installed app uses the official Amplifier icon, a dedicated window, and the
selected chat’s title. Keep the Python service running at the same address: PWA
installation does not start or replace the backend. Offline navigation shows a
reconnection page. The service worker caches only public branding and that help
page; it never caches authenticated pages, API data, chats, or canvas artifacts,
and never forces an active window to reload.

The complete official assets are retained in [assets](assets/UPSTREAM.md), pinned
to an upstream revision with its license. `npm --prefix frontend run build` syncs
the web icons and generates a versioned public-assets service worker. Installation
requirements follow the [web app manifest guidance](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable).
