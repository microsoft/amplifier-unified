# Native bundle-switch diagnostic qualification

PR #832 native CI run `39042372524`, job `117424375119`, failed one case and
passed 210 at source `a3eb8360e96010ee3cf8f89ff015dbf9401099df`.
The real bundle-switch fixture intentionally mounted a broken provider, but
still expected `RuntimeStartupError`. Provider failure isolation now returns the
specific `ConfiguredModuleError`, so the expected rejection escaped the fixture
before its existing rollback assertions. This was a fixture expectation failure;
no production behavior was changed to satisfy it.

The corrected fixture accepts only the canonical typed diagnostic and asserts
the exact module, provider instance, reason and allowlisted guidance. Unexpected
errors and accidental acceptance still fail. Fresh/reaped worker, same-name
module revision isolation, saved history, model/effort/budget, rollback, usable
restored worker, and zero model-call assertions remain unchanged.

The unchanged test reproduced the hosted failure in 6.74 seconds inside owned
DTU `dtu-unified-codex-dev-20261014`. The corrected real bundle-switch case plus
`tests/test_module_failures.py` passed **18 tests, zero skips, in 6.71 seconds**.
Runtime dependencies were the final qualification environment's published Core
2.0.2, Foundation `8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`, loop-live
`08de4d44` and context-simple `db641363`, reused read-only. All fixture state and
sources were private; no model/provider request or live-host mutation occurred.

The native integration lane already owns the real bundle-switch test. Its
coverage now explicitly requires the typed failed-mount diagnostic in addition
to exact rollback; safe-diagnostic tests retain their normal pytest ownership.
Raw hosted failure, unchanged reproduction, corrected results and source hashes
are retained in `output/afk-approved-20261017/native832/` outside Git. The failed
hosted run remains failed; fresh CI must qualify the corrected revision.
