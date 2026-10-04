# Amplifier Unified Client TUI

An independent native Ratatui client for an AHP 0.9 host. Multiple terminals and
web clients follow the same host-owned ACP agent work while keeping separate drafts
and navigation. Closing the terminal detaches; it does not cancel execution.

The package and intended repository name are `amplifier-unified-client-tui`.
This repository is a fresh connected-client export. The existing
`microsoft/amplifier-app-tui` repository remains separate and unchanged.
A local package is not evidence of publication or installation elsewhere.

## Install and run

Python 3.11+, Node 22+, macOS or Linux (Windows through WSL2) are required.
The platform wheel contains the native renderer and bundled official AHP SDK.
An installed wheel needs neither Cargo nor npm nor any Amplifier execution libraries.
Source builds require Rust/Cargo and a C linker; regenerating the protocol bundle also requires npm.

```sh
uv tool install /path/to/amplifier_unified_client_tui-0.5.0rc2-PLATFORM.whl
amplifier-unified-client-tui --server ws://127.0.0.1:8765/ahp
amplifier-unified-client-tui --resume --workspace /workspace/on/the/host
amplifier-unified-client-tui --session ahp-session:/SESSION_UUID
```

`amplifier-tui` remains a local convenience executable for the same client.
`AMPLIFIER_UNIFIED_URL` selects a default host; `AMPLIFIER_TUI_NODE` can name an
explicit Node executable. Remote connections require WSS with verified TLS.
Use `--token-file /private/token` and optionally `--ca-file /private/ca.pem` for
an authenticated host. Tokens are sent in Authorization, never URLs, saved drafts,
conversation history or command-line values. There is no implicit lookup in an old
application's home directory. Loopback may omit a token only if that host permits it.
Client identity is presentation identity, not authentication.

Each launch gets an independent client ID. `--client ID` restores that view's
selection, drafts and uncertain input from `--state-dir`; concurrent processes
cannot claim the same local identity. Private intent is partitioned by host URL.
A cleared derived protocol cache can be fetched again; a deleted unsent private
draft cannot be recovered from the host, which never received it.

New managed conversations may use exact host-configured launch choices:

```sh
amplifier-unified-client-tui --new --managed --agent amplifier \
  --bundle approved --provider configured-instance --model exact-model
```

`--agent` selects an advertised ACP implementation; `--provider` is its native
provider instance. Bundle aliases resolve through passive managed options to an
exact revision; model/provider choices are paired and validated by the native owner.
`--effort` requires that pair. Initialization discovers choices without allocating
or starting a runtime. Allocation and the first input happen only on explicit Send;
original creation identity and choices persist before dispatch. Absent negotiation,
unknown bundle/provider and unsupported resumed/existing-workspace overrides refuse.
A managed conversation uses a fresh host-owned workspace, not `--workspace`.

## Everyday use

Enter sends, Alt+Enter adds a newline, and paste never submits. F4 opens Actions.
`/resume` lists root conversations in pages; `/new` opens a local composer and
creates host work only on explicit Send. Full AHP IDs or unique catalog-ID prefixes
can select a conversation. `latest` searches the current workspace scope. Raw native
engine IDs require the host's mapping and are not assumed to be AHP resource IDs.

The first conversation view requests 50 turns. Older pages use standard fetchTurns;
the renderer keeps every item in that requested window. Explicit backfill retains
loaded observations until detach; this is not a native-context limit. Supported
text, thinking, tools, errors, reported usage and resource references retain identity
and ordering. Large tool details stay inspectable as structured source.

`/messages` (Actions → Messages) lists the loaded saved messages. Reply selects a
private quote and Clear reply removes it; both preserve the draft and survive reload.
Only explicit Send submits the quote's canonical locator. Refresh saved messages is
an explicit bounded history read when a reference is absent. Streaming fragments and
archived context cannot be quoted. Frozen quoted source appears with the saved reply.
Inspect shared reactions reads the selected message. Add/Remove sets an offered emoji
for the authenticated host account; it never sends a prompt. A lost acknowledgement
blocks another change to that message until its original receipt is inspected. Current
counts cannot prove an unknown request, and receipt inspection never repeats it.
Quoted Send saves the complete frozen submission privately before dispatch. `/deliveries`
shows its original text, quote and command even after host admission or unknown native
execution. Inspect original quoted submission is read-only and works offline; it never
resends or replaces a newer draft/quote. Host admission alone does not prove execution.
A negotiated watch updates selected saved-message counts as other clients react.
Reconnect restores a read-only baseline; explicit Inspect refreshes current counts if
updates are unavailable. Watches never settle unknown original requests.

Permission choices preserve the options supplied by the agent. Stop uses standard
cancel; it does not promise force termination or undo. Rename changes the shared
session title. Unsupported queue, steer, takeover, history editing and device actions
refuse explicitly, without converting a command into a model prompt.

`/workspaces` pages existing host-authorized directories. Choosing one changes only
this client and opens an unsent composer; `/resume` uses server-filtered root sessions
when workspace.sessions is advertised. Standard-only hosts retain 50-row catalog
paging with visibly local per-page filters and unverified child semantics.
`/settings` (also `/models`) opens the standard host-provided model and conversation
settings. Opening the picker refreshes only the selected standard session snapshot,
without warming an agent. Only offered mutable choices can be selected; confirmation uses the action
echo and current host values. The private draft stays unchanged and no message is
sent. Refused changes and unknown outcomes have different notices. `/effects`
inspects original setting receipts; current readback never proves an unknown request.
Cold managed creation keeps its explicit launch flags until the host provides a
standard session schema; this client does not warm an agent just to guess choices.

`/details` inspects the selected native identity and failure information. `/recovery`
reviews a cold independent recovery copy; confirmation binds the inspected history
revision and never submits the retained draft. `/managed-files` reviews only a proved
managed allocation; disposal requires an explicit confirmation and host-owned
dependent-owner/family protection. `/effects` reads original operation receipts.
Unknown operations never replay. Escape dismisses a review and retains the composer.

Targeted agent UI tools inspect the actual renderer and use its current revision for
draft replacement, selection and local panels. A local keystroke invalidates a stale
edit before autosave. Device-effect receipts are durable before the action and never
replay unknown effects. Inspection includes a private draft only when explicitly requested.
Selection is an admitted asynchronous operation; losing its result remains unknown.

The local intent journal is saved before sending. AHP echoes establish acceptance,
not successful execution. `/deliveries` retains unknown outcomes; its recovery action
reads host receipts and never repeats the prompt. A disconnected client reconnects
with official replay/snapshot semantics. Missing local state starts from fresh
snapshots. Catalog cursors are discarded after reconnection or invalidation.

## Boundaries and capability inventory

| Surface | Current mapping |
| --- | --- |
| Session list/create/select/rename | Standard AHP catalog, session resources and actions |
| Text send/stream/errors/cancel | Standard chat actions; stable turn ID is durable intent identity |
| Tools and approvals | Official reducer, ordered generic rendering, exact offered permission IDs |
| History and reconnect | view.turns 50, fetchTurns, official replay/snapshot; no command replay |
| Private drafts/navigation/editors | Client disk and Ratatui; zero backend edit traffic |
| Usage and artifacts | Reported usage and generic resource references; rich viewers remain separate |
| Standard model / mutable conversation settings | Advertised standard schema and action echo/readback; no prompt, private draft retained |
| Native module/provider configuration beyond standard schema | Host capability owner; no private runtime import or guessed setting |
| Independent recovery copy / managed-file review | Negotiated preserve-history lifecycle; passive review, explicit exact-revision confirmation, original effect receipts |
| Workspace discovery / scoped root sessions | Negotiated workspace.list/workspace.sessions; 50-row opaque cursor pages; local private selection |
| Queue, steer, force stop, takeover, history editing | Explicitly unavailable until negotiated and mapped |
| Voice/camera/clipboard/canvas/publishing | No claim of connected device/rich-feature support; use an advertised capable client |
| Agent access to terminal-local UI | Standard client-provided inspect/action tools; actual renderer revision checks protect unflushed keystrokes; no permanent draft mirror |
| Standalone execution | Not part of this independent client |

The connected wire is solely AHP. A bundled Node sidecar uses the unmodified official
`@microsoft/agent-host-protocol` SDK and reducers; Python adapts identified presentation
rows to Ratatui. Streaming crosses the local presentation boundary as changed rows,
not a serialized historical transcript per token. Client protocol state is bounded by
selected resources and requested windows; explicit full-history recovery follows
upstream semantics and is not silently truncated.

The helper bounds incoming/outgoing frames, pending commands and output backlog;
exceeding a bound fails visibly and keeps delivery uncertainty. The current two-process
presentation topology is a compatibility choice to retain official reducers and the
native renderer. Tests do not establish comparative latency or process-memory gains.

## Development and evidence

```sh
uv sync --inexact --no-sources
npm ci --prefix ahp
npm run build --prefix ahp
cargo build --locked --release --manifest-path frontends/ratatui/Cargo.toml
AHP_HOST_ENTRY=/independent/host/dist/cli.js uv run --no-sync pytest -q tests/test_ahp_client.py
TUI_TEST_CANDIDATES=1 AHP_HOST_ENTRY=/independent/host/dist/cli.js uv run --no-sync pytest -q tests/test_ahp_terminal.py
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_direction.py
uv build --wheel
```

Use owned temporary homes and fixture agents. Ordinary AHP tests launch the public
host executable. With `AHP_HOST_MODULE`, the client-tool composition fixture imports
only the host package public API, never private sibling implementation modules. The actual terminal
smoke runs at 120×40 and 40×20 with streaming, permission, private draft and terminal
restoration. `TUI_CONNECTED_EXECUTABLE` repeats it against an installed wheel.
See [acceptance](notes/ACCEPTANCE.md) and [verification guide](SMOKE_TESTS.md).

This client contains no embedded execution harness, `standalone` extra,
`--standalone` bypass, CLI passthrough or direct native session writer. The supported
test manifest covers AHP behavior and retained native renderer interactions.
See [the boundary inventory](notes/AHP-CUTOVER.md).
Paid provider calls, Copilot/Codex/Claude accounts, production Spark performance and
physical-device interaction are separate acceptance boundaries. No source or wheel
check alone proves publication, installation elsewhere or real-account compatibility.

The [repo-local parity inventory](notes/CONNECTED-PARITY.md) records contract mapping,
installed-peer evidence and remaining acceptance boundaries.

Qualified release asset schema, supported-platform candidate workflow and safe
publication handoff are described in [release/README.md](release/README.md).
Only independently passing installed platform artifacts may be advertised.
