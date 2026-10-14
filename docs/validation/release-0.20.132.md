# Release 0.20.132 qualification

Base: `6e7183a1c` (main after #636 and #638). Application instances and tests
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

## Checks

- 135 focused tests: delegate exchange, browser details, conversation navigation,
  and event-log reconstruction.
- 85 release identity, note and qualification tests.
- 681 frontend unit tests.
- Production browser: new work-activity paging regression and existing delegate
  activity journey (ownership, nested calls, lazy exact response, focused history,
  desktop/mobile layouts, recording controls). Chromium uses the existing DTU
  single-process flags.
- Production assets rebuilt twice with matching hashes.

These are affected checks, not a new full Python run. The separately merged #636
contains its own installed/native/browser evidence; this release includes that
main change. Its stale-write protocol is cooperative and does not cover arbitrary
shell commands, external editors, custom writers or separate app data homes.

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
