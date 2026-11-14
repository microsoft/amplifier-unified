# Use Unified as a Constellation backend

The installed `amplifier-unified acp` command attaches standard ACP stdio to an
already-running Unified host. Several attachment processes can use that same
host. Closing one does not shut down the host or stop accepted work. This text
backend uses the public ACP SDK shipped with Unified, without the private ACP
application or a second writer for Unified's data directory.

## Setup

Install and start Unified normally, connect its model account, and choose its
default bundle. Use a dedicated host/data directory for an independent agent
installation, or attach to an existing host. The attachment command never starts,
restarts or updates a host automatically.

For a local host under the same operating-system account:

```sh
amplifier-unified --data-dir /path/to/unified-home --workspace /path/to/workspace acp
```

This command speaks ACP on standard input/output; diagnostics use standard error.
Configure Constellation Instance's existing custom backend fields:

```json
{
  "command": "/absolute/path/to/amplifier-unified",
  "args": ["--data-dir", "/path/to/unified-home", "--workspace", "/path/to/workspace", "acp"],
  "env": {},
  "secret_env": {},
  "working_directory": "/path/to/workspace",
  "allowed_working_roots": ["/path/to/workspace"]
}
```

Apply this backend object through Instance's existing settings/configuration
operation, preserving its other settings and revision checks. The workspace must
already exist on Unified's host. Optional `acp --bundle NAME` selects a bundle
for new conversations; otherwise they use Unified's default selection.

Remote attachment uses `acp --server https://unified.example --token-file
/private/unified-owner-token`, plus `--ca-file` for a private CA. Remote HTTP and
redirects are refused. Local attachment reads the selected installation's existing
owner credential and configured local listener, using the host's existing leaf
certificate pin for HTTPS; it never creates a credential. This credential
provides owner access: keep it private and give it only to an authorized launcher.
Never place its contents in arguments, URLs or Instance settings.

### Choose a profile with MCP support

Transient HTTP MCP support also requires `tool-mcp` in the selected native
profile. Work 0.4.0 does not include it. If an attachment supplies
MCP servers without that capability, preparation refuses the request before
model execution.

For a new conversation that needs connected tools such as Instance's context
retrieval, save this explicit profile as `/path/to/profiles/work-mcp.md` on the
Unified host. This composition was tested with published Unified 0.94.1, the
pinned Work source below, and canonical MCP v0.3.0:

```markdown
---
bundle:
  name: work-mcp
  version: 1.0.0
  description: Explicit Work profile with canonical MCP support.
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-work@b5154aabf0aaed0ad9465f711abb3bfb18dee6f8#subdirectory=bundle.md
tools:
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@77b74bee1a7947a0cf4b3bac4d0dd74490c1ea21
---

@work:context/system.md
```

Select it through the existing launcher option:

```sh
amplifier-unified --data-dir /path/to/unified-home --workspace /path/to/workspace acp --bundle /path/to/profiles/work-mcp.md
```

For Instance, append `"--bundle", "/path/to/profiles/work-mcp.md"` to the backend
`args` above. The profile path is on the Unified host. This selects the profile
for **new conversations**; exact-ID resume retains the conversation's saved root
bundle. Changing launcher arguments does not convert an existing Work-only chat.
Unified's default profile remains unchanged.

Preserve existing [scoped source choices](../scoped-bundle-sources.md): module or
bundle overrides can take precedence over these declarations. Confirm the actual
selected sources when qualifying a different configuration. Transient server
URLs, headers and credentials arrive through the ACP connection, not this profile.
The MCP module also reads the execution account's ordinary MCP configuration;
review those settings when preparing a dedicated agent.

## Ownership and recovery

Unified owns native execution and durable input receipts. Constellation and its
experiences retain their public conversations and participation mappings. The
returned ACP session ID identifies the corresponding Unified chat, which can be
inspected in Unified while the external experience uses it.

New sessions, exact-ID resume, text prompts, streamed text/tool activity and
explicit cancellation use the existing execution owner. Reconnect never creates
a replacement chat or resends an earlier prompt. A lost response after dispatch
leaves the outcome uncertain to the client; inspect the retained chat before
deciding on new input.

Attachment does not change access mode. Existing Full authority continues without
a human approval loop. Decisions this text adapter cannot present stay with the
host; it never invents consent. Use Unified's existing Access control or `access`
command to set standing access.

Disconnect releases observation and future use of the attachment's transient HTTP
MCP settings. Admitted work retains its mounted tools and can finish. Reattachment
can retire a released, settled worker through the existing acknowledged retirement
path before supplying fresh tools. Connected owners, active/protected work and
unconfirmed retirement are not replaced. Retry after that work settles.

Host shutdown remains different from client disconnect and follows existing
recovery policy. This feature does not promise survival of process/machine loss.
The authenticated WebSocket hop is internal to the shipped launcher and host,
not a portable upstream ACP network protocol; external clients use ACP stdio.

### Optional conditional retirement

Initialization metadata `com.microsoft.amplifier.retirement` version `1`
advertises `_amplifier/retire` and advisory notification
`_amplifier/retirementRequested`. These are private optional extensions over the
existing ACP connection, not upstream ACP session-close semantics or a blanket
claim that a wrapper can be killed safely.

The request contains only `sessionId`, the exact execution attached to this
connection. The result repeats `sessionId` and has `disposition` equal to
`retired`, `busy` or `unknown`. Only `retired` authorizes closing this attachment.
The owner refuses cleanup when it has other attached sessions or unsettled work;
this does not limit how many sessions a connection can normally use. Native
retirement checks active and background work, approvals, tools and persistence
under the existing admission/retirement handshake. Connection-bound tools stay
mounted until the acknowledgement and a final matching-owner check. Refusal,
timeout, caller cancellation or changed ownership does not release those tools.
An exact successful result can be read again after a lost response, without
creating a session or replaying an input.

The advisory notification contains only `sessionId`. The existing worker-retention
sweep requests cleanup only when its warm-worker budget, idle timeout or pending
generation update calls for it. The defaults remain 32 warm workers and 12 idle
hours. Finishing a turn does not itself request attachment cleanup. Policy is
checked again under native admission before retirement: a delayed pressure hint
cannot evict a worker that is now within its warm budget. Older
`retirementAvailable` advertisements do not negotiate automatic cleanup.
For an attachment with no mounted tools, normal native retention can finish
without first involving that connection. Its acknowledged completion emits the
same request after the exact worker is removed, including after a late reply.
The attached client can then obtain the already-retired receipt. This does not
start another retirement or replay work, and failed notification delivery cannot
undo the completed native retirement.

The request is neither an idle guarantee nor an instruction to retry work.
Reading an already-retired receipt does not produce another notification. This reuses the existing
retention sweep and settings; no client timer or second retention policy is added.
Naming completion wakes that owner after its future and persistence settle.
Background resources, kernel or computer custody and uncertain operations can
still refuse cleanup. A demand arriving while the attachment is busy or already
checking retirement can be ignored; subsequent existing retention sweeps can ask
again. There is no guaranteed immediate retirement at every resource transition.
Confirmed cleanup retires the native warm worker as well as its attachment, so
the next real message resumes saved history with worker startup cost. It trades
idle resources for later startup, rather than promising a performance gain.

## Scope and qualification

Advertised capabilities are text, resume and transient HTTP MCP. Legacy-history
loading, images/audio, client-side files/terminals and a new permission UI are not
advertised. Companion's voice layer can use this text backend. Separate chats can
run concurrently; one native writer and ordinary admission still govern each
execution.

Maintained qualification uses the real SDK, connection and service persistence
with a controlled runtime, plus a real worker retirement handshake. Live-provider,
Companion voice and user-desktop acceptance are separate evidence; candidate tests
do not imply adoption by a live host.
