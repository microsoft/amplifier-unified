# Appearance preview close/reopen qualification

[Public readiness run 39373993744](https://github.com/microsoft/amplifier-unified/actions/runs/39373993744)
failed on source `0fd4cd23538191be718266e95a32fd70bf22546b`. Immediately
after clicking Close panel, the preview journey called its Settings opening
helper. The helper timed out reading `data-compact` from a panel that was gone.

The helper may recognize the old panel while its asynchronous close is still
finishing. The fixture now requires the public local view to show `panel: null`
and the Settings component to be absent before opening the successor.
It also checks the close command's accepted receipt after releasing the old
preview. Waiting for that receipt before release would deadlock the test's
intentionally held command queue.

The original before-delivery, after-acceptance, A→B→A, successor-preview,
saved-library, draft-preservation, unchanged shared appearance, no-send and
browser-error assertions remain. No product, helper, timeout or package changes
are included.

## Evidence and limits

Qualification used source `50b566fccebd4fec76e78cce3382e414fa9f0321` plus
the fixture correction, with committed 0.20.184 assets. Every run had its own
network and mount namespaces, temporary directory, synthetic state, and browser;
the existing development previews were preserved.

- The unchanged local journey passed. A delayed close HTTP request alone also
  passed: local optimistic closure need not await the server.
- A diagnostic scheduler deferred delivery of the actual Close click, let the
  unchanged opening helper recognize the retiring panel, then completed the
  close before the helper's next read. It reproduced the same 30-second
  `browser-settings.mjs:29` timeout.
- With the correction, the same controlled schedule completed the full journey
  in 11.626 seconds. The normal corrected journey passed in 9.485 seconds.
- All runs, including the expected negative control, finished with zero owned
  residual processes and no forced cleanup.

The scheduler proves the missing fixture transition barrier, not the exact
browser task ordering in the original hosted run. Its diagnostic-only scripts
are retained with the failed and passing receipts under
`output/appearance-preview-transition-20261020`; they are not shipping test
overrides. No live provider, physical-device or production-adoption acceptance
is claimed. The synthetic runtime fixture used copied development dependencies
(Core 2.0.1, Foundation 1.0.0 distribution, Python 3.13.16, Node 22.23.3 and
Playwright 1.63.0); this is browser/host fixture evidence, not qualification of a
native Core worker or a new installed package.
