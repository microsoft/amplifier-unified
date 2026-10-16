# Integration test ownership

The ordinary host suite may omit optional runtime dependencies. That does not
qualify those integrations. `scripts/integration-coverage.json` assigns every one
of the 87 skip records audited from revision `d1735f82` to an execution owner.
Three records skipped entire modules, so 87 is not the number of test cases.

## When checks run

- **Affected pull requests:** `Owned integration checks` selects lanes using the
  manifest's changed-path rules. Shared runtime/dependency/runner changes select
  every automated lane. Documentation-only changes do not launch integrations.
- **After publication:** `Post-release verification` calls every automated lane
  against the immutable published revision. Its report includes the integration
  result; failed, cancelled, missing, or skipped jobs cannot count as success.
- **Manual:** the same automated matrix can be dispatched on a selected ref.
  Installed-PWA, optional M365 overlay, and unpublished profiling checks have explicit manual owners
  below. Neither is implied by a green headless or host-only run.
- **Publication:** the existing short native-runtime group remains the owner of
  ten audited opt-in cases. It now verifies its JUnit report for skips and missing
  required selectors. It is not rerun as another integration lane.

There is no new nightly schedule or exhaustive release gate. Broad verification
runs in the background; publication and verification completion remain distinct.

## Accountable checklist and initial results

These IDs extend the standard UX smoke inventory with integration prerequisites.
All automated lanes require **zero unexpected skips**, zero errors/failures, and
execution of every declared selector. Missing packages, source permissions,
servers, browsers, displays, or filesystem behavior fail their designated owner.

| ID / owner | Coverage | Initial owned-DTU result | Desired result |
| --- | --- | --- | --- |
| IC-01 native | Real Core, Foundation, loop, provider serialization, context, native jobs, kernels, processes, attachments, terminal, controls, warm/history workers | 77 passed, 0 skipped; 35.50s execution with clean local loop source | Same coverage on affected PRs and after every release |
| IC-02 release-runtime | Existing guidance, child/recipe lifecycle, module cache group | Release 0.20.167: 109 passed, 0 skipped in the existing group | Keep the existing short publication owner; reject new skips |
| IC-03 browser-headless | External-link authentication, service-worker forwarding, narrow/wide setup renderer | 3 passed, 0 skipped; 5.91s | Reproducible disposable Chromium on affected PRs and post-release |
| IC-04 browser-installed | Real installed-PWA launch and entry behavior | Not qualified by this Linux/headless run | Explicit installed-app/display acceptance for changes to PWA launch/install/entry behavior |
| IC-05 cli-interop | CLI → Unified → CLI history, ownership, provider settings and portability | 3 passed, 0 skipped; 4.15s with freshly prepared CLI environment | Same execution with recorded CLI and loop revisions |
| IC-06 filesystem-aliases | Concurrent create cannot overwrite case/Unicode filename aliases | 3 passed, 0 skipped on GitHub macOS; Linux prerequisite probe correctly rejects non-aliasing filesystem | Both alias variants execute on macOS; a skip fails |
| IC-07 uv-reset | Actual package corruption/missing-dependency repair using uv | 1 passed, 0 skipped; 0.62s in fresh prepared environment | Repeat on reset/dependency changes and post-release |
| IC-08 context-intelligence | Real isolated server/database, authenticated graph routing and content redaction | 1 passed, 0 skipped; 2.96s | Real disposable Neo4j + server, no production endpoint or account |
| IC-09 connected-documents | Native upstream composition, shared UI/agent invocation, policy and idempotence | 2 passed, 0 skipped; 1.62s against explicit local upstream copy | Manual optional-overlay acceptance; synthetic Graph transport, no live account writes |
| IC-10 profiling | Real optional capture library, revocation, privacy, retention and receipts | 10 passed, 0 skipped; 10.28s against clean local library `8edea47c3a05eb4e6206211ac993a0a673245b8a` | Repeat against an explicit clean source; automate when canonical source distribution is available |
| IC-11 Stories joined | Actual worker storage → real Stories MCP import/retained read → native MCP App review/export/reconnect | Passed against explicit source and supported Agent; separate fresh-install defect repaired upstream | [Manual optional-integration owner](stories-joined-acceptance.md), required on affected changes/source adoption; missing prerequisites fail, never skip |

The initial [GitHub matrix](https://github.com/microsoft/amplifier-unified/actions/runs/38873044197)
passed all six automated lanes: 88 tests, zero skips, including the macOS cases.
Each lane recorded unchanged source throughout execution.

Times in the table are test execution observations on spark-2, not CI provisioning targets.
These native/provider tests use deterministic offline providers; they do not
prove paid-provider latency, real Microsoft 365 authorization, or live Office
client acceptance. Their integration claims concern the actual native modules
and host boundaries exercised by the named tests.

### Terminal lifecycle regression, October 16

The PR812 native run `38968575306` timed out waiting for its stop-test provider.
The fixture sent that new-turn input after seeing assistant text but before the
previous generation finished. A delayed assistant-event observation reproduced
an explicit held-steering receipt; it was not evidence that stop had failed.
The fixture now waits for the terminal client's idle snapshot and asserts an
accepted, non-steering receipt before waiting for the provider. The actual
two-client detach, duplicate delivery, reconnect and cancellation checks remain.
The existing IC-01 native lane owns this check; no additional lane or retry is
added. Normal execution and the same 750ms delayed-event probe passed locally;
the original hosted failure remains recorded separately.

## Running an owned lane

Use an owned disposable DTU locally, with private state, dependencies, services,
ports and browser context. CI uses disposable hosted runners. Never point these
checks at an active user service. Source copies must be clean and revision-bound.

The workflow checks out the declared dependencies under `.ci/`, then invokes:

```sh
python scripts/run_owned_integration.py --lane native \
  --work /owned/new-lane --output /owned/evidence/native
```

The helpers prepare prerequisites without changing the shared development
preview. The common runner retains JUnit plus a result receipt, hashes source
before and after execution, rejects changed inputs, removes stale result files,
and rejects collection skips as well as per-test skips. It verifies selector
presence, not just pytest's exit status. Preparation records include resolved
sources and installed packages; the native lane also retains its lock/project.
Only evidence is uploaded, never private homes, caches, credentials or user data.

For installed-PWA acceptance, use `integration_clients.py prepare` and
`preflight` with `--lane browser-installed`, a new private state directory,
`AMPLIFIER_TEST_INSTALLED_PWA=1`, and an actual display. Pass the prepared
interpreter/environment to `integration_checks.py run --lane browser-installed`.
A headless substitute does not satisfy this owner.

For profiling, prepare a native environment with
`integration_native.py prepare --profiling-source file:///clean/library ...` and run
`integration_checks.py run --lane profiling` in that environment. The library
must retain its real implementation and exact source identity. Missing library
access is pending qualification, not a successful skip.

## Access and maintenance

The private loop source uses the existing read-only CI key. M365 tools are not
part of Unified’s standard shipped dependencies. Its two tests qualify an
optional developer overlay, so they remain manual: provide a clean upstream
checkout under `.ci/m365` and the declared `.ci/loop-live`, then explicitly run
the `connected-documents` lane. No new cross-repository CI access is needed.
The local profiling repository currently has
no configured remote, so it remains a manual owner until publication is decided.
Fork PRs cannot consume private repository secrets; their private-source lanes
remain unqualified until an authorized source-access run is available. Do not
use `pull_request_target` to execute untrusted PR code with secrets.

When adding an integration or discovering another skip, update the ownership
manifest, affected-path rules, this checklist, and the nearest UX smoke scenario.
When a selector is removed or renamed, update its owner deliberately: a missing
selector is a failing result, not permission to shrink coverage silently.
