# Standard UX inventory and execution plan

Frozen source: `1d993e0bb32bc90b75fc66f56f1b9014c2d63f87` (0.20.165 plus PR776).
Inventory owner: `build_qualify145`. Integration owner: root. This is a source
inventory and browser execution plan, not a release gate or a passing test report.

## Reviewable records

- [Workflow checklist](../../frontend/tests/standard-ux-inventory.json): 196 stable
  workflow IDs in 27 families, prerequisites, real interaction steps, expected
  outcomes, condition/state matrix, source references and existing test candidates.
- [Control census](../../frontend/tests/standard-ux-controls.json): 1,666 lexical
  native control/option declarations across 126 source files. Its source-file
  crosswalk suggests workflows; it does not assert that their tests cover a control.
- [Route catalog](../../frontend/tests/standard-ux-routes.json): 30 Settings routes,
  13 Chat routes, workspace destinations, custom control carriers and exclusions.

All workflow browser statuses begin **not-run**. The 65 associated test scripts
are candidates for inspecting and reusing exact assertions, not 196 passes.
Source references identify implementation owners; each control has a precise
baseline line and excerpt. Workflow IDs survive subsequent source edits. Control
IDs identify declarations in this frozen baseline; new declarations get new IDs,
and removed/changed declarations retain an explicit migration/disposition record.

The discovery pass is deliberately conservative. There are 26 native controls in
the standalone Operations and Computation components whose standard entry has
not been found. They remain **required discovery**, not exclusions. Workflow
`accessibility.dynamic-discovery` resolves their reachability and all runtime
schema/slot-generated controls. A later rendered absence needs supporting source
evidence and reviewer disposition before it becomes not-applicable.

## Scope

Follow every built-in control reachable through a non-Advanced entry, including
menus, disclosures, child pages, available choices and failure/recovery states.
Use standard interface detail on desktop and narrow layouts. Explicitly requested
model reasoning and bundle controls remain included even when a disclosure says
“Advanced model options.”

An internal filename or global Advanced catalog placement is not an exclusion.
Workspace Details exposes Model rules and Modules & sources; AI connections
exposes image preferences; Privacy exposes Backups & recovery; Chat info exposes
current context. These alternate ordinary paths are included. Workspace work
also exposes assignments, task claims and integration checks, while Canvas
exposes output versions and publishing. Use disposable local peers for their
effects. Explicit advanced-only worktree/portability/schedules, raw session tools,
module authoring, custom CSS and global reset remain listed in the JSON exclusions.
The standard link into an excluded destination still gets an entry/back check.

Do not silently turn an unavailable capability into a pass. Physical microphone,
screen permission, OS notification, real account authentication and remote model
latency are separate acceptance contexts. Synthetic controls can qualify the app
flow while the real external capability remains blocked with its exact reason.

## Contexts and safety

1. **Synthetic standard:** full mutable UI, small retained/disposable chats,
   files, workspace roots and two independent browser profiles.
2. **Synthetic capability:** controlled provider/auth/MCP/notification/update/
   publishing peers. Where real Foundation/Core is used, record its exact source,
   configuration and provider. A simulated peer is never called live acceptance.
3. **Private scale read-only:** isolated clone of the approved private snapshot,
   credentials removed and schedules/outbound automation disabled. Use for
   history/list/search/Work and large-state timings. Keep raw content, identifiers,
   screenshots and credentials out of Git and public PR evidence.

All applications, browsers and tests run inside owned DTU resources. The active
editable source/state and its four retained synthetic chats remain reserved to
the integration owner. This branch owns no listener, service or dependency install
at inventory freeze. No main merge, release or spark-1 change is authorized.

## Execution order

Run baseline before correcting behavior. Start with desktop and narrow route
discovery and record each actually rendered control, accessible name, disabled
state, dynamic options and destination. Expand every in-scope submenu/disclosure.
Compare the DOM to the source census and open discrepancies rather than ignoring
them. Repeat required controls with keyboard and touch; hover-only visibility
does not prove keyboard/mobile reachability.

Then execute these linked journey groups, retaining assertions and measurements:

| Wave | Families | Required transitions |
| --- | --- | --- |
| 1 | entry, settings, accessibility | Boot, setup, every ordinary destination, Back/close, private draft guards, narrow routes, focus, disabled capabilities. |
| 2 | nav, create, starters | New chat/workspace, managed/existing folders, hierarchy/search/filter/page, pin order, row details, rename, archive/restore/delete, retry and scroll retention. |
| 3 | composer, connections, bundles, delivery, decisions | Model/reasoning/bundle choices, attachments, accepted send/Stop, failed/unknown outbox, ownership, questions/approvals, provider removal and context recovery. |
| 4 | messages, work, info | Newest page first, explicit older pages, display-only native records, copy/reply/reactions/edit/fork, Work fields/paging/live settlement, truthful usage/coverage. |
| 5 | canvas, outputs | Every supported format, tabs/versions, dirty edits, references, images/GIF/PDF, interactive forms and local publishing receipt lifecycle. |
| 6 | workspace, coordination, configuration | Files/details, standard config overrides, assignments/claims, resource storage/reservations/environment lifecycle, exact integration evidence. |
| 7 | appearance, voice, notifications, privacy, storage, updates, support | All ordinary settings, preview/save/recovery, safe diagnostic/import/cleanup, exact update confirmation, feedback and conditional device flows. |
| 8 | scale and cross-client overlays | Repeat applicable read journeys against private scale; six/twelve synthetic active chats; independent browser selection/drafts; streaming/menu/cancellation responsiveness. |

Use existing scripts only after matching their actual actions and assertions to
workflow IDs. A script may cover parts of several workflows. Record partial
coverage at control/assertion granularity; never spread one passing script across
all source-owner candidates. Original 165 failures remain linked through
`output/readiness-day-20261015/issues/verification-families.json`. The issue owner
edits existing scripts; this branch owns new inventory/discovery/report helpers.

For each workflow exercise applicable initial, pending, accepted, rejection,
unknown-outcome, navigation-away/back and reopened states. Missing prerequisites
produce blocked records, not silent skips. Destructive operations require a fresh
disposable fixture and exact preview/confirmation. No actual outbound operation
is needed to check a fake target's UI lifecycle.

## Evidence and performance

Each execution record must name inventory ID(s), exact source/build/runtime,
data-context receipt, fixture identity, viewport/input method, cold/warm state,
concurrency, actual observed actions, concrete assertions, start/end times,
measurement values, output files and status. A source read or API-only setup is
not a browser action. API setup is permitted for prerequisites; the action under
test uses the real visible control. Distinguish a command accepted from completed.

Current measurements start empty. The JSON carries the root plan's proposed
targets: local feedback 100 ms typical/200 ms p95; warm navigation 500 ms median/
1 s p95; cold history page 2 s p95; search 500 ms warm/1 s cold p95; send admission
300 ms p95; synthetic runtime ready 2 s warm/10 s cold; added streaming lag 250 ms
p95 with 12 active sessions. Record meaningful progress above 3 s and forbid
indefinite loading. These are desired values, not observed results.

Measure gesture→visible feedback→accepted receipt→usable result independently.
Capture request counts/bytes, transport lag, long tasks and host CPU/RSS/event-loop
delay when available. Compare only matching inputs. Root's initial timing warning
requires both >20% and >100 ms p95 regression and a comparable repeat; correctness
regressions remain blocking immediately. No hidden full catalog/history load,
unintended send, repeated effect, cross-client private leak or false completion is
acceptable merely because timings improve.

## Freeze and changes

Root completed the completeness review and authorized browser execution. Inventory v1 is frozen. Keep the
26 reachability controls required until resolved. New integrated product changes
(including per-tool update failure/retry/skip) receive explicit inventory deltas
against their own source; do not relabel baseline observations as newer behavior.
Record every pass/failure/blocked/not-applicable result and its reason. Preserve
failed-before evidence when a targeted correction passes. This document does not
authorize another broad release gate or changes to existing product contracts.

## First execution slice

The owned `standard-ux-discovery-browser.mjs` runs 18 destination probes in four
input/layout variants. It uses visible navigation and real disclosure controls;
there is no hidden state-command shortcut for a destination. The evidence helper
records each observed action, assertion, rendered control/options instance, ARIA
snapshot and screenshot. Set `AMPLIFIER_TEST_ARTIFACTS` to an owned directory.
`STANDARD_UX_VARIANTS` and `STANDARD_UX_ROUTES` restrict a focused rerun without
changing its assertions. `DTU_CHROMIUM_SINGLE_PROCESS=1` is the qualified ARM DTU
launch mode. The fixture uses an ephemeral loopback port and synthetic providers.

The initial 72 unique probes passed after two harness corrections (the actual
Settings footer entry and the finite release-disclosure count). Their original
failures remain in the evidence. This is partial reachability/layout coverage,
not 72 completed workflows. Per-control observations across several pages and
variants contain repeated controls and must not be compared numerically with
the lexical source census. Shared-load timings are observational only.

Execution evidence and the per-expected-outcome ledger live at workspace-root
`output/readiness-day-20261015/ui-inventory/`. The ledger has 649 baseline outcomes;
new integration workflows are recorded separately in
[the delta ledger](../../frontend/tests/standard-ux-inventory-deltas.json).
The first delta covers #635's controlled per-tool Retry/Skip flow at its exact
integration source, including the narrower evidence limits of that browser test.

The functional execution ledger maps unchanged original browser assertions to
zero-based expected-result indices, exact script hashes, layout/input contexts,
and retained run logs. A result can be covered in one context while its other
variants remain untested. Compound outcomes receive partial credit where an
assertion proves only part of the sentence. API fixture setup is distinguished
from a visible action under test; synthetic transport/media does not qualify a
real provider, physical device, or external publication.

The second inventory delta clarifies composer acceptance: the submitted text can
clear into the durable local outbox before host acknowledgement. Its exact intent
must remain recoverable, and an older receipt must never overwrite a newer draft.
The frozen wording remains recorded beside the clarification. This aligns the
checklist with the existing pending-message contract; it is not a product change.
