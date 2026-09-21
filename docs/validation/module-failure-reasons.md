# Safe configured-module failure diagnostics

Companion to Core PR112 and loop-live PR5. Core emits an optional reason_code; loop-live retains a fixed allowlist; Unified sanitizes the worker boundary again and uses fixed remediation text. Historical or unknown reasons remain unknown. No exception text, paths, source URLs or credential fields are copied into the diagnostic.

The private module-load-failures.json file and persisted application state retain safe categories. Reopened native sessions can inspect the bounded file without reproducing the failed work. A successful mount clears the current diagnostic. Conversation details render the remediation directly, without another single-line disclosure.

Validation: 52 focused diagnostics/runtime/health/warmup tests, including real worker IPC, private-file permissions, raw-field exclusion, reload, native history inspection and successful recovery; 213 frontend tests; rebuilt packaged assets. Core's companion has 44 local loader/init checks; loop-live has 72 passing tests plus one skip and four passing platform/Python CI jobs. This branch adds no dependency pin or release version. Full reason delivery requires the reviewed Core change; older Core is compatible but yields unknown.
