# Standard UX qualification

The inventory contains **199 workflows and 669 expected outcomes**.
The current evidence ledger records **609 outcomes observed in a
named context, 42 partially observed, and 18 not run**.
**160 workflows** have every inventoried outcome
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
**60 residual outcomes** has its own rationale, exact supporting
claims and next acceptance step in
`output/readiness-day-20261015/ui-inventory/residual-outcome-dispositions.json`.
No disposition changes an unrun result into a pass.

An independent completion audit closed two finite gaps: actual workspace Pin/Unpin
through keyboard and coarse-touch controls, and combined private draft/history
preservation through connection removal, replacement and reload. Their exact-source
receipts are in `finite-standard-control-qualification-20261015.json`.

Finite local-worker acceptance remains queued for durable Stop and active-worker
configuration, default-model and file-policy boundaries. The remaining disposition
labels do not by themselves establish that all required work is complete.

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
