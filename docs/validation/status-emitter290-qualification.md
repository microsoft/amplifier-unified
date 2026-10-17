# Status emitter #290 qualification and smoke additions

Scope: optional local observation of current Core/Foundation Unified and CLI work.
Baseline: Unified175 (`c8f5fd897`); new source is the commit containing this record.
All execution used the owned `dtu-unified-codex-dev-20261014` on spark-2, synthetic
state, Python 3.13.16, Core 2.0.1, loop-live 0.2.0, context-simple 1.0.0 and installed
CLI 0.1.1. No provider credentials, live model calls, historical-copy changes,
spark-1 adoption or shared service changes occurred.

The pre-change app had no opt-in cross-host status surface. Current and desired
results are separate below; these rows extend the smoke inventory for deployments
that intentionally enable status publication. Ordinary disabled deployments must
retain their zero-work baseline.

| ID | Journey/check | Current result | Desired regression threshold |
| --- | --- | --- | --- |
| STATUS-01 | Leave publication disabled; construct/close app | No emitter thread or files | Always zero publication work |
| STATUS-02 | Run real installed CLI + real Unified worker; external pure consumer reads both | Both report actual held local tool, schema 1 and accepted plan counts; 49 ms CLI tool observation in final native probe | Under 1 s locally; same schema, no guessed state |
| STATUS-03 | Plan succeeds, fails, receives invalid receipt | Accepted counts only; failed/invalid becomes unavailable; worker normalization preserves redacted receipt | No stale plan represented as newly current |
| STATUS-04 | Validate a bundle without starting it | No discovery thread/file; explicit source is required by CLI resolver | No validation-only phantom session |
| STATUS-05 | Concurrent reads during writes; terminate producer | Atomic private JSON; crash becomes stale, orderly close ends observation | No partial JSON or invented completion |
| STATUS-06 | 300 active sessions plus old idle catalog | At most 256 live observations; stable active map rotates; coverage incomplete, evictions unobserved | No cold hydration, silent omission or false work completion |
| STATUS-07 | Complete work, admit new work, resume same identity | Bounded terminal retention and successful re-admission | Capacity recovers; same identity can become live again |
| STATUS-08 | Malformed file/FIFO and accumulated producers | Bounded read rejects invalid/nonregular entries; exact instance lookup bypasses prior producers | No blocking FIFO open; overflow remains explicit |
| STATUS-09 | Two pending questions, one answered | Remaining question retained; missing identity reports unknown | Never clear unrelated pending attention |
| STATUS-10 | Install standalone and Unified wheels together; uninstall standalone | 0 overlapping RECORD paths; both commands work, Unified survives uninstall | Independent import and command ownership |
| STATUS-11 | Default redaction and explicit CLI plan labels | Default excludes prompt/text/arguments/results; labels only explicit CLI config | Unified labels remain redacted; no secret-bearing payloads |

Executed focused suite: `tests/test_status_emitter.py` and
`tests/test_execution_events.py` (27 tests). Native fixture:
`tests/fixtures/status_emitter_probe.py`. Installed wheel ownership fixture:
`tests/fixtures/status_package_probe.py`. Raw private evidence, exact receipts,
source hashes and resource ownership are retained outside Git under
`output/afk-approved-20261017/status-emitter`.

Failures during development remain in evidence: CLI bundle resolver correctly
rejected implicit source composition; module validation initially created an
unwanted phantom observation; initial overflow rotation did not advance fast
enough. Each led to a source correction and focused regression, not a softened
acceptance criterion. Offline provider auto-detection produced unrelated install
warnings in the private CLI environment; the explicitly configured fixture provider
completed without any remote model call.

Limits: this is one current CLI/Core/Foundation integration with a scripted
provider and local tools, not a live-provider, TUI, recipe or ACP qualification.
No UI workflow changed. Usage/provider metadata absent from canonical events stays
unavailable. The discovery limit is intentionally incomplete at overflow; exact
instance lookup and explicit coverage support honest consumers. Default behavior
is opt-in, and no standalone package has been published to a registry.
