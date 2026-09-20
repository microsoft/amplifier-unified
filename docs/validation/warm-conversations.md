# Warm conversations and navigation validation

This change was developed from Unified 0.12.0 and integrated with the current
0.13.0 main (`d12f8d1`). It adds idle-worker
retention and immediate optional preparation, a bounded browser conversation
cache, unchanged-history read avoidance, and scroll-triggered history paging.
It does not change providers, Core, Foundation, or the runtime dependency pins.
Lazy provider loading and interchangeable worker pools remain out of scope.

## Evidence

- Runtime retention tests cover oldest-idle eviction, expiry, active and
  unanswered-operation protection, uncertain transport writes, stale parked
  status, bounded preparation, startup approvals, policy changes while queued, and input crossing
  retirement. The actual worker checks parked state before accepting retirement.
- A real Worker with the pinned runtime and an isolated deterministic provider
  handled two turns in the same instance, retired cleanly, then restored a third
  turn in a new instance. Saved accepted inputs and responses were preserved
  without replay. The integrated run took about 7.27 seconds cold and 0.090 seconds
  for the next fixture turn while warm. These are fixture measurements, not
  model-response or production latency promises.
- The production browser bundle displays cached history and the correct private
  draft while the selection HTTP request is deliberately blocked. Drafts typed
  during that interval survive the response and later switches. Dirty-canvas
  browser coverage preserves the existing edit guard. The integrated cached
  switch painted in about 8.4 ms before the blocked server response.
- Native-history browser coverage uses 5,000 synthetic conversation summaries.
  It checks bounded rendering, typing, automatic upward-scroll paging with a
  stable reading position, and read-only child histories. This does not measure
  Spark's production catalog, filesystem or network latency.
- Readiness settings use the same shared action as agents. Tests cover numeric
  validation, persisted configuration and application to the running manager.
  The production browser fixture passed desktop/mobile layout, save, reload,
  and direct inspection of the actual running manager's updated settings.
- The TUI HTTP contract test prepares an explicit chat without selecting it,
  submits no input, and deduplicates a retried preparation command.

The final backend suite passed 1,177 Python tests (11 opt-in skips), including
the startup-approval and retirement-review corrections. The unchanged frontend
passed 165 tests. Wheel/source builds
and packaged-source/static-asset verification passed. These checks do not
constitute a release or deployment.

## Retirement review correction

An independent lifecycle review reproduced a late-acknowledgement race: after
the retirement wait timed out, the host forgot the intent, reported the worker's
successful exit as an error, and could not resume the next control. Retirement
now has a persistent reconciliation task. The sweep and new command callers
have bounded waits that do not cancel that task or admit work to an owner that
may still exit. A late positive acknowledgement completes cleanup and records
the normal resume information; a late refusal reopens admission to the same
worker. An uncertain write keeps its original reply future. A missing reply
never authorizes a forced shutdown, and an exit without acknowledgement is
reported as uncertain rather than successful retirement.

The correction passed 92 focused runtime, retention, warmup, live-client and
bundle checks, including the five new delayed/missing-acknowledgement cases.
An independent focused recheck also cancelled a caller waiting for retirement,
confirmed reconciliation survived and recorded the late acknowledgement, then
resumed a control without a spurious runtime error. This was a focused review
of the correction, not comprehensive pull-request acceptance. The actual-worker
probe passed again after the correction: about 4.44 seconds cold and 0.102 seconds
for the next fixture turn, with safe retirement and resume without replay.

Repeatable checks include `tests/test_runtime_retention.py`,
`tests/test_session_warmup.py`, `tests/fixtures/warm_worker_probe.py`, and the
frontend conversation-switch, native-history, worker-retention, draft-switch,
and canvas-dirty browser fixtures. The actual worker probe requires the pinned
runtime dependencies and a fresh temporary output directory.

## Memory investigation

Read-only samples on Spark found two quiet Unified process groups at about
432 MiB and 174 MiB proportional set size. The larger group was approximately
217 MiB for the Python worker, 213 MiB for a Copilot helper, and 2 MiB for an SSH
child. Neither accumulated CPU time during a four-second sample; this does not
establish their state over their entire lifetime. A Mac worker had about 174 MiB
RSS after more than six hours; RSS and Linux PSS are not directly comparable.

A partial Spark CLI sample found 40 matching root processes, of which 19 groups
had readable memory accounting. Those groups had a median of about 451 MiB PSS
and totaled about 9.6 GiB. This is not a count of every CLI session on the host.

A fresh Spark process using Unified's runtime, with no session or history loaded,
measured these cumulative PSS values:

| Imported through | MiB PSS |
| --- | ---: |
| Python baseline | 6.1 |
| Core | 21.9 |
| Foundation | 26.4 |
| loop-live | 27.1 |
| OpenAI provider | 46.2 |
| Anthropic provider | 67.6 |
| Copilot provider | 93.7 |

These are cumulative and import-order dependent, not isolated provider costs.
A separate allocation-traced run identified imports, standard-library objects
and schema/type machinery as substantial contributors, but profiling itself
added memory. It does not attribute the full live-worker heap. Tools, working
context, provider state and allocator retention remain additional contributors.

The runtime mounts providers configured for the session, not every provider
that exists. Unified also imports schemas for configured nested-agent providers.
Copilot's helper is not necessarily started merely by mounting: its explicit
subprocess-prewarm option defaults to false. No provider behavior changed in
this work. The default worker count is configurable host policy, not a proven
capacity limit for every machine.

## Remaining boundaries

No existing Mac or Spark service or user preview was restarted for these checks.
Spark production switching latency still requires acceptance after deployment.
Cold history reads still scan the saved working transcript, and scroll-back can
accumulate rendered history in the browser. This does not implement indexed
disk reads, virtualized history, event-log-only recovery, or TUI widgets.
