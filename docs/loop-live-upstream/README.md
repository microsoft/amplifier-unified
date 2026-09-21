# Upstream loop-live

Unified uses [`microsoft/amplifier-module-loop-live`](https://github.com/microsoft/amplifier-module-loop-live)
from `main`. The isolated runtime declares its source in
[`amplifier_web/runtime_deps/pyproject.toml`](../../amplifier_web/runtime_deps/pyproject.toml);
the host's bundle overlay uses the same canonical repository. The implementation
is no longer copied into this repository.

[Original upstream PR](https://github.com/bkrabach/amplifier-module-loop-live/pull/1)
introduced generation completion/input correlation and optional host ownership
with idle parking. Its tests also protect duplicate input receipts across
ownership changes and explicit failure for invalid admission tokens.

The upstream repository owns the generation/ownership contracts and portable
tests. Unified retains real Core/Foundation integration probes for shared
history, warm workers, attachments, canvas, and delegation. The host still
chooses storage, locking, approvals, and when a parked session must reload.

When changing the dependency, keep runtime and bundle sources consistent and run
those probes. Record the resolved commit in qualification evidence; the declared
source tracks `main`. Do not reintroduce a private copy of the orchestrator.
