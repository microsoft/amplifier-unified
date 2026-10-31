# Peer inbox policy and fair delivery qualification

This slice replaces the fixed pending inbox, sender and assignment ceilings with
three optional global Work concurrency settings. Each defaults to 0 (no count
ceiling). Configured positive limits still count queued/submitting/steering inputs
and reserve return capacity atomically. Existing saved limits remain in force;
policy changes never discard work, clear Stop or replay unknown admissions.

## Delivery and scaling boundary

The recipient queue remains the canonical owner. The next sender is selected
from indexed scalar metadata across the entire durable pending set, ordered by
least recent admission and then oldest pending row. Only that sender's next 64
receipts are materialized, in FIFO order. Exhausting a batch of suppressed,
expired or confirmed unsubmitted rows schedules the next bounded pass. An
accepted or uncertain admission ends the pass and is never retried.

Uncapped inbox admission performs no receipt-count queries or native task read.
The regression fixture includes 66 real guarded sends over simulated elapsed
time, a second sender after the first 64 rows, and 71 retained receipts with
64 KiB result bodies. It asserts bounded receipt loading, index use, NULL-sender
progress and exact durable ordering. The separate pair-rate rule remains active.

Relaxing a configured inbox limit schedules existing guarded drains for queued
inputs and backpressured returns. Creation failure notifications retain their
unknown creation result without repeating preparation. Test execution results
remain owned by the existing TestQueue notification pass, already woken by a
settings save; no second test execution is created.

## Qualification scope

Focused Python checks cover optional finite limits, reserved-return accounting,
concurrent admission, Stop, expiry, unknown results, creation and handoff returns,
notification supersession/acknowledgment, settings persistence, and test outcomes.
Two new regression cases fail against the unchanged base: the seventeenth sender
input is rejected, and a second sender is hidden beyond the first receipt page.

The built client settings browser test uses a real application fixture and
controlled resource observations. It covers finite and zero peer settings,
lost-save acknowledgment reconciliation, concurrent-writer protection, preserved
drafts, narrow layout and cold reopen. Generated client assets are built from
this source. This is isolated Linux host/browser qualification with scripted
worker transport, not live provider efficacy, native platform parity, or a
production deployment claim.

## Separate work and integration

This slice does not change the pair-per-minute throttle, lifetime response
revision ceiling, watch registration/observer paging, or canonical Work input
coverage. Those have separate semantics and owners.

The resource-count policy slice changes other concurrency defaults and the same
advanced settings form. Integration must preserve its zero defaults for owned
resources and reservations while adding these peer keys; do not choose either
whole-file version when resolving that overlap.
