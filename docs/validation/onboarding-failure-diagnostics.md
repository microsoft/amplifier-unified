# Onboarding diagnostic follow-up — October 16, 2026

This bounded follow-up addresses two confirmed defects in the onboarding review:
the worker boundary flattened useful bundle failures, and the public CA
fingerprint instructions named plain `doctor`, whose ordinary output does not
print the fingerprint. It does not adopt the peer proposal's new onboarding,
default-bundle, automatic-update, or networking policies.

## Additive smoke coverage

These cases extend the standard UX inventory without changing its frozen counts.
They run with the existing full Python test owner; no new release lane is added.

| ID | Coverage and observed result | Desired behavior and limits |
| --- | --- | --- |
| `SETUP-BUNDLE-01a` | Five typed bundle failures cross an actual subprocess boundary during cold startup and ready-worker remount. All ten cases pass with the fix; all ten fail against unchanged baseline `4504b709` with the generic `worker_startup` category. | Preserve dependency/load/validation, missing-ID and ambiguous-ID distinctions; keep the saved request and private diagnostic; publish no dependency URL, credential or path; duplicate commands never replay the request. The child is a protocol fixture, not live Foundation resolution or full browser onboarding. |
| `SETUP-TLS-01` | Public `cli.main` parsing of `--data-dir … setup-tls status` prints the exact SHA-256 fingerprint of a synthetic existing CA and leaves file contents and modification times unchanged. | The documented host command must provide the value to compare with the transferred certificate. This checks the CLI path, not actual client trust installation or native DNS. |
| `SETUP-TLS-01a` | Rendered setup instructions fit 320px and 1280px Chromium viewports and expose the corrected command. | Maintain readable trust guidance and no horizontal overflow. Owned ARM Chromium uses reduced process isolation for this check; native browser trust remains separate. |
| `SETUP-BUNDLE-01b` | Proposed, unexecuted here: inherited enabled add-on with a broken include through setup assistance and model selection. | Truthful failure, retained input, no implicit disabling or configuration change. The boundary fixture above is only one part of this journey. |
| `SETUP-BUNDLE-02` | Proposed, unexecuted here: explicit repair of the broken add-on followed by an explicit retry. | Change only the selected scope after user action; no automatic widening or replay. |

The final changed-path run passed 56 tests with zero skips. The initial
surrounding regression run passed 114 tests with one existing skip:
the reused Python environment lacks `playwright.async_api`. The separately
executed real Node Playwright check qualified the setup-page layout and corrected
command; it does not turn the skipped Python test into a pass. An initial Node
probe closed its single-process browser when disposing the first implicit
context; the successful probe used one explicit context for both viewports.

The first new test run also exposed a fixture mistake: cold startup correctly
removes the failed worker before the test tried to await its reader. The test now
waits for that reader only in the ready-worker remount case. That attempt is
retained as a test-harness failure, not a product finding.

## Ownership and evidence

The canonical changes are in `runtime.py` and `session_health.py`; bundle
selection and composition remain strict. The trust instructions change in
`README.md`, `docs/DEPLOYMENT.md`, and `setup_page.py`; the supported CLI behavior
is unchanged. Tests are `test_bundle_startup_diagnostics.py` and
`test_tls_deployment.py`.

Qualification runs only in the project-owned spark-2 DTU, with synthetic state,
a private source copy, and read-only reuse of existing dependencies. There is no
live host update, service restart, trust-store change, provider call or new
dependency install. Exact source hashes, commands, before/after logs, screenshots,
resource ownership and cleanup are retained in the workspace artifact directory
`output/onboarding-diagnostics-20261016`. The wider onboarding audit and proposed
unexecuted smoke matrix remain in `output/onboarding-review-20261016`.
