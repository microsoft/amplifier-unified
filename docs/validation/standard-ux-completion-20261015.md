# Standard UX qualification

The inventory contains **199 workflows and 669 expected outcomes**.
The current evidence ledger records **616 outcomes observed in a
named context, 36 partially observed, and 17 not run**.
**166 workflows** have every inventoried outcome
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
**53 residual outcomes** has its own rationale, exact supporting
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
`archive-running-worker-20261015.json`. Local competing-owner and recovery-copy
no-replay acceptance remains assigned to the coordinator.

The canonical recipe repair and exact-candidate acceptance remain required work.
Final integrated preview/source/PR reconciliation also remains separate; these
outcome counts do not establish completion of the entire requested task.

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
