# Installed media failure evidence and bounded cleanup

The final 0.20.176 installed-package run at source `511c2504b` exceeded its
unchanged 300-second scenario deadline in `gif-canvas-errors`. The retained
failure points to that phase's unbounded cleanup receipt wait; it does not
identify the original failing assertion. The host had accepted the earlier
inspection and dirty-view declaration, but no GIF open receipt was observed.

An isolated diagnostic run and a completely unchanged full installed rerun both
passed against the same wheel and packaged client. The original intermittent
failure is therefore **unreproduced**. No application cause or product fix is
claimed.

The fixture now bounds the queued-receipt and cleanup waits at its existing
30-second receipt limit and saves the primary failure before cleanup. It releases
the held command, settles its receipts before unregistering the route, and avoids
queuing additional app commands behind an unsettled receipt. A failed cleanup
remains a failure; the exact owned page is closed and removed from fixture
bookkeeping. The original primary exception remains the reported phase error.
All dirty-view, FIFO ordering, no-mutation, decoder readiness, frame disposal,
draft preservation and endpoint assertions remain in place. Neither the
scenario deadline nor any application behavior changed.

The negative control preserves real queued dispatches but intentionally leaves
one fixture receipt observation unsettled and raises a distinct primary error.
The old fixture loses that error to a route-cleanup failure. The corrected
fixture must retain the primary error, report the cleanup deadline, close the
owned page, and exit failed with no forced child retirement. This control is
failure-path evidence, not a passing media acceptance result.

The existing installed inline-media owner in public readiness remains the
repeatable full-flow check. Diagnostic forks, forced controls, command identities,
phase receipts and process cleanup evidence are retained separately under
`output/afk-approved-20261017/installed-media832/qualification.json`; they are not
new application modes. Physical keyboard zoom, native transport controls and
visibility checks still require their separate headed-device acceptance.

Final evidence: the unchanged full installed journey passed in 57.16 seconds;
the corrected full journey passed in 56.019 seconds with `coreChecks: PASS`,
zero forced process retirement and zero remaining owned processes. The corrected
forced-failure control exited 1 in 39.232 seconds, retained its exact primary
error plus the 30-second cleanup failure, and reported `pageClosed: true` with
successful final teardown. All runs used the unchanged candidate wheel
`2f6c6ea76913cdfda6385ae50c12dd4769004a4f14252c9aff198261d3df61ca`
and client build `b931a4d86a1c52fe`.
