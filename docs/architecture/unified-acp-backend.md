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
