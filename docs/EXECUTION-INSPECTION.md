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
status, timing, usage and bounded action/outcome hints; neither those observations nor reconstructed event
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
first 10 lines followed by a separate ellipsis and a preview label, with a single
expand control. Expansion shows the complete content without the preview marker;
structured JSON may be formatted for readability. Copy retains the original saved
text. Copying the shortened view still copies the complete saved field.
Exceptionally long unbroken text is
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

Collapsed tool rows retain the tool name beside the action verb and a bounded
command, code, file, or action preview, plus an available error and exit status.
On narrow screens, that preview may wrap onto two lines below the tool label. These are display hints,
not complete arguments or execution evidence. Image-tool lifecycle hints (`image_generate` and
`nano-banana`, including analysis) omit prompt/path and result-text hints; the
shared normalizer also clears previously supplied hints. Their original saved
arguments/results remain available through explicit, scoped detail inspection.
Other tools without a specialized display also carry `inputPreview`, a deterministic JSON-shaped request hint of at most
180 characters. It visits at most four entries per container, two container
levels and 24 values; shortened values end in an ellipsis. Known credential-named
fields are redacted in this hint. This is not a secret scanner for arbitrary
free text, and it does not alter the saved input. Zero, false and null remain
visible; absent input clears the hint. `app_control` dispatch also carries a
bounded `actionPath` from its actual nested `args.action`, without flattening
its arguments. Expanding reads saved arguments
and results; copy controls never treat a shortened hint as the full payload.
Unrelated log appends do not invalidate an unchanged exact saved field. Source
replacement or edits still invalidate it; temporary read failures offer retry
without claiming the content changed.

Expanded tool payloads use whitespace-only JSON layout for bounded complete
objects and arrays, preserving numeric spelling, duplicate keys and string
escapes. Copy retains the original saved text. Long text has an explicit preview
and expansion control; incomplete saved reads stay labeled as previews until
loading succeeds. A stalled or non-progressing detail read offers Retry; closing
the step cancels it. Older-detail lookup remains one bounded page per explicit
request and never starts conversation work.

New tool observations carry a host-generated ID shared by the exact native event
and its host update. The bounded current window can attach that event's input,
result, error and typed delegation fields without adopting native lifecycle or
accounting. Collapsed hints are at most 180 characters each and do not substitute
for complete saved fields. A new observation cannot inherit an earlier input when
its own producer omitted it.

Expanding a host tool whose saved fields are outside the current window performs
one bounded read of that tool's admitted native session. **Search older saved details**
requests another bounded page; it never scans the whole history or enumerates
child directories. Lookup leases are bound to the web client, source identity,
open Work group, admitted row position and observation ID. At most 100 matching
observations and eight window pages are retained in memory.

Already-open resident Work rows retain their accepted exact field references when
newer events leave the recent 100-observation window. These leases follow the
existing admitted-row/group lifetime (up to eight groups, each with up to 800
admitted rows); no complete log, payload, or source page is retained. At most
seven field descriptors and 512-character previews are retained per row:
2,867,200 preview characters per maximally admitted group, plus bounded
descriptors. A different tool observation or provider attempt cannot inherit
those fields. Reads and downloads still verify the exact canonical bytes and
the current client, owner, source, admitted position and identity before and
after asynchronous work. Closing/retiring the group releases its references;
normal recent-window turnover alone does not erase an open detail.

Older history without an exact host observation ID shows related saved records
as separately expandable observations. Matching call IDs, tool names or times
cannot prove an attempt, so these fields are never grafted onto host lifecycle
rows. Repeated call IDs stay separate. Clearly unrelated times are excluded;
unknown timestamps do not become positive identity evidence. Missing, partial,
unverified and expired details remain distinct, with bounded retry controls.

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

## Independent metric visibility

Settings → Appearance → Execution metrics controls individual steps, Work
summaries and conversation totals independently. Each can hide its accounting
without hiding work status, failure, pending decisions or cancellation. Until a
scope has an explicit choice, Focused keeps inline step/Work metrics quiet;
explicit choices then survive changes to the overall detail level. Selected
fields apply to token, cache, reasoning-token, cost, elapsed-time and model-call
count displays and metric exports. An empty selection is valid.

These preferences use the persisted shell presentation contract and the same
revision-bound actions exposed to agents. They do not alter accounting,
execution, child attribution or provider requests. A theme change does not
rewrite the choices. The optional compact conversation status is off by default.
It shows current conversation status; cumulative usage is read only when the
person chooses **Read usage**, then remains a timestamped snapshot until refresh.
It has no polling interval and does not request usage on each navigation update.

**Copy selected metrics** and **Download selected metrics** project the existing
step/Work usage or `capacity.read` aggregate. JSON includes scope, available call
identity, observation time, coverage and selected metric values/statuses. It
never includes provider request/response payloads, credential fields or private
reasoning text. Reasoning tokens are a reported count. Child identity is retained;
worker aggregates are never added to their constituent calls a second time.
Cancelled/failed calls retain their recorded usage. Live elapsed time is marked
live; settled duration requires a recorded end. Partial and estimated values,
reported zero, missing values and pending reports remain distinct.

The current telemetry supplies tokens, cache reads/writes, reasoning-token
counts, attributed cost and lifecycle timing. The host does **not** have a
normalized current context-window token measurement, queue duration or
provider-internal retry count. The status details explicitly report those limits.
Cumulative input, context character counts and a configured budget are not
substituted for current context-window health. No new telemetry collector,
provider price table or historical scan is introduced by this presentation.
