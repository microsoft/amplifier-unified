# Release 0.20.133 qualification

Initial base: `6e7183a1c`; integrated main `121e214d2` (#640 and #641). Application instances and tests
ran with synthetic data in the owned `dtu-unified-public-readiness-20261008`.
No shared host was installed, updated or restarted.

## Reproduction and change

User-authorized inspection of the existing Mac Edge window confirmed 0.20.130
assets, missing work beside the latest CPU-usage replies, and older work groups
with incorrect multi-day durations. Read-only server responses retained 18,973
nodes and 335 turns; the bounded page ended with older delegate deliveries.

Derived delivery nodes were appended after execution nodes. Paging the resulting
insertion-ordered groups let 100 older reports displace newer model/tool work.
Groups now follow their explicit transcript anchor position before bounded paging.
Returned reports describe an instant, do not inherit the parent turn's duration
or failure, and do not invent a timestamp when the imported time is unknown.
An actual manager failure remains on its execution group.

A synthetic regression with 120 retained reports and a later model call fails
with the old paging code in both Python and the production browser. The fixed
browser shows the latest work, opens its actual model detail, and retains it
through reload and earlier-page loading. All group pages preserve each identity
exactly once and lazy report content remains readable.

## Measured causes and changes

Read-only sampling on spark-1 found synchronous JSON/state persistence, repeated
usage rollups and detail hashing, catalog invalidation during tool progress,
and background native event reconstruction. The CPU-usage chat's root native
log measured 995,793,623 bytes, with 16,718 nodes and 161 child histories. Its
parsed index was already 70,894,547 bytes before transcript associations,
exceeding the existing 64 MiB RAM budget. The reader therefore could never retain
that root, and a changed projection reparsed its historical raw events. A
separate low-priority read-only process measured 3.884 seconds for the root's
cold parse alone. This did not install code or modify its canonical history.

The candidate keeps the RAM limit and saves bounded disposable descriptors when
an index is too large or evicted. It validates the canonical source and resumes
from the last parsed byte. Raw provider bodies stay in their existing files,
and exact detail reads retain digest validation. A missing or damaged checkpoint
rebuilds; no checkpoint can cause a model request or tool effect to replay.

Ordinary execution-detail publication now retains the shared navigation catalog
when a semantic guard proves navigation/attention/preparation unchanged. A new
leaf tool reuses existing usage aggregates; a late parent or ownership change
still rebuilds them. Worker matching is indexed. Detailed work summaries and
fingerprints are calculated only after selecting a page or requested group.

Authenticated bounded diagnostics expose operation/lock timing, synchronous
thread CPU time, event-loop delays, checkpoint hits and canonical bytes read.
Copy diagnostics includes them; no prompt, body or credential capture is added.
Static build requests also expose handler timing, which still excludes request
admission delays and static-body transfer.

## Controlled performance checks

Same owned DTU, synthetic state, one active chat, 10,000 catalog entries, 20,000
execution nodes and 1,000 turns. Baseline was `790109eee` before performance
changes. Eight terminal tool updates and three work-page reads were measured;
background test load was present, so these are indicative controlled results,
not production latency guarantees.

| Operation | Baseline | Candidate |
| --- | --- | --- |
| Native-managed chat, terminal tool update | 250–284 ms | 58–71 ms |
| App-owned chat, terminal tool update | 271–312 ms | 87–121 ms |
| Recent work summaries | 168–171 ms | 41–43 ms |
| Maximum scheduling delay during app-owned burst | 313 ms | 81 ms |

A separate 263 MB synthetic native log with 4,000 events forces eviction after
every read. Cold reconstruction took 718 ms. A new reader restored the checkpoint
in 148 ms and parsed zero canonical bytes; appending one event took 174 ms and
parsed only its 116 bytes. Exact old output remained readable. The checkpoint
occupied 396,073 bytes; no parsed index was retained in RAM. The first uncached
read still parses that requested log. Metadata reconstruction and temporary
JSON/gzip allocations remain; this is not a fully disk-paged execution reader.

Existing demand tests cover 23,128 catalog entries with only subscribed/running
chats selected and no repeated catalog walk. A new publication test makes an
unrelated-root iteration fail during repeated tool completion. A 1,000-group
regression makes any visit to the oldest 900 tool bodies fail during recent-page
summary construction. These check work avoided, not only elapsed time.

## Checks

- Full initial performance run: 7,050 passed, 169 skipped, five failed, six
  subtests passed. It ran before the final corrections/checkpoint integration.
  Failures were the legacy no-ID message case (two paths), subscriber routing,
  and two expectations that unrelated clients should receive detail updates.
  Each failed path is included in the passing final affected run below; this is
  not reported as a second full-suite pass on the final source.
- Final integrated source: 580 affected backend/release tests passed, one skipped,
  including checkpoint corruption/replacement/truncation/partial append, exact
  raw output after eviction, active-client routing, indexed history, usage,
  publication durability, fork recovery, assignment integration and draft defaults.
  The separate native-child assignment file was skipped because the optional
  `amplifier_module_context_simple` module is absent in this host test environment.
- 686 frontend unit tests passed.
- Final production browser: work-activity paging, delegate activity, new-chat
  controls/draft reload and workspace switching all passed. Chromium uses the
  existing DTU single-process flags. There are no provider calls in these fixtures.
- Two production builds matched SHA256 file-for-file; build `2482c0ca53ebdcc1`.
- Independent read-only review found no blocking defect in checkpoint retention,
  canonical validation, usage shortcuts or scoped publication.

The two post-release browser failures from 0.20.131 were reproduced as stale
selectors/flows: obsolete Canvas/header/workspace controls and a title selector
also matching a reservation option. Tests now use the current UI and exact chat
rows. Draft persistence is verified by immediate reload rather than requiring a
backend write. Both complete scenarios pass against final production assets.

Final-source benchmark repeat: app-owned tool updates 88–121 ms, summary reads
41–42 ms, burst scheduling delay 90 ms. The checkpoint repeat read 0 old bytes
on restore and 116 bytes on append (149/175 ms). Earlier baseline comparisons
above use the initial matched workload; no live-server speedup is inferred.

## Remaining latency and verification

Real clicks in the user's existing Mac window took approximately 15.2 seconds to
show AHP/ACP messages and 3.2 seconds to return to CPU usage. In the return sample,
only one 63 ms browser long task was observed; two media reads took approximately
5.9 and 6.2 seconds. These are live 0.20.130 observations, not candidate benchmarks.
The window was returned to CPU usage and temporary observers were removed. No
message was sent. Broader server latency remains open in #633.

0.20.131's post-release installed-package and Python lanes passed, while browser
and readiness lanes failed. The new release will receive its own exhaustive
post-release run; publication alone does not establish those lanes are green.
