# 0.20.172 integration qualification

Candidate includes #819, #820, compact peer messages (#676), assistant reactions
(#745), independent execution metrics (#293), and the reviewed-correction portion
of #294. The original GitHub title/body replacement requirement remains open.
The user-named Readiness Manager proves same-workspace task creation already
works; cross-workspace creation in #786 remains a separate product decision.

All local application/test processes ran inside the project's owned spark-2 DTU
with synthetic state. Spark-1 was read only for the named historical evidence;
no live update, restart, model request, or GitHub issue mutation was performed.

## Combined candidate

- Locked dependency dry-run passed; reused the unchanged owned dependency set.
- Full frontend suite: **1,008 passed**, zero skips, 20.56s.
- Changed integration/registry/release-note checks: **64 passed**, 19.52s.
- Production build: **0.20.172 / ff8356316c069bb9**, 9.61s. A second build produced
  identical hashes for every generated file.
- Built peer/reaction browser passed exact clipboard hashing, deferred payload
  reads, source attribution, independent reactions, reload, older/focused pages,
  keyboard controls and 320/390px light/dark layouts at 200% zoom.
- Built observability browser passed persisted scope/metric choices, selected
  copy/download equivalence, real model rows and narrow layout. Explicit usage
  read: **254ms**, exactly one request and no background polling.
- Integrated feedback browser passed original/previous/latest/proposed review,
  explicit confirmation, concurrent maintainer-content preservation, independent
  client drafts, reload/retry, unchanged original chat and new selected export.
  This scenario serves candidate React through Vite and intercepts GitHub calls;
  it is not live GitHub write acceptance.

Independent reviews covered authority, exact intent, private client scope,
canonical accounting, partial/unavailable states, bounded reads, final integration
conflicts and registry ownership. Original frozen inventory/scenario hashes stay
unchanged; the extended plan now contains 78 scenarios and the source-browser
ownership union contains 121 scripts.

## Focused evidence retained from constituent work

- Peer/reactions: 127 Python and 12 client tests. Combined frontend run includes
  the updated peer renderer expectation; the old paragraph/full-row expectation
  correctly failed after the deliberate compact presentation change.
- Observability: 41 Python, 44 client and legacy usage/Chat info browsers. No new
  accounting accumulator or inferred context-window/retry/queue measurement.
- Feedback: 37 Python and 20 client tests, plus full HTTP/SSE browser acceptance.
  Unknown outcomes and consumed review receipts remain non-replayable.
- #820: exact-head focused checks and eight previously unreached readiness tail
  scenarios passed before merge. Broad readiness continues independently.

Raw logs, selected exports, screenshots, source hashes and cleanup receipts are
retained under the task workspace's `output/decision-queue-20261016/`,
`output/observability293-20261016/`, and `output/feedback294-20261016/`.

## Limits and routine ownership

The ARM Chromium single-process workaround is specific to this DTU. Physical
macOS/Windows prompts, native clipboard behavior and paid-provider behavior are
not claimed. Browser timing is a local regression baseline (target <500ms for
the synthetic explicit usage read), not a production-scale latency guarantee.
The peer fixture permits the actual isolated shell-query helper but rejects all
model/provider starts; one helper and zero model starts were observed.

The existing publication path owns fresh installed-wheel/native-runtime checks.
Broad Python/browser/platform checks run after publication and their failures
remain actionable follow-ups, without delaying the foreground release loop.
Publishing never authorizes automatic spark-1 adoption.
