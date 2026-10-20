# Managed runner updates worklist

Status: source candidate implemented and under independent review; separate from the urgent runtime preparation
repair. No production installation or policy changes are part of this work.

1. Preserve moving update policy separately from exact installation/configuration
   identity. Migrate only positively evidenced app-generated setup pins.
2. Integrate runner-specific staging into existing global updater attempts.
   Ordinary MCP qualification is insufficient for a library-only runner.
3. Install candidate code separately, retain its source/dependency identity and
   run the actual adapter snapshot and offline owned lifecycle on the configured
   backend. Persist intent before effects; unknown outcomes retain ownership.
4. CAS-select qualified code only for future work. Keep old package and
   configuration receipts for accepted jobs, recovery and rollback.
5. Apply the same receipt contract to Incus and registered targets only where the
   target has independently qualified the exact candidate. Do not weaken static
   package validation while implementing this path.
6. Cover explicit pins, stale selection, lost responses, restart, changed package
   bytes, failed/unknown cleanup and old-owner access with focused tests. Run a
   bounded owned backend proof before publication.

Immediate currency remains the separately qualified 0.6.4 snapshot. It does not
by itself implement future automatic runner updates. A scope that remains
unimplemented must be reported explicitly rather than disguised as an
app-qualified permanent exception.


Current evidence: the focused runner/batch/Smart Tool/sequence checks passed
(72 checks), followed by 35 runner boundary checks after adding native isolation
and retirement coverage. The composed manager cases cover prompt progress,
capacity waiting without the chat lifecycle lock, availability discovery while
waiting, interrupted stage receipts and unrelated failed-tool independence.
Actual backend lifecycle and rendered UI acceptance remain pending at this
source checkpoint. No live Docker qualification is claimed.

Selection changes cancel future qualification effects and reconcile only the
original owner. Unknown start/cleanup results retain that owner without replay.
Code retirement is limited to purpose-installed candidates no longer selected,
needed for the single rollback, or owned by unfinished work; existing owner and
connection deletion guards apply. Receipts and user artifacts are preserved.

Incus engine and registered-target updating is a separately owned implementation
using the same fixed dispatcher contract. The common candidate does not yet add
those sources to inventory; existing qualified engine pairs remain unchanged.
