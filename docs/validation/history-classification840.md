# History classification across bounded refresh slices

Post-release 0.20.177 verification reported one Python failure: the automatic
history test expected a saved session to change from root to internal after one
explicit refresh. The run completed with 9,903 passing and 110 skipped tests;
its extended browser, readiness, installed-package, and integration jobs passed.
The original failed run remains recorded in issue #840.

NativeHistory intentionally bounds each reconciliation slice by 64 steps and
25 milliseconds. Its directory mode retains this bound even for explicit
refreshes. Watcher notifications may arrive later; fallback reconciliation must
converge without them. Changing the implementation to scan the complete catalog
in one refresh would contradict the working-set and responsiveness contract.

The unchanged scenario passes in isolation, but fails its original classification
assertion when constrained to one reconciliation step with no watcher
notifications. This reproduces the incorrect one-refresh assumption; it does not
prove the exact scheduling or elapsed slice time in the historical hosted run.

The corrected test advances at most 64 refresh slices for this two-session
fixture and requires the exact expected classification. It exercises both the
normal configuration and a deterministic one-step, watcher-free fallback. It
retains purpose, visible/internal counts, hidden ordinary navigation, readable
saved history, read-only protection, persistence after reopen, and unchanged
canonical bytes/mtimes. It additionally verifies that browsing starts no runtime
and sends no prompt. A permanently wrong classification must still fail.

This is a test correction only; the shipping discovery algorithm, slice limits,
watcher behavior, and application assets are unchanged. The 64-iteration test
guard is a finite bound for this tiny fixture, not a user-facing latency target.

Qualification inside the owned spark-2 DTU passed 138 automatic-history, native
history/stamp, and recovery tests with zero skips. The unchanged one-refresh
assertion failed under a forced one-step/no-watcher configuration. A separate
negative control that permanently classified internal sessions as root failed
both corrected variants, confirming that bounded retries cannot conceal a
persistent classification defect. Product source was restored after this control.
The fresh full hosted Python rerun remains a separate background qualification;
these focused results do not relabel the original 0.20.177 failure.
