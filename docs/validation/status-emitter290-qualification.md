# Status emitter #290 qualification and smoke additions

Scope: optional local observation of current Core/Foundation Unified and CLI work.
Historical implementation baseline: Unified175 (`c8f5fd897`); the initial source
is the commit that introduced this record. Initial execution used the owned `dtu-unified-codex-dev-20261014` on spark-2, synthetic
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
| STATUS-02 | Run real installed CLI + real Unified worker; external pure consumer reads both | Both report actual held local tool, schema 1 and accepted plan counts; 49 ms CLI tool observation in the historical native probe | Under 1 s locally; same schema, no guessed state |
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

Future execution belongs to the explicit `status-installed` affected-change lane.
Its [prerequisites and reusable invocation](integration-coverage.md#installed-local-status-owner-october-17)
use `scripts/qualify_status_emitter.py`; ordinary unit tests cannot substitute for
the real CLI/worker boundary or independent installed-wheel ownership checks.

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


## Installed release 0.20.176 requalification

The unchanged `status-installed` owner passed against source
`511c2504b350e64b31d31687de548b1f3e5dfc17`, the clean installed Unified 0.20.176
wheel, and published registry **Core 2.0.2** on Linux ARM. Foundation was
`8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`; the real installed CLI remained 0.1.1.
Five genuine CLI default modules were built from recorded immutable sources and
installed into a separate environment clone. The original CLI environment and
all its existing package bytes remained unchanged.

Fresh native acceptance observed the real held local tool through both the CLI
and a Unified worker; CLI observation took **48.53 ms**. The CLI ended cleanly,
and no network model call occurred. Independent package acceptance found zero
overlapping RECORD paths; uninstalling the standalone emitter preserved Unified's
import and command. Strict source/wheel and published-Core byte checks passed,
as did source identity before/after execution. The complete owner took 18.218 s
and left no owned processes requiring retirement. This is a new receipt; it does
not relabel the historical Core 2.0.1 / 49 ms result above.

The qualified Unified wheel SHA-256 is
`2f6c6ea76913cdfda6385ae50c12dd4769004a4f14252c9aff198261d3df61ca`;
standalone emitter 0.1.0 is
`63dabddefce350f384576ff9cd028a69cd5e891ba62acc92e3e3e2aedef2b2a0`.
Raw native/package receipts, exact CLI preparation and dependency identities are
retained in `output/afk-approved-20261017/full-smoke-plan/final-supplements-passed`.
Earlier strict-validator rejection of generated packaging residue and offline
CLI prerequisite failures remain retained as failures before this clean run.

The scripted-provider, local-tool and optional-publication limits above still
apply. These checks do not establish physical-device, live-provider, TUI, recipe
or ACP acceptance, and do not claim registry publication of the standalone tool.
