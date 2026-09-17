# Amplifier Unified

A local Python host serving a bundled React interface. One conversation supports typed chat, notification-oriented text, and real-time voice. Community bundles and tools execute through AmplifierSession, Foundation and loop-live in isolated worker processes.

## Run this checkout

```sh
uv run --project /path/to/amplifier-web amplifier-unified --workspace /path/to/your/project
```

Then open http://127.0.0.1:8941. Start a conversation, choose a community bundle URI or the configured `anchors` default, and send a message. The runtime prepares its pinned environment on first use. On first launch, existing Amplifier settings, bundle registry/cache and keys are copied into the app-owned configuration. Later launches read the app configuration and server environment credentials. Missing credentials or unavailable providers are reported as errors, never simulated responses.

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
uv tool install git+https://github.com/bkrabach/amplifier-unified@v0.5.4
amplifier-unified
```

End users need no Node installation: compiled React assets ship in the Python package. **Settings → Maintenance → Updates** checks community sources and this private release channel. Automatic checking is on daily while the host is open; automatic installation is opt-in. App releases restart the host when idle. See [the update design](docs/UPDATES.md).

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

The runtime dependencies are pinned separately under `amplifier_web/runtime_deps/`. The launcher prepares them through uv in a writable user cache. The outer host remains small and independent of provider import dependencies.

## Standalone host and session flow

No `amplifier-app-cli`, `amplifier-loop-live-cli`, or `amplifier-workspace` host libraries are installed or imported. The application owns configuration, approvals, checkpoints and child-session lifecycle, using Foundation's public bundle preparation and session APIs.

```text
React chat / text / Live voice / Realtime fallback
    → shared app actions and identified input
    → main AmplifierSession + loop-live
    → configured providers, tools, delegated AmplifierSession workers
    → identified completed manager turns and worker reports
    → one conversation, notifications and voice narration
```

Live and Realtime request reasoning and app operations through `amplifier_delegate`. Only the Amplifier session executes tools, including `app_control`. Live retains its own audio transport and conversational speech generation; the session owns delegated work. Ending audio leaves accepted work running. A manager response reports pending background workers separately from completed work.

Configuration lives in `~/.amplifier-unified/config/settings.yaml`, with private keys in `config/keys.env`, imported workspace snapshots in `config/workspaces/`, and live workspace overrides in `<workspace>/.amplifier-unified/settings.yaml`. Foundation has an app-owned registry/cache. Transcripts and job evidence live under `sessions/`; old transcripts are imported when their session is explicitly resumed. Interrupted operations are never automatically replayed. Settings provides provider connections, model discovery, routing presets, and provider-supported login. ChatGPT device-login tokens live in the app’s private configuration; providers without a public login flow use token or existing SDK authentication.

Community bundles retain their providers, tools, hooks and agents. The supported streaming orchestrator is explicitly overlaid with loop-live and the original/adapted mount plans are recorded privately. Custom incompatible root orchestrators fail with an actionable error. The app currently includes a reviewed local loop-live change for delivered-input correlation and manager-turn completion; [the upstream contribution](docs/loop-live-upstream/README.md) is prepared but not published.

## Shared control and appearance

`GET /api/state` exposes shared session/application state and attached device snapshots. `GET /api/actions` lists action schemas. `POST /api/actions` accepts `{action,args,id?,expectedRevision?}`; UI controls and the runtime's app-control tool use the same handlers. `GET /api/events` streams state updates. State includes `attention.items`, unread counts, and section/page destinations. `attention.read` accepts item IDs to acknowledge review; it does not dismiss the underlying update or issue. Changed facts become unread again, and acknowledgements survive restarts. SQLite stores conversations, accepted command IDs and settings. Interrupted work is marked rather than silently replayed.

Skins are complete self-contained CSS files. The Appearance panel imports, edits and exports skins. The supplied Converge skin includes the Amplifier logo and blue/lilac surfaces. Device permission dialogs are still handled by the browser. Tool actions that specifically request human approval remain human approvals.

The server binds only to loopback and rejects cross-origin requests. This first version is for a single user's local computer, not remote hosting.

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

Connection options (`--port`, `--workspace`, `--data-dir`) precede the subcommand. One-shot commands connect to an existing host or start a temporary local host; they use the same approvals and runtime. Noninteractive tools cannot grant human approval automatically.

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

The left rail expands on hover; its sidebar icon toggles whether it stays pinned. It manages existing workspace
folders and their conversations. The right canvas has an adjustable width and
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
intermediate file. Artifact bodies are stored separately in SQLite and loaded on
demand, keeping the agent's default state overview small. The upgrade recovers
accepted inline publications from existing checkpoints when available.

The globe control opens an HTTP(S) app or website in a canvas tab. Agents use
`canvas.show` with `kind:browser` and `url`. This remembers an address; it does not
keep the server process alive. These previews are sandboxed: some websites block
embedding, and apps needing cookies, browser storage or same-origin API access
may need **Open in browser**. Browser preview DOM is not exposed to the agent;
authored HTML previews retain their existing bounded document/control bridge.
