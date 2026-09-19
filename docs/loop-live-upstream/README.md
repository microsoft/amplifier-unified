# Upstream loop-live

Unified uses `bkrabach/amplifier-module-loop-live` version 0.2.0, pinned to
`3ceb44ee6fb0476461c9b03c4a3e76e866f2f9b1` in both the isolated runtime and bundle overlay.
The implementation is no longer copied into this repository.

[Upstream PR](https://github.com/bkrabach/amplifier-module-loop-live/pull/1)
promotes generation completion/input correlation and optional host ownership
with idle parking. Its tests also protect duplicate input receipts across
ownership changes and explicit failure for invalid admission tokens.

The upstream repository owns the generation/ownership contracts and portable
tests. Unified retains real Core/Foundation integration probes for shared
history, warm workers, attachments, canvas, and delegation. The host still
chooses storage, locking, approvals, and when a parked session must reload.

To update the dependency, change both immutable pins together and run those
probes; do not reintroduce a private copy of the orchestrator.
