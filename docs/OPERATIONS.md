# Observed operations and durable output

Unified exposes one conversation-scoped view of managed processes, existing
Smart Tool receipts, and workers through `operations.list`, `operations.status`,
`operations.read`, `operations.wait`, and `operations.cancel`. The Operations panel
and agent app actions use these same actions. Reading or waiting does not select
a conversation, change its draft, activate a runtime, or replay work.

## Ownership and admission

The host binds the real conversation identity at the worker bridge. A process
handle is authorized by its conversation, runtime session and mounted owner; an
OS PID or tool-supplied session identifier is not authority. Child runtimes stay
bound to the owning conversation while cancellation targets their exact mount.

Managed process production requires a tool implementation that supports the
optional `operations.observe` capability and trusted `managed_processes: true`
configuration. This change does not enable it in default bundles. The host
registers a local callback, never a remote callback URL. The portable journal is
producer-neutral; the host currently accepts only tool-bash process events.

These actions observe existing work; there is no generic operation submission
endpoint. Existing shell dispatch admits process starts with its existing hooks,
policy and permissions. A future submission adapter must check any exact required
question IDs before admitting dependent work; unrelated work remains independent.

Cancellation uses the existing `RuntimeControls.invoke` tool pre/post/error and
coordinator permission path, with actor, source and operation attribution. It
binds the exact `bash` terminate arguments and revalidates them after hooks,
including in-place modification. It does not require the conversation to become
idle. Approval and bridge waits run outside command locks. Cancellation never
reactivates a retired runtime and never treats a request as proof of termination.
Other receipt adapters remain read-only here and retain their existing controls.

## Durable library and observation contract

`amplifier_operations.OperationJournal` is a standard-library-only package included
in the wheel. It has no app or module imports. Its public calls are:

- `ingest(session_id, runtime_session_id, event)` returns `(record, changed)`.
- `status(session_id, id)` and `list(session_id, limit=50, before=None)` read receipts.
- `read(session_id, id, cursor=0, max_bytes=16384)` reads a bounded output page.
- `recover(session_id=None, reason=...)` invalidates unresolved ownership.
- `close()` releases storage. Opening a library reader alone does not recover work.

The event envelope contains `schemaVersion: 1`, `eventId`, monotonic `sequence`,
`operationId`, `ownerId`, producer `source`, `kind`, `phase`, and `at`. Phases are
`started`, `output`, `state`, and `finished`. Output chunks have `cursor`,
`next_cursor`, `stream`, `text`, `source_bytes`, `binary_output_withheld` and
`encoding_loss`. The host deduplicates identical events and rejects conflicting
identity reuse, ownership changes and out-of-order delivery. Sequence and output
cursor gaps are retained as missing evidence. Commands and stdin are not in this
event stream; output itself may be sensitive.

Storage is a private SQLite WAL journal at `operations.sqlite3` with FULL
synchronization. Receipt and output changes commit together; output is not copied
into an additional unbounded event archive. Host backups use a consistent SQLite
snapshot, including output. Default output retention is 10 MB and 10,000 chunks
per operation; source event identities retain the latest 10,000 sequences. Output
reads are repeatable while retained, bounded to 128 chunks and 4–100 KB of source
bytes per page. `nextCursor`, `hasMore`, `earliestCursor`, `droppedOutputBytes`, and
`cursorGap` make pagination and expired evidence explicit. The host list returns
at most 100 recent operations; known IDs remain readable directly.

`operations.wait` uses revision changes and event notification, not a polling
loop. It returns on actual observation changes or its bounded timeout (up to
60 seconds). Clients carry the last revision and output cursor when reconnecting.

## Truthful completion and limitations

Process states distinguish `running`, `cancel_requested`, `completed`, `failed`,
`cancelled`, and `outcome_unknown`. Terminal process success/failure/cancellation
requires an observed exit code. On app restart or loss of the owning runtime,
unresolved active receipts become `outcome_unknown`, their captured output stays
readable, and their controls become unavailable. No command is replayed. A real
final event from the same owner can settle an unknown receipt; a reconstructed
runtime cannot. External side effects may have happened even when outcome is
unknown. This is durable observation, not a broker that keeps a process alive
through host death.

Output completeness has three separate fields:

- `streamComplete`: the producer drained its streams.
- `captureComplete`: every observed original byte is represented without source
  sequence gaps, observer loss, binary withholding or lossy text decoding.
- `outputComplete`: streams drained, capture is complete, and no retained original
  output expired. This stays false after exit if any original evidence is missing.

The optional producer uses a bounded mailbox and a deadline for observer calls.
It never blocks subprocess draining on storage. Under backpressure, observer
failure, timeout, binary guarding, invalid UTF-8, or retention expiry, output can
be partial. Loss markers are evidence of loss, not a substitute for the missing
bytes. Availability of the callback does not guarantee durable capture. A host
crash can occur before an event commits; surviving receipts cannot prove an
unobserved external outcome. Normal process completion with a complete local
ring is likewise not proof that the host journal captured everything.

Smart Tool receipts and worker jobs are projections of their existing stores,
not duplicate execution registries. Their original result/error/report fields
remain the evidence; output completeness is unknown rather than inferred from
terminal status. An idle worker is waiting for input, not completed. Large output
references can point to an actual saved operation ID and cursor via the shared
read action; arbitrary historic tool payloads are not silently archived here.

PTY, terminal resizing, native Windows managed execution, and reconnecting to a
live process after host death are unavailable. Raw interpreter input still needs
an explicit trusted mount policy; command allowlists do not authorize stdin.
Full original binary output storage and guaranteed lossless delivery are not
provided by this text observer. There is no automatic retry of input, execution,
or external side effects.

## Validation

Run the focused Python suite with `pytest tests/test_operations.py`. If the
managed bash module is importable, it also exercises a real subprocess and durable
output beyond its local ring; otherwise that optional integration test skips.
`node frontend/tests/operations-browser.mjs` starts an isolated fixture and checks
live output, final state, reconnect, selected conversation/draft preservation and
mobile bounds against the production build. These checks do not claim a live
provider conversation or full Work bundle composition has been deployed.

Additional host receipt stores can register a read-only adapter with
`service.operations.register_source(name, list_records, read_record)`. Callbacks
receive the bound session ID (and exact prefixed ID for read) and return common
operation records. Identity/session mismatches are rejected. The original store
owns execution and evidence and calls `operations.notify()` when it changes.
This registration is a trusted host seam, not an agent-defined callback.
