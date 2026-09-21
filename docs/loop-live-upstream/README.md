# Upstream loop-live

Unified follows `microsoft/amplifier-module-loop-live@main` in both the isolated
runtime and bundle overlay. Validation records the exact resolved revision; it
does not replace the source declaration with a commit pin.
The implementation is no longer copied into this repository.

[Upstream PR](https://github.com/bkrabach/amplifier-module-loop-live/pull/1)
promotes generation completion/input correlation and optional host ownership
with idle parking. Its tests also protect duplicate input receipts across
ownership changes and explicit failure for invalid admission tokens.

The upstream repository owns the generation/ownership contracts and portable
tests. Unified retains real Core/Foundation integration probes for shared
history, warm workers, attachments, canvas, and delegation. The host still
chooses storage, locking, approvals, and when a parked session must reload.

When validating an updated dependency, resolve the configured source and run
those probes; do not reintroduce a private copy of the orchestrator. The PR link
above records the original contribution before the Microsoft repository migration.
