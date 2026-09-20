# Diagnostics and Context Intelligence

Settings → Diagnostics configures the same
`diagnostics.*` actions available through the agent's app tools.

The application depends on the public `amplifier-bundle-context-intelligence`
library (temporarily pinned to the reviewed public-client contribution while its
upstream PR is pending). It uses that library's event envelope, idempotency keys, authentication
strategies and HTTP client. It sends to the ordinary Context Intelligence `/events`
endpoint; no server plugin or Unified-specific endpoint is required.

## Local capture and correlation

Ordinary session capture uses the community `hook-context-intelligence`, mounted
through Foundation, just like a CLI bundle. It writes the same
`~/.amplifier/projects/<slug>/sessions/<id>/context-intelligence/events.jsonl`
and metadata contract. The orchestrator emits prompt submission; the host supplies
prompt completion at each final turn. The native transcript remains beside that
capture as `transcript.jsonl`. Foundation owns the shared execution lock and native transcript/metadata
reader and writer; event logs enrich history in memory and never replace it. See [storage](STORAGE.md).

The default app-added hook is local only (`destinations: {}`). A hook already
selected by a bundle keeps its own configuration; a saved complete mount plan or
explicit module override is respected. Configure that hook in the running mount
plan to change **raw local session capture**. Raw CI captures can contain prompts,
tool arguments/results, reasoning and other content, as in the CLI.

These diagnostics settings instead govern the app's **selected index and optional
forwarding**. The app tails shared captures read-only. Its default projection
contains metadata, not prompts, reasoning, tool inputs/results, HTML or files.
Conversation text is a separate opt-in. App-only events (updates, feedback, canvas,
Smart Tool activity) append CI-format records; they do not replace session logging.
A bundle's independently configured remote exporters retain their own policies.

`diagnostics/events.sqlite3` is now only a bounded file-offset index and delivery
outbox, not another event archive. Reading old captures never schedules uploads.
New selected records can be forwarded to explicitly enabled destinations using the
public library client. Authentication, redaction, destination selection and retry
policy belong to the app; the local hook needs no ingestion API or server.

App capture queues without network I/O on the conversation path. The in-memory
queue is bounded at 2,000 records; each server's pending/failed queue at 2,000.
Overflow and storage errors are visible without stopping conversations. Unflushed
app events can be lost on a crash; normal shutdown flushes. The hook owns its own
local durability and flushing behavior.

Index retention defaults to 30 days and 25,000 records. It also expires pending
deliveries with an explicit counter. It **does not delete shared events.jsonl or
transcript.jsonl files**. Accepted delivery receipts live for the index retention
period. SQLite reuses freed pages.

## Optional independent destinations

No server is configured or enabled by default, including when familiar CI
server environment variables already exist. Each explicit destination specifies:

- Server URL and an API-key **environment variable name**, or Microsoft Entra
  resource URI using the library's default credential chain.
- Its own selected streams, server workspace label and optional workspace-path
  glob. Remote HTTP is rejected; localhost HTTP is supported.
- Whether to include workspace folder paths (off by default).

A workspace label organizes a shared server's graph; it is **not** a security
boundary. Only enable a destination whose operators may receive those streams.
The UI and agent state reveal credential availability, never key values.

Saving a new route affects new records only. Changing or removing a destination,
or disabling capture, cancels that route's pending data. Old events are never
silently sent to a new endpoint or backfilled when another stream is selected.
An already accepted or in-flight request cannot be recalled.

Connection tests authenticate with `/whoami`, then send one synthetic probe to
verify write access. Green means authenticated and accepted for indexing, not that
graph enrichment has completed. The server can report a recognized duplicate on
an idempotent retry; this is tracked separately from a new acceptance.

## Failures and inspection

Timeouts, connection failures, HTTP 408/429 and server errors retry with bounded
backoff (at most eight attempts); rejected credentials and other permanent errors
wait for explicit Retry. Retries preserve the exact envelope and idempotency key.
The public client never follows a redirect with an ingestion request. Error
receipts retain classifications and HTTP status, not server bodies or credentials.

Local records are paged by session ID and stream glob through
`diagnostics.records`. `diagnostics.export` exports only the page you just
inspected in the Context Intelligence JSONL event format. This keeps logs out of
the default app-state snapshot and gives users and agents the same deliberate
inspection and sharing workflow.

Update receipts additionally retain semantic phases, attempt and command IDs,
durations, exit codes and bounded probe classifications. Raw subprocess output,
arguments, environment values and paths are excluded.
