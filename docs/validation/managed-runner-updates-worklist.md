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
(85 checks), followed by the retained configuration regressions. Updated purpose-installed
generations require exact runtime proof even when their source commit was already
app-qualified; genuinely legacy setups keep their original compatibility path. The composed manager cases cover prompt progress,
capacity waiting without the chat lifecycle lock, availability discovery while
waiting, interrupted stage receipts and unrelated failed-tool independence.
Actual Linux Incus and Docker qualification each completed source snapshot,
offline owned execution, sentinel artifact export, stop/removal and future-only
selection. Native observations confirmed the Docker container, volumes and
network absent; the original Incus adapter reported authoritative absence. Setup
origin was a private controlled receipt fixture; installation, candidate library,
transport, backend, durable jobs, artifacts and cleanup were real. No host setup
or production configuration was changed. A first Docker attempt exposed an
inspector representation bug, rejected before the check, and cleaned up. Its
failure was retained; a separate owned attempt passed after normalization of
native capability names and exact named-volume bind verification.

The built, mounted global Updates UI passed desktop and narrow browser checks
for capacity wait, unknown outcome, cold reopen, failure and exact Retry/Skip
result IDs. Native Windows automatic generation promotion remains unsupported;
passive availability checks return a truthful not-checked state without POSIX
file calls. Linux and WSL behavior is unchanged. macOS and native Windows backend
lifecycle qualification are not claimed.

Runner qualification is independent of the ordered app/component sequence, not
just its availability checks. A capacity wait or unknown runner result must keep
its exact owned attempt while manual/automatic app installation, restart
continuation, and included/other component updates proceed through their normal
guards. A late runner result must not overwrite that sequence's phase, errors or
inventory. Already-owned runner selections are excluded from fresh component
inventory to prevent duplicate staging. Ordinary Smart Tool batches retain their
existing idle gate. The maintained `smart-tool-updates` browser scenario covers
the visible newer app action and release notes during a runner wait, reload,
retained runner receipt, and the app's subsequent idle wait on narrow/desktop
clients. Composed backend regressions cover both automatic/manual admission,
continuation after manager reload and actual local component promotion.
The refresh journey also holds a runner observation in flight: one click must
remain queued and discover the new app after that observation finishes. Idle
notifications must not continuously wake a capacity-only runner; ordinary
tool, app and component idle wakeups remain covered separately.

Qualification does not consume ordinary environment-count or retained-owner
quotas. Both the core probe and private Incus engine probe use their existing
exact update owner and physical-host reservation, with count ceilings disabled
only for that private admission. Storage thresholds, memory/CPU pressure,
reservation accounting, immutable backend identity and unknown-effect recovery
remain mandatory. Explicit ordinary-work policy is neither changed nor bypassed
by a public action parameter.

Selection changes cancel future qualification effects and reconcile only the
original owner. Unknown start/cleanup results retain that owner without replay.
Code retirement is limited to purpose-installed candidates no longer selected,
needed for the single rollback, or owned by unfinished work; existing owner and
connection deletion guards apply. Receipts and user artifacts are preserved.

Incus engine and registered-target updating is a separately owned implementation
using the same fixed dispatcher contract. The common candidate does not yet add
those sources to inventory; existing qualified engine pairs remain unchanged.
