# Bounded discovery in the private-client recovery fixture

The private-client cache-recovery test establishes a saved native chat before
removing its disposable catalog cache. Native discovery remains bounded even
for explicit refresh, so one completed refresh call does not prove that the
fixture chat has been discovered.

The readiness lanes for 0.20.200 and 0.20.201 failed while dereferencing that
initial fixture row, before their cache-deletion and private-selection assertions.
The unchanged case passed in a focused isolated run. A controlled one-step
recovery pass without watcher hints reproduced the same missing-row boundary:
refresh returned without errors while reconciliation was still running.
This establishes a reachable setup assumption, not the exact timing of either
hosted failure.

The test now advances canonical discovery until the known fixture row exists,
with a maximum of 64 passes and an explicit failure if it remains absent. The
same helper establishes expected rediscovery after restart. No sleeps, eager
catalog scan, fabricated row, or application behavior change is introduced.

All four existing outcomes also run with one-step recovery and no watcher hints.
The assertions for partial/unavailable coverage, private selection, draft and
Canvas retention, navigation, confirmed absence, explicit deletion, and absence
of runtime start/send remain in the maintained test. The navigation case waits
for actual rediscovery before proving that the user's newer selection stays put.

Regression ownership is `tests/test_live_clients.py` in the existing public
readiness selection and full Python lane. The separate canonical Work browser
timeout tracked in #961 is outside this correction. Historical failed runs are
not relabeled by later qualification.
