# Post-release 0.20.184 Python fixture corrections

The published `959e9bf5371da7e907e18fcf54891a28dbbb18d5` census found nine failures in two stale test boundaries. This correction changes tests and CI ownership only.

- A stopped requester's saved wait is suppressed before a new continuation command or message is admitted. The revision test now checks that closed wait, the missing command, the still-inspectable second reply, unchanged messages and runtime inputs, and the preserved interruption revision. It no longer expects a newly created suppressed command.
- Idle app activation intentionally refreshes public release metadata. Optional-feature restaging fixtures now provide that read-only lookup separately from their installer/probe witness and require the lookup to occur. Existing no-install and no-runtime-close checks before optional-feature rejection remain, as do retained receipts, exact feature selection, busy-host deferral and successful retry assertions.

`tests/test_coordination_revisions.py` now both triggers and runs in the existing **Task coordination contracts** workflow. Both repaired modules remain collected by the full post-release Python census; no skip or timeout was added.

## Qualification

Inside the owned spark-2 development DTU, using private source/state/dependencies:

- Unchanged nine failing cases reproduced on the same published Core 2.0.2 environment: **9 expected failures**, 11.92 seconds.
- `tests/test_coordination_revisions.py`, `tests/test_tui_updates.py`, and `tests/test_pending_app_updates.py`: **76 passed, zero skipped**, 66.72 seconds.
- Parsed workflow confirms the revision module is a pull-request trigger and appears once in its pytest command.

Local evidence is retained under `output/postrelease184-python-20261019`. The initial private environment copied the development baseline's Core 2.0.1; only that copied Core distribution was replaced with the retained canonical 2.0.2 wheel before final qualification. The earlier nine-failure reproduction and one intermediate assertion using the wrong result-envelope field remain retained separately. Restoring the unchanged controls did not alter the qualified test sources.

These are scripted host/protocol and isolated package-probe tests. They do not claim live-provider or physical-desktop acceptance. No application code, package version, built client, shared environment or live instance changed.
