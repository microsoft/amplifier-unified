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
Short work groups stay visible; groups exceeding 15 action rows can be collapsed.
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
request options such as message/tool counts, limits and reasoning effort. A
Load raw request control reads large recorded `data.raw` fields from the
existing event log only when requested. Short requests load with the expanded
action and appear inline, without a second disclosure. No request body enters initial browser
state or a saved execution projection; only a digest/length reference and bounded
metadata are indexed. This view does not enable or change provider capture. If a
request was not recorded, or parallel requests lack enough identity to associate
one safely, the call reports that no raw request is available.

Native request details are retained when native and app lifecycle telemetry are
combined under one stable call ID, including unambiguous calls still in progress.
Provider identities are checked before merging, and ambiguous parallel calls are
never assigned another call's raw request. Raw requests use the same 15/10-line
preview and full-copy behavior as tools, with cancellation, retry and source
replacement checks. Row and raw-request expansion use the shared `view.update`
path and survive reload.
