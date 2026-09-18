# Diagnostics and Context Intelligence

Settings → Maintenance → Diagnostics & Context Intelligence configures the same
`diagnostics.*` actions available through the agent's app tools.

The application depends on the public `amplifier-bundle-context-intelligence`
library (temporarily pinned to the reviewed public-client contribution while its
upstream PR is pending). It uses that library's event envelope, idempotency keys, authentication
strategies and HTTP client. It sends to the ordinary Context Intelligence `/events`
endpoint; no server plugin or Unified-specific endpoint is required.

## Local capture and correlation

Metadata is captured locally by default. Streams cover app actions, sessions,
workers, tools, model/token/cost calls, canvas activity, Smart Tool operations and
update diagnostics. App chat IDs, runtime session IDs, parent session IDs, turn IDs,
tool-call IDs, operation IDs and update-attempt IDs correlate the records. Model
usage uses the server's standard provider/LLM event names and usage fields.

Conversation text is a separate **opt-in** stream. Metadata does not include
prompts, model reasoning, tool arguments/results, rendered HTML, attachments,
provider configuration or raw command output. Content redaction is best effort;
selecting conversation text may disclose personal or confidential information.
A configured bundle's independent telemetry hooks retain their own policies;
these settings govern the app's collector, not unrelated exporters.

Capture enqueues without network I/O on the conversation path. A background task
persists records in a private `diagnostics/events.sqlite3` database. Network
workers are independent of capture and of one another. The in-memory queue is
bounded at 2,000 events, individual metadata/content records at 48 KB, each
server's pending/failed queue at 2,000 records. Overflow or storage failure is
visible in settings and attention indicators, rather than stopping conversations.
Unflushed records can be lost if the process crashes; normal shutdown flushes.

Retention defaults to 30 days and 25,000 records (whichever limit comes first).
It applies to pending deliveries too; an expired delivery is counted explicitly.
Accepted delivery receipts are kept for the record's retention period. SQLite
reuses freed pages; file size can remain at its previous high-water mark.

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
