# Optional peer rates and complete watch delivery

This slice builds on optional inbox policy (#1367) and current reply-revision
handling (#1368). It changes only the pair-message rate and per-target watch count
and makes watch lifecycle observation complete across bounded read pages.

- `maxPeerMessagesPerMinute` and `maxPendingWatchesPerTarget` default to 0.
  Positive saved settings still reject new requests before effects; exact retries
  return the original receipt. Zero skips quota-count queries.
- All watches present at lifecycle-event capture are considered in 64-row keyset
  pages. Status, workspace, time and row high-water are captured once. Later
  registrations wait for a later event. Startup also reads bounded pages.
- Existing Stop, ownership, expiry, cancellation and unknown-delivery behavior is
  unchanged. Watching does not start its target or satisfy a work dependency.

## Qualification

In an owned isolated Linux environment:

- 70 focused Python tests passed in 95.90 seconds: seven new policy/pagination
  cases, adjacent reply/watch lifecycle tests, and execution-policy validation.
- One adjacent configured notify-rate test passed in 1.10 seconds after replacing
  its retired constant override with the supported optional policy. This is a
  distinct test, for 71 total observed passes.
- Two new regressions failed against unchanged pre-change application source:
  the 33rd same-minute message and 65th pending watch were rejected.
- The frontend build passed. The built Settings browser passed positive and zero
  settings, lost-acknowledgment readback, stale-writer refusal, narrow layout and
  cold reopen. Its settings fixture uses synthetic resource observations.

The new watch cases exercise 130 real registrations, both status-change and idle
conditions, a later registration during observation, a clock/status change during
pagination, nonmatching first pages, expiry, restart, requester Stop, and a retained
unknown notification whose receipt remains byte-identical. SQL checks require
bounded page reads; no unbounded receipt list is needed.

These are scripted-runtime and built-browser checks, not live-model efficacy,
production adoption, or a claim that unlimited work can exceed physical resources.
No new scheduler, durable event store, automatic rejection retry, dependency
readiness rule, or proof/payload-size relaxation is included.
