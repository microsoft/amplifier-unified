# Recent Work receipt recovery

The full browser census at `4b6c7c48801457c4ccf138bd12cf19d810813a31`
observed `execution-inspection` waiting for a current `recent-events` receipt
while the app exposed retrying status and no receipt. That run's log does not
identify the exact worker failure. Its failed result remains preserved.

Deterministic source controls identify two actual defects: publication invalidation
during an unchanged cached-source probe was classified as worker failure, and a
later quiet cache reuse could report ready/current after losing its receipt.
All nine new invalidation, real-failure and owner/source/fence supersession cases
fail on unchanged production source. The bounded correction distinguishes an
explicit superseded read, preserves real error backoff, and keeps the accepted
receipt with the existing eight-page cache. Reads do not execute or replay work.

In the owned spark-2 DTU, the corrected WorkWindows, event-window and resident-page
suites pass 65 cases without skips. Two additional focused regressions cover
session removal before task admission and explicit latest-page replacement.
The unchanged built-client `execution-inspection` scenario passes its original
receipt, live/terminal Work, retry, copy-denial recovery and reload assertions.
The new deterministic cases run through Full Python checks; the existing extended
browser scenario owns the real UI flow. No timeout or product assertion changed.

Evidence is retained under
`output/afk-approved-20261017/work-receipt832/`. The browser uses synthetic
canonical events and the existing ARM Chromium launch adapter. This proves
bounded display recovery, not live-provider timing, exhaustive history, or the
precise cause of the earlier census failure.
