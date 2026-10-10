# Saved-conversation loading

Run the production build, then
`AMPLIFIER_PERF_EVIDENCE=/tmp/history-loading.json node frontend/tests/history-loading-performance.mjs`
inside an owned DTU. The fixture creates disposable native JSONL histories,
uses the real history reader, HTTP/SSE delivery and production frontend, and
rejects any model start or send. It verifies that source files remain unchanged.

The three histories contain 1,000 messages, 10,000 messages, and 10,000 messages
plus 10,000 completed tool calls. Each message contains about 2 KB of Markdown.
Each scenario gets one cold application-cache open and two warm reopens, on
loopback and an emulated remote connection. Remote emulation uses Chromium CDP:
100 ms latency, 10 Mbps download and 2 Mbps upload. The OS file cache is warm
from fixture creation; this is not a cold disk or actual internet measurement.

Timing starts when the browser dispatches `session.select` and ends after the
latest saved message enters the rendered DOM and two animation frames pass.
It does not substitute the appearance of the shell or a loading indicator for
saved content. Server history-load duration is reported separately. Network
instrumentation includes actual SSE frames and decoded API bytes through the
selection's acknowledgment, rather than measuring only `/api/view` requests
(the selected conversation may arrive entirely over SSE).

## October 9, 2026 candidate measurements

Product source: `4acefec39e68862c0669b39909615936ac4392b9`, the locally combined
candidate for PRs #495, #499, #500 and #504. Owned ARM64 Linux DTU, Python 3.13,
Node 22 and single-process Chromium. Eighteen measurements, not a statistical
latency distribution:

| Link | Cold application cache | Warm reopen |
| --- | --- | --- |
| Local | 612–746 ms | 355–1,193 ms |
| Emulated remote | 621–739 ms | 396–603 ms |

Every initial render contained 100 messages. The largest decoded SSE frame was
241,406 bytes; the largest total observed decoded API delivery during selection
was 596,516 bytes. Largest source transcript: approximately 22.2 MB; associated
tool log: approximately 26.8 MB. Original source hashes were unchanged and no
model work started. Frame and DOM bounds are assertions; measured milliseconds
are reported rather than enforced as a machine-dependent CI threshold.

These results do not reproduce the reported intermittent ten-second Mac Mini
loading delay. They do not qualify first-send runtime preparation, cold OS
caches, huge attachment payloads, concurrent active conversations, initial
catalog discovery, full activity expansion, or an actual Mac Mini/remote route.
Those remain separate investigations; these measurements are not evidence
that the user's observed delay is fixed.

Full logs and measurements are retained in the owning workspace's
`output/history-loading-20261009`. The fixture stops its owned server and removes
its temporary source/state directory on normal shutdown and SIGTERM.

## Recheck after the reviewed changes merged

Product source: `b190c3b7777d3d910e381de739b9407550bfa42e`; harness integration:
`5eb56173`. The same eighteen scenarios passed against merged main in the owned
DTU. This is a separate run, not a replacement for the earlier measurements.

| Link | Cold application cache | Warm reopen |
| --- | --- | --- |
| Local | 708–868 ms | 396–705 ms |
| Emulated remote | 652–948 ms | 371–540 ms |

Every initial render again contained 100 messages. The largest decoded SSE frame
was 240,362 bytes; total decoded API delivery peaked at 597,105 bytes. Original
histories remained unchanged and no model work started. Evidence is retained in
`output/history-loading-integrated-20261009`. The limitations above still apply;
this run does not establish that the reported remote Mac Mini delay is fixed.

## Reopening previously expanded native history

The initial-load benchmark did not cover a native history window already
expanded by an earlier visit or another reader. Such a window could bypass
browser message paging. Browser projection now caps passive native messages at
100 independently of the retained server window. Message cursors and source
ordinals preserve access to retained pages and older disk history.

`node frontend/tests/retained-history-browser.mjs` runs the production frontend
against a real 10,000-message JSONL transcript with 1,000 rows already retained.
It checks the initial display bound, navigation to unloaded history, retained
and disk paging, duplicate prevention, an independent reader's history
expansion, and reading-position restoration after switching and reloading.
Source hashes must remain unchanged and no model work may run. Explicitly
loading earlier pages still adds rows during that visit; this is not complete
scroll virtualization.

A separate synthetic loopback comparison reopened the same fully retained
1,000-message transcript. The frozen candidate at `7e73300c` rendered 1,000 rows,
delivered 2,448,365 decoded API bytes, and painted saved content in 2,467 ms.
The repair based on main `57f4f472` rendered 100 rows, delivered 316,988 bytes,
and painted in 637 ms. These are individual samples with warm OS caches in an
owned ARM64 Linux DTU, not latency percentiles or proof about a particular
remote user's route. The largest SSE frame fell from 2,371,018 to 239,761 bytes.
Both runs preserved source history and performed no model work.
