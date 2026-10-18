# Work readiness during browser reload

This change adopts only the selected-reader notification correction and its two
applicable tests from `06abe0c3bee55e5cb18d38125f4667434dcd873f` on
`fix/interim-work-history`. It does not adopt that branch's separate history or
folding implementation. The baseline is main
`711933cb8f0af031ae1505284073d7fac49c51ad`.

## Confirmed boundary

A new browser document attaches its state stream before registering shell
resources. A Work read completing in this interval previously notified only
registered shell clients. Its internal receipt could be ready/current while the
new document's cached outer frame remained loading. Later cache reuse sees an
already current receipt and does not notify again. An unrelated command or
observation can incidentally repair the display; it is not a recovery guarantee.

The correction includes connected and current selected web readers in the
existing notification lane. Selection and web-client checks remain in force;
the existing shell owner still excludes session-detail queues and invalidates
only that client's cache. No accepted work, event history, provider call,
execution authority, timeout or receipt assertion changes.

## Controlled browser evidence

The owned spark-2 DTU runs the unchanged packaged client with synthetic state and
the real saved-event process reader. An evidence-only variant of the complete
canonical Work journey gates its cached source-stat read across the final
reload, and holds shell registration until that read completes. Read-only
inspection records the internal receipt and each cached browser receipt.

The first control showed the mismatch but passed after an unrelated audit append
caused another read. The discriminating control therefore also holds POST
`/api/view` and POST `/api/actions` until the receipt assertion. Those requests
are unrelated observations/commands; the Work WebSocket, source read, state GET
and shell registration remain available. Held requests are released before the
remaining original journey assertions.

On unchanged main, the full journey fails its original final 15-second
ready/current/recent-events assertion (24.241 seconds total). The internal
receipt is ready while the new browser cache remains loading. With only the
notification correction, the identical diagnostic script passes the full journey
in 9.692 seconds. Neither script changes a receipt result or broad-publishes state
to manufacture recovery. Both runs leave zero owned child processes.

The two deterministic Python checks cover the selected recipient set and actual
WebSocket/HTTP cache behavior, including the prior recipient-set negative
control and unchanged unrelated-client cache/token. The full WorkWindows file
passes 30 tests with zero skips. Ordinary canonical Work acceptance is also run
with the original, uninstrumented fixture and unchanged browser assertions.

## Limits and ownership

The original published .176 run
[39061951410](https://github.com/microsoft/amplifier-unified/actions/runs/39061951410)
did not retain its failed frame; this controlled reproduction establishes a
concrete reachable cause, not the exact ordering of that historical failure.
A separate later branch run `39114616898` failed its initial receipt check with
`source_changed`, not the final reload. That artifact is preserved separately
and is not explained by this notification proof.

Regression ownership remains Full Python checks (`tests/test_work_windows.py`)
and extended-browser `canonical-work`; no inventory counts or frozen migration
fingerprints change. Local evidence under `output/canonical-work836-20261018/`
retains source hashes, diagnostic scripts, both boundary receipts, original
failures, focused JUnit and strict browser cleanup. Source-level fault injection
and tracing are evidence only and are not shipped with the application.
