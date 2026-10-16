# Standard UX qualification

The inventory contains **199 workflows and 669 expected outcomes**.
The current evidence ledger records **618 outcomes observed in a
named context, 36 partially observed, and 15 not run**.
**168 workflows** have every inventoried outcome
observed in some context. These figures describe exact evidence, not universal
device, provider, security or performance acceptance.

Ordinary navigation and outcome completion are separate:

| Workflow accounting | Count | Meaning |
| --- | ---: | --- |
| Ordinary path observed | 189 | Named public actions and expected results have browser evidence. Broader outcomes can still be partial. |
| Ordinary UI with platform limit | 1 | Workspace environment controls use an isolated synthetic manager; real nested-platform allocation is not qualified. |
| Advanced or boundary audit | 3 | Bundle internals, computer/screen controls and rollback are outside ordinary navigation. Supplemental proof remains. |
| Crosscutting audit | 6 | Keyboard, responsive layout, async behavior, privacy, performance and dynamic discovery are audits across controls, not unvisited destinations. |

The frozen prior snapshot had 198 workflows/663 outcomes and 601 covered-in-context outcomes.
The declared PR #781 queue delta adds one ordinary workflow and six newly observed
outcomes. It does not relabel the frozen baseline. The combined queue qualification
records 123 changed backend tests, 22 final read-admission tests, 33 affected component
tests, and the original environment, final held-plan and new queued-result browser
journeys. Physical Docker/Incus and provider acceptance remain separate.

Root accepted this scoped accounting, **not full completion**. Each of the
**51 residual outcomes** has its own rationale, exact supporting
claims and next acceptance step in
`output/readiness-day-20261015/ui-inventory/residual-outcome-dispositions.json`.
No disposition changes an unrun result into a pass.

An independent completion audit closed two finite gaps: actual workspace Pin/Unpin
through keyboard and coarse-touch controls, and combined private draft/history
preservation through connection removal, replacement and reload. Their exact-source
receipts are in `finite-standard-control-qualification-20261015.json`.

Actual normal-worker acceptance now closes five additional outcomes: durable Stop,
no automatic continuation after service reopen, unchanged active-worker defaults
and source graph, and file-policy enforcement at explicit worker reopen. The
separate browser and runtime witnesses are joined only for those named outcomes
in `standard-ux-worker-outcome-delta-20261015.json`. The prior 609/42/18 snapshot
is retained. Newly unqualified source activation is not claimed.

Normal Work activation also retains its 13 declared tool capabilities, live
orchestrator, boundary context and configured provider; exact evidence is in
`work-required-modules-20261015.json`. This qualifies the named Work profile,
not arbitrary bundle composition. Actual-worker Archive now preserves running work, committed history and workspace
sentinel files. Explicit Stop is a separate action; see
`archive-running-worker-20261015.json`. Public recovery and takeover also preserve the prior local file effect and
transcript, start no competing worker, and admit no input on duplicate receipts.
The competing owner is a real Foundation lock/release process; full CLI runtime
and arbitrary remote effects remain unqualified. See
`recovery-ownership-worker-20261015.json`.

The canonical recipe repair now has exact-source CLI, independent-checker,
repository-gate and retained-corpus qualification. Canonical PR publication and
final integration/CI/resource reconciliation remain separate; these outcome
counts do not establish completion of the entire requested task.

Other residuals distinguish external account/device/platform acceptance,
runtime or durability invariants, broad security and filesystem-read claims,
Advanced or nonexistent controls, and crosscutting audits. Named synthetic
capabilities and browser observations retain their actual limits. Missing real
provider acceptance is not silently relabeled a UI success.

The approved completion policy is risk based. Every ordinary workflow needs an
actual public path and relevant effects. Distinct control/navigation patterns
need narrow, keyboard or touch witnesses; destructive and async states need their
specific refusal/recovery checks. The original four-input-mode matrix is retained,
but repeating every workflow in every mode is not the completion gate. Shared
component evidence is reusable only through a named source/assertion crosswalk;
it never proves an unrun effect or every sequential Tab path.

The source audit accounts for all **1,666 frozen lexical candidates** and records
current-source deltas separately. The **72 route-discovery probes** observed
**1,742 stable rendered instance IDs** with **204 route/containment assertions**.
Those are not 1,742 effect passes. Twenty-six candidates in unused built-in
Computation/Operations components have import-graph, compiled-marker and rendered
route evidence for their reviewed unreachable boundary. Nine named component
groups expose 138 Advanced controls. Independent review confirmed those routes;
ordinary Workspace Work, workspace configuration and Privacy alternates remain
included. Arbitrary extension-generated surfaces are not silently excluded or
claimed qualified.

The eight shared-pattern crosswalks name their Settings dialog, navigation,
Canvas dialog, deletion, update-confirmation and diagram witnesses. Universal
keyboard access, arbitrary payload redaction, every renderer's hidden reads and
external-effect replay remain explicit limits. The nonexistent generic dirty
Canvas Cancel dialog is a scoped inventory correction; actual retained editing,
refused replacement and targeted recovery keep their own evidence.

Evidence records retain source hashes, fixture/runtime context, action/assertion
lines, original failures and cleanup receipts. Historical baseline, corrected
candidate and actual-scale measurements remain distinct. Functional timings under
shared load are not performance claims. No main release or production adoption is
part of this audit.

Machine-readable policy: `frontend/tests/standard-ux-completion-policy.json`.
Source/control audit: `docs/validation/standard-ux-completeness-audit-20261015.json`.
The authoritative current ledger and exact claims are
`output/readiness-day-20261015/ui-inventory/{coverage-ledger,functional-claims}.json`.

## Post-release 166 results

This follow-up is additive to the frozen audit above. The 199/669 counts and
external/platform residuals remain unchanged. Each case below maps to the
[existing smoke checklist](standard-ux-inventory-20261015.md#post-release-166-regression-checklist)
and its routine workflow. Product changes are integrated through `bac4749c4`;
`270c60be0` rebuilds the committed client. Focused receipts qualify their recorded
sources, not a future hosted result. **Final rebuilt-head Full Python, Public
readiness and Extended browser qualification remain pending at this checkpoint.**

The original [166 post-release run](https://github.com/microsoft/amplifier-unified/actions/runs/38845659651)
retains its failures: Python passed (9,084 tests plus six subtests, 87 skips),
while execution inspection, mobile navigation, cache cleanup and the first
readiness history fixture failed. The subsequent
[extended run](https://github.com/microsoft/amplifier-unified/actions/runs/38849349256)
passed 70/72 scenarios and exposed appearance preview and active-client failures;
the [readiness run](https://github.com/microsoft/amplifier-unified/actions/runs/38849351378)
stopped at missing owned evidence storage. These historical runs are not relabeled
green. A separate sweep of 30 remaining readiness scripts passed 27 and exposed
voice following, stale retained-history assumptions and a missing PDF dependency.

| Case | Observed baseline / discovered gap | Current focused result | Desired result / remaining limit |
| --- | --- | --- | --- |
| R166-01 | A late preview could reactivate after navigating away. | Preview ownership fix `cec5614f6`; four unit cases and four focused browser flows passed, including held requests, A→B→A, close/reopen and saved-library previews. | No retired preview applies or destroys its successor/draft. Final integrated jobs pending; custom CSS remains supplemental scope. |
| R166-02 | An unchanged layout receipt waited for unrelated global state, then performed 1,500 ms recovery. The hosted 3,442 ms outlier lost its detailed report and remains unattributed. | Fix `ddd2a66f3`: 16 focused layout/transport tests and the unchanged browser workload passed. Eight repeated no-ops had no state acknowledgement or recovery read; initial changed values retained acknowledgement. | Correct receipt ownership and no needless recovery. Separate measured comparison below; no claim that this explains the original hosted outlier. |
| R166-03 | The old smoke assumed a complete 5,000-turn rail and shared legacy paging. The corrected bounded journey then exposed lost native reading restoration. | Frontend restoration `6b54df9c3`, no-full-index assertion `a5a3d86c0` and backend cursor `41c7af345`; focused retained-history browser passed in 11.818 s, including independent readers and bounded switch/reload restoration. | Same visible message/offset without full-history hydration or model work. Duration is whole functional scenario, not cold-page latency. |
| R166-04 | Native page references did not carry the current-page restoration cursor. | Focused native backend qualification passed 38 cases; exact tests reject tampering, another owner, reader restart and changed source, and restore after page-cache eviction with bounded reads. | Refuse stale authority safely. Browser behavior after an expired reference is not newly claimed by backend refusal evidence. |
| R166-05 | Initial raw-scroll mismatch was a notice-height change; after fixing the fixture boundary, a real browser layout clamp incorrectly paused active-call following. | Fix `bac4749c4`: exact old controller fails the new next-append counterexample by 400 px. Fixed controller passed (2.568 s), full voice journey passed (10.846 s), ordinary chat scroll passed (11.958 s); focus/wheel pause and explicit resume remain checked. | Layout clamp preserves owned follow; user reading intent pauses it. No physical audio or live-provider claim. |
| R166-06 | Fixture closed the current Work reader, emitted a malformed live model observation and used an ambiguous terminal-row selector. | Fixture/test correction `7b539a36e`: browser passed in 6.83 s; original-reader negative control fails, and separate forced retirement/service-reopen proof preserves canonical terminal model usage. | Current and saved Work show truthful completion/usage. The one-off retirement proof is explicitly separate from routine browser coverage. |
| R166-07 | An 80-message fixture no longer exceeded the current 100-message initial page. | Expanded fixture and current paging/anchor assertions passed the focused message-render scenario and hosted readiness run `38849351378` before its later evidence-setup failure. | Exercise real older-page loading; preserve visible anchor and unique rows. No pass for obsolete legacy preload assumptions. |
| R166-08 | Raw scroll changed while a Work notice settled; that alone did not establish lost reading position. | Mobile fixture now distinguishes stable geometry from notice removal, checks message identity/offset and retained Canvas state; focused/extended scenario passed. | Preserve reading and drafts in both cases, while retaining the stricter raw-position assertion for stable geometry. |
| R166-09 | Cache UI assertions passed but SIGTERM bypassed fixture cleanup; runner found descendants. | Signal-aware shutdown and bounded awaited exit passed the focused strict scenario; runner cleanup still fails on leftovers. | Zero remaining owned descendants. Original cleanup failure remains a failed scenario. |
| R166-10 | Readiness omitted mandatory evidence ownership; active-client and state-transport reports collided. | Owned readiness evidence/upload repair and focused affected scripts passed; three report/runner contract checks passed and report paths are distinct. | Setup failures and every unrun downstream case remain visible; retain each original report independently. Final workflow execution pending. |
| R166-11 | The continuation environment lacked declared `pypdf`; PDF failed before its intended coverage. | Same-source PDF browser passed in 3.731 s with the declared artifact dependency environment (`pypdf` 6.20.1). | Qualify the actual declared environment. No PDF product change or physical-device claim. |
| R166-12 | Final static consistency gate caught frontend source fixes with stale committed assets. | `270c60be0` rebuilds the client; final-head qualification is being repeated against those assets. | Build/source consistency and exact-head checks must pass before claiming the combined candidate. Earlier focused results are retained with their original source/build identities. |

The active-client comparison is recorded in
[its exact-source receipt](active-client-noop-qualification-20261016.json).
One before/after run used the same synthetic 22,915-session/3,932-workspace
catalog, four isolated browser contexts and no inference. With **12 samples**,
this test's reported p95 equals its maximum: action median/max changed from
44/1,550 ms to 33/142 ms; admission median/max from 17/147 ms to 21/42 ms;
health median/max from 7/242 ms to 10/253 ms. Existing limits stayed 2,000 ms
for actions and 1,500 ms for admission/health. These are bounded local comparison
results, not proof of global p95, live-provider performance, or the original
300 ms send-admission target under every workload. Keep correctness counters
separate from latency, and use matching inputs for future regression warnings.

Private retained evidence is at workspace-root:

- `output/postrelease166/readiness-continuation/`: original 30-scenario results,
  voice exact-baseline negative control, corrected checks and cleanup receipt.
- `output/postrelease166/execution-inspection/qualification.json`: current Work
  browser, negative controls and separate durable-observation proof.
- `output/post-release166-fixes/retained-history/qualification.json`: bounded
  native reader qualification and source identity.
- `output/post-release-fixes-20261016/theme-preview/qualification.json`: preview
  ownership qualification.

The cases now have named routine owners in the inventory crosswalk. The wider
result remains pending until those jobs complete on the rebuilt candidate;
neither a focused pass nor this documentation update closes a historical failure
report by itself.
