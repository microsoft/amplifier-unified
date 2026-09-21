# Configured-module failure diagnostics

Core emits optional reason codes; loop-live retains a fixed allowlist; Unified preserves the categories across worker IPC and presents fixed remediation text. Unknown or older-Core reasons remain unknown. Raw exception details, paths, source URLs and configuration are not copied into this diagnostic.

The worker writes its private current report in runtime-reports/<session>/module-load-failures.json. Conversation inspection reads that exact location, with a fallback for older native-side reports only when no current report exists. An explicit cleared current report wins over any older diagnostic, including when successful preparation creates the first current report. Successful preparation clears the current file; ready status clears persisted and cached inspection categories. No work is replayed and strict module validation remains enabled.

Reconciled against current Unified main including #64, #65 and #66. The current Chat details layout retains inline diagnostic content without restoring its removed nested disclosure. Packaged UI is rebuilt from combined sources.

Independent validation:

- 74 focused backend/runtime tests pass, including real worker IPC, persisted state reload, older-Core fallback, private file permissions, native-history fallback, current report precedence and clearing.
- Four real runtime cases pass using Core112 Python source plus installed Core1.6.1 native kernel, Foundation406 source and merged loop-live b9bad894: update probe, fresh worker, resumed worker, and invalid-hook worker failure through recorded inspection. Provider/capture hook are synthetic, dependency installation is disabled in the provisioned disposable runtime, no model/network calls occur, and saved history/shared cache are preserved. This is not a native kernel rebuild or production activation.
- The invalid-hook inspection case failed before the report-location repair: the worker emitted the correct category but inspection returned an empty list. The unchanged regression now passes.
- 221 frontend tests pass. Two real Chromium runs cover ordinary provider-failure and module-failure Chat details, copied diagnostics, desktop/mobile bounds, naming and recovery-copy preservation.
- Packaged frontend build, undefined-name/static correctness checks and diff whitespace checks pass. The repository uses compact multi-statement formatting; a blanket Ruff style pass is not claimed.

No dependency pin or version change is introduced. Foundation406/Unified66 fix cache ownership separately; this change explains failed module loads. Source validation does not establish installation or live-host recovery.
