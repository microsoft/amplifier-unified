# Worker failure feedback qualification

The feedback projection owns the deliberately small, public-safe reproduction
summary. The runtime reader and session-health projection own the original
failure classification. Feedback must retain the built-in `worker_communication`
and `worker_exit` categories and `WorkerCommunicationError` and
`WorkerExitedError` types without exporting raw error details, custom exception
names, paths, conversation content or correlation identifiers.

This is a reporting correction related to [#842](https://github.com/microsoft/amplifier-unified/issues/842),
not a claim that its historical disconnections have been fixed. It follows
the family vision's truthful recovery principle and the shared clients'
[evidence and feedback principles](../clients/VISION.md).

## Maintained checks

- `tests/test_feedback_diagnostics.py` delivers typed failures through the real
  runtime-event owner, then checks the on-demand browser/agent diagnostic action,
  frozen submission, mocked issue body, and saved receipt after application
  reopen. Reads do not publish or load history. Changed later failures do not
  rewrite accepted reports. Existing custom/malformed-value, size, opt-out and
  content-exclusion checks remain required.
- The existing registered `frontend/tests/feedback-browser.mjs` journey now
  presents a synthetic worker communication failure, opens reproduction details
  through the UI, and asserts the precise safe classification and absence of raw
  detail. It retains its on-demand collection, explicit refresh, opt-out,
  submission/reopen/reload, attachment and narrow-layout assertions. GitHub and
  providers remain mocked.
- `tests/test_runtime.py` retains real subprocess exit-code/type and communication
  cleanup coverage; `tests/test_session_health.py` retains the local category and
  recovery guidance checks. Reporting tests do not establish long-running worker
  reliability or a cause for the reporter's failure.

## Evidence still needed for the original disconnect

The public report shows an open browser event stream and a stopped chat runtime.
Those are capture-time facts; its older failure does not establish browser
connectivity at the time of failure or explain the latest input. The runtime
reader's communication-error handler
also covers normalization and host event processing, so the label alone does not
prove that the worker process died or that its pipe failed.

The handler currently retains a generic message with the exception class, but
does not write a communication-specific receipt with the original stack. For a
recurrence, first retain the local failure detail and timestamp, corresponding
runtime-event tail, matching host logs, process exit status if known, and the
running app/component revisions. If those do not identify the failed operation,
the next investigation needs a bounded private reader receipt distinguishing
read/decode/host-callback phases and stack locations without locals or payloads.
Share evidence privately after inspection;
do not paste raw conversation/provider payloads into a public issue. Confirm the
receipt belongs to the failed turn before connecting it to the latest input.
Without that evidence, keep the underlying disconnect investigation open and
avoid automatic restart or replay as a purported fix.
