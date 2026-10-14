# Release 0.20.131 qualification

Base: `a513a1776fc29234b84c2ab619b7624832281736` (0.20.130).
Application tests and browser instances used synthetic state in the owned
`dtu-unified-public-readiness-20261008`. No shared installation was changed or
restarted.

## Reproduced navigation failure

The shell command queue awaited its post-command navigation refresh. A slow
refresh therefore held subsequent selections even after the previous selection
had committed. Refresh promises also recursively awaited later invalidations,
allowing continuous updates to extend a caller's wait indefinitely.

The production-browser test holds `/api/shell` open and makes two real chat
selections against the synthetic host. On the released 0.20.130 assets, the
second selection never reaches the server and the test fails. With the fix, both
selections are accepted and the second chat's messages and draft are visible
before the held shell response is released. Authoritative selection ordering and
dirty-renderer barriers remain enforced; shell projections refresh independently.

## Other bounded improvements

History loading snapshots only the file reader's identity and paging inputs,
excluding unrelated execution, configuration and worker payloads. Tests exercise
both the initial 100-message window and loading the preceding 100 messages.

Execution reconstruction groups foreground nodes by turn once, and uses lookup
and binary search for ordered native message anchors. Unsorted windows and
duplicate indices retain their existing presentation semantics. For a frozen
synthetic 12,000-action/600-turn fixture, median reconstruction time over three
runs fell from 322.2 ms to 100.3 ms in the same DTU. This excludes file parsing and
is a component measurement, not an end-to-end live latency claim.

## Checks

- 681 frontend unit tests passed.
- 212 affected history, selection, execution, index-retention and detail tests
  passed; 157 release-note, persistence, demand-loading and attribution tests
  passed; 100 shell, client-isolation and release-policy tests passed.
- Production browser: delayed/failed/rapid selection, preserved drafts, and
  selection while shell refresh is stalled passed. Initial optimistic paint was
  120.1 ms in this test run, before the deliberately held server response.
- Draft switching and cross-chat send isolation passed. Dirty renderer input and
  its pending dirty declaration continued to block destructive navigation.
- Chromium used single-process mode in this DTU. The dirty-renderer validator
  initially failed to launch its GPU subprocess; the same test passed with a
  test-only Node preload supplying the same single-process/no-GPU flags to its
  nested validation browser. No validation result was mocked or bypassed.
- Production assets were rebuilt twice with identical file hashes.

## Remaining investigation

The user confirmed reloading the browser after updating to 0.20.130. Read-only
localhost probes on spark-1 still reproduced intermittent server delay, including
a 47-byte build response with 6.02 s to headers and 7.27 s total. A navigation
index request took 1.74 s while its reported handler time was 4.15 ms. Those
measurements exclude a browser connection limit as the sole explanation.

Two nonblocking external profiles identified history parsing, reconstruction,
JSON encoding and persistence. Their sampling read errors and varying workloads
mean they do not establish precise time attribution or a controlled before/after
comparison. The server latency work remains tracked in #633; this release does
not claim all live delays are fixed.

0.20.130's post-release installed-package and full Python lanes passed. Its
browser and readiness lanes failed on outdated/ambiguous browser assertions,
tracked in #637. Publication and exhaustive verification remain separate, and
the next published revision will receive its own post-release run.
