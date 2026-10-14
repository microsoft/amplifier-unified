# Execution inspection from native history

Tool input, result and error content comes from the session's existing
`context-intelligence/events.jsonl`, including delegated sessions and the supported
CI relocation setting. The authenticated owner's view shows the recorded values
without another redaction pass or a capture-time truncation. This does not change
the event logger's capture policy. Existing logs gain the view without replaying
work or changing their bytes.

`EventLogView` maintains a disposable, incremental in-memory index of lifecycle
metadata, short previews and byte references. It does not write an index, payload
capture or materialized execution view. New live observations carry identifiers,
status, timing and usage only; neither those observations nor reconstructed event
rows are saved to `unified/view.json`. Pre-existing legacy history is preserved.
Foundation's transcript associations place undated native tool calls by their
recorded identities; a session creation date is not treated as a message time.

Work is grouped between conversation messages. Each group has its own elapsed
time, usage and tool/model counts. Group totals cover all source rows even when
individual action rows are paged. The shared shell presentation setting
`executionDetail` selects time only (`minimal`), time plus usage/cost (`standard`),
or those details plus tool/model counts (`detailed`). Skins can also control the
call-count display with `--a-work-counts`.

Each action starts as a collapsed one-line summary, including model calls.
Every work group has a collapse control, regardless of action count. Groups start
collapsed unless explicitly expanded; their saved state survives reloads and
collapsing a group preserves the individual action expansion choices inside it.
Group size is measured from the skimmed rows, not the size of unopened payloads.
Code fences show all content through 15 lines, then preview the
first 10 lines with a single expand control. Exceptionally long unbroken text is
also previewed. Commands, output and exit status, file contents, patches, task
lists and delegated actions have typed presentations. Additional recorded
arguments and result fields remain visible without a duplicate raw-input/result
expander. Copy uses the complete loaded field, including text outside the preview.

The browser initially receives bounded history and 512-character action previews.
Expanding an action reads each referenced tool field from its original
log record, verifies its digest, and returns that complete field in one request.
Parsing and file reads run off the server event loop. A replaced log invalidates
old references; failed reads retain the preview and offer retry. Partial final
JSONL records are retried after the next append. Inspection never executes tools.

Assistant replies automatically load complete saved text when approaching the
viewport, using the existing authenticated 16 KB message-detail pages. Navigation
cancels pending reads. User messages and large worker summaries keep their
existing text controls.

Live timers use host time; completed timing uses recorded start/end timestamps.
Model details show provider/model, timing and reported usage. Native provider
receipts and matching app lifecycle receipts are counted once. Missing usage is
pending during a live call and unavailable after completion. Reported, estimated
and partial cost stay distinct; missing cost is not shown as measured zero.

Expanded model calls show provider/model, timing, token accounting and recorded
request options such as message/tool counts, limits and reasoning effort.
Choose **View request** or **View response** to load that provider's captured
payload from the existing authenticated detail endpoint. Every payload, including
a short one, requires an explicit click. The JSON tree supports incremental
expansion; **Copy raw request/response** copies the loaded field.

**Download recorded request/response (JSON)** exports the loaded payload with
session/call identity, provider/model, timestamps, source digest, and available
redaction/truncation metadata. A partial capture has `capture.truncated: true`
and `-partial` in its filename. Missing capture metadata remains unknown, never
an assertion of completeness. Exporting does not perform another model request
or recover omitted content. Captures currently have a one-million-character
host limit; some providers emit only summaries. Old uncaptured calls cannot be
reconstructed. The download is local and is not automatically sent as feedback.

No payload enters initial browser state or a saved execution projection; only
an opaque digest reference and bounded metadata are indexed. Model-recording
references use `digestKind: event-field-v1`, binding the exact source record and
field; formatted length/line counts are omitted until the payload is opened.
Clients must pass the supplied digest unchanged and tolerate absent length.
The host validates source bytes before returning the same complete formatted
payload; this does not change capture, redaction, or download contents. Existing
text-digest references remain readable. Provider identities
are checked before merging native and app lifecycle telemetry. Ambiguous calls
are never assigned another call's recording. Failed reads offer retry, and
replaced logs invalidate old references. Closing or changing the call cancels
pending reads. Payload disclosures close when their model row is remounted.

Workspace plan summaries read root todo receipts through the incremental event
index. They do not hydrate the saved execution graph, traverse child logs, or
associate the conversation transcript. A changing chat's read failure retires
only the indexes touched by that read, not every other chat's parsed history.
