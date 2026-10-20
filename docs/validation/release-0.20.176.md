# Release 0.20.176 qualification

**Required automated checks for the reviewed 216 browser invocations over 208
distinct scripts now pass within their recorded boundaries**, using the original census and
explicit focused replacements. Backend, frontend, installed runtime and optional
integration owners are reconciled below. Original failures and interrupted runs
remain unchanged. The original installed-media timeout was not reproduced;
physical-device acceptance and unmet performance targets remain explicit limits.

## Source and environment

The complete backend census used `af08e2d56f9197df37a32c79ffe94b829be4c855`.
Test/ownership correction `4b6c7c48801457c4ccf138bd12cf19d810813a31` changed no
application, built-client or dependency bytes. The browser census subsequently
used `9e5db0d22494a873569a1fec09d65dd292d9737e`, including the recent-Work recovery
correction, with all 2,615 tracked files verified and client `555933a337eab39e`.
All 15 hosted checks passed on that exact tree.

Focused follow-ups use combined source
`b2366e359d80010efac72b51197629d99ab1eb2d` and rebuilt client
`b931a4d86a1c52fe`. Final registered source
`511c2504b350e64b31d31687de548b1f3e5dfc17` changes only the additional ownership
records after those focused checks; all 2,622 tracked files match. The clean
installed wheel has SHA-256
`2f6c6ea76913cdfda6385ae50c12dd4769004a4f14252c9aff198261d3df61ca`.
Its installed runtime, status, profiling and media owners pass. The final media
fixture correction changes only error/cleanup diagnostics; application and wheel
bytes remain identical (fixture commit `409b85867b3423d57f5df32f6de439f7f3e1917d`).
Its source SHA-256 is
`24cab3923f4e4b543d010b4d62c1d90b52a1d8e57a596037a487c2905fc36064`.

Execution is confined to the owned Linux ARM DTU with synthetic state, independent
source/installed/CLI environments, Python 3.13.16, published native Core 2.0.2,
Foundation `8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`, and pinned runtime modules.
Node 22.23.3, Playwright 1.63.0 and Chromium 153 use a recorded process-local
`--no-sandbox --no-zygote --disable-gpu` launch configuration. The final browser
runs do not use Chromium single-process mode. No live schedule, external message,
user-account action or production installation is part of this qualification.

## Recorded checks

| Check | Actual result | Scope |
| --- | --- | --- |
| Complete backend census | 9,972 passed, 11 failed, 27 skipped; six passing subtests; 4,955.40 s | Original result retained. Nine failures occurred before worker assertions because a temporary socket path exceeded Foundation's limit; two used an inaccurate fake absent-capability response. |
| All 11 failed backend selectors after corrections | Passed | Exact selector mapping retained. Fresh native/runtime owners used a short private temporary root and the corrected fake; no product fallback was added. |
| Native integration / release runtime | 216 / 10 passed, zero skips | Real published Core and pinned runtime components with controlled providers and state. |
| Complete frontend, earlier candidate | 1,028 passed, zero skips | Earlier exact-source result retained. |
| Complete frontend, focused combined candidate | 1,036 passed, zero skips; 17.773 s | Includes fixture-lifecycle and onboarding-focus regressions; client `b931a4d86a1c52fe`. |
| CLI interoperability | Three passed, zero skips | Independent installed CLI and Unified environments. |
| Context Intelligence / connected documents | One / two passed, zero skips | Private local service and synthetic data; optional prerequisites explicitly prepared. |
| Installed default behaviors | 12 passed, zero skips | Noneditable candidate wheel and pinned behavior repositories. |
| Headless browser integration / package reset | Three / one passed | Preserved passing backend receipts with exact test-source identity. |
| macOS filesystem aliases | Three passed, zero skips in hosted macOS CI | Exact candidate-tree evidence; not physical macOS UI acceptance. |
| Source browser census | 209 invocations completed: 202 passed, seven failed | 201 distinct source scripts. Original results remain unchanged. Focused replacements are tracked below. |
| Installed inline media | Passed in 56.019 s; zero cleanup residue | All required core phases pass on the installed wheel; overall physical qualification is false. Native controls, true visibility and keyboard/200% zoom remain explicitly unqualified. The earlier 300.976 s timeout remains unexplained; see below. |
| Runtime-concurrency browser | Passed in 68.308 s; zero cleanup residue | Real workers at 0/6/12 background loads; retained turn, stream, navigation and Stop assertions. Functional acceptance does not waive performance targets. |
| Five explicitly added browser invocations | Passed | MCP clients passes in 10.205 s; ordinary connection controls, preferences, privacy effects and voice settings each retain all four built-in variants. All have zero cleanup residue. |
| Installed status emitter and package coexistence | Passed in 18.218 s; zero cleanup residue | Exact-source CLI default modules prepared separately; unchanged strict source/wheel/published-Core owner passed. Real CLI/Unified worker observations, no network model calls; zero overlapping package records and uninstall preserved Unified. |
| Optional profiling | 10 passed, zero skips; 10.46 s | Fresh declared-dependency environment and canonical local prototype `8edea47c`; all 14 source hashes verified. Original unavailable/setup failures remain separate. |
| Physical desktop/PWA acceptance | **Unqualified** | Headless Linux cannot establish physical microphone, OS notification, installation or foreground-device behavior. |

The 27 original skip records are reconciled separately: 26 have matching passing
selectors in their explicitly prepared integration owners, including the recovered
profiling source and hosted macOS filesystem check. One collection-level skip
expands into three installed runtime-safety cases. The installed desktop PWA
launch remains unqualified; the original broad-run skip records are unchanged.

### Original browser failures and focused replacements

| Original failed invocation | Later result | Evidence boundary |
| --- | --- | --- |
| `settings-drafts` | Passed with zero retired/remaining processes | UI assertions originally passed, but strict cleanup retired four processes. Their original identities were not recorded. The lifecycle correction has separate negative, Node and real Python signal controls. |
| `storage-history` | Passed with zero retired/remaining processes | UI assertions originally passed, but strict cleanup retired two processes. Original residual identities remain unknown. |
| `standard-ux-connection-inspection` | Passed | Holding the close request reproduced local-closed/durable-open state and the obstructing model popover. The fixture now waits for durable state before reload; effort persistence assertions remain. |
| `standard-ux-decisions-touch` | Passed | The fixture waits for completed navigation/Settings transitions; desktop and touch journeys pass with the decision/no-replay assertions retained. |
| `smart-tools` | Passed in 8.704 s, zero cleanup residue | A healthy tool-review strip shifted the Canvas frame. The review entry moved to Canvas options; genuine errors remain inline and the original geometry assertion remains. |
| `native-history-error` | Passed in 2.814 s, zero cleanup residue | An actual kernel control proves denial with DAC override/read-search dropped only for the scenario process. The original browser assertions pass unchanged. |
| `standard-ux-storage-denial` | Passed in 3.120 s, zero cleanup residue | The unchanged real OS-denial scenario passes under the same bounded capability drop. No product permission check is weakened. |

The additional MCP recovery journey passes in 14.178 s with zero cleanup residue.
The extra MCP clients journey exposed a separate application defect: returning
focus could reopen onboarding Settings after an accepted Close, despite unchanged
empty-provider state. Its correction, three renderer regressions, rebuilt client and
final MCP clients acceptance pass. The complete four-mutation journey takes
10.205 s with zero cleanup residue; its controlled original failure is retained. Later focused source revisions,
per-case observations, process identities and hashes are retained separately from
the original census.

## Census and harness accountability

The requested union contains **211 invocations over 203 distinct scripts**: all
141 scripts associated with the October 15 execution ledger, current scheduled
browser owners, and an explicitly added third settings-fixture consumer. This is
not a claim that every possible outcome or native-device context is covered. A
separate repository census identifies 283 browser files; 80 are outside the
original execution union. The ownership audit selected five additional distinct
scripts: MCP clients and four ordinary effect suites. Their separately recorded passing executions
extend the reviewed scope to 216 invocations over 208 scripts. All original
invocations now have actual outcomes and separately retained passing qualification.
The original installed-media timeout remains unexplained rather than relabeled. The remaining file dispositions
are tracked separately; this report does not claim that all 283 files ran.
The original 199-workflow / 669-outcome ledger remains 618 observed in context,
36 partial and 15 unrun. Its 51 residual outcomes are not silently closed by a
script-level pass. See the [inventory and desired outcomes](standard-ux-inventory-20261015.md).

The first 210-case attempt recorded 46 passes and 11 failures before an outer
command timeout interrupted the next case. A detached replacement retained
strict per-case deadlines and process ownership. It stopped after 58 passes and
two failures; a separate continuation executed only the 151 previously unrun
entries. Its 149 source entries completed. The remaining two never launched
because the coordinator interpreted a provenance-only GitHub environment
expression as executable configuration. Their separate final-source invocation preserves that
declaration apart from the resolved execution environment. Media then reached its
actual browser assertions but timed out in `gif-canvas-errors`. It was not
reproduced by a full diagnostic journey or a completely unchanged full journey
(56.9 s / 57.16 s). The fixture could hide its primary error while waiting forever
in cleanup; bounded receipt waits now retain the primary error and retire only
the owned page. A forced nonsettling control fails as intended with both errors
preserved and clean teardown. The corrected full journey passes in 56.019 s
without changing product code, the installed wheel, dirty-state protection or
required assertions. This establishes passing final acceptance and improved
diagnostics, not the cause of the original 300.976 s stall.

Other diagnosed setup failures remain in their original receipts: fixed screenshot
paths collided with retained artifacts; fixed-port settings fixtures accepted a
different listener; and generated standalone build copies contaminated a Unified
wheel. Corrected ownership uses exact audited screenshot mappings, fixture-owned
ready messages and ephemeral ports, and a clean wheel built without standalone
build residue. The strict wheel/source validator rejected the contaminated wheel
and remains unchanged. No interrupted case, missing prerequisite or cleanup
failure is counted as a pass.

## Current results and remaining targets

| Area | Current evidence | Desired result / outstanding boundary |
| --- | --- | --- |
| Automation overview and future-run repair | Bounded listing, explicit review, draft preservation, Stop races, unknown-run refusal and one future submission pass with synthetic schedules. | [Automation goals](automation-hub-277-679.md) include warm interaction under one second and no idle polling/replay. Historical #679 account/fingerprint state and original worker exit were not reproduced. |
| Saved MCP views | Cooperative SDK reconnect/review preserves input, grants and mutation receipts. | The actual saved Studious artifact and original sequence in #807 remain unqualified; generic recovery does not close that report. See [MCP qualification](mcp-contract-review-20261017.md). |
| Copied-scale performance | Functional navigation/streaming passed at 1/6/12 deterministic workers over 27,508 copied sessions and 5,272 projects. | #633 remains open: warm acknowledgement exceeds the 300 ms target. Final 12-worker stream p95 was 364 ms and independent confirmation 284 ms, above the 250 ms target. [Current/before/desired measurements](copied-scale-176.md) remain source-bound; no end-to-end speedup is inferred from the encoder microbenchmark. |
| Native and external capabilities | Synthetic flows, real Core/runtime boundaries and separate macOS filesystem CI are qualified within their stated scope. | Live-provider latency, original saved-tool behavior, physical microphone/notification/PWA and foreground native-device checks require their own acceptance context. |

Canonical repeat ownership remains in [integration coverage](integration-coverage.md)
and the existing browser registry; the frozen 65-browser and historical 87-skip
baselines are preserved. This one-time census is not a new universal release gate.
Raw source/dependency identities, failed and successful attempts, and cleanup
receipts are retained in the workspace's private
`output/afk-approved-20261017/full-smoke-plan` evidence collection. Copied-scale raw
owner identities remain private; published summaries contain sanitized aggregates.
