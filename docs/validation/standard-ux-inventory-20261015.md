# Standard UX inventory and execution plan

## Current follow-up smoke coverage (October 16)

| Case / owner | Current evidence | Desired result and limits |
| --- | --- | --- |
| `standard-ux-keyboard-reflow` / extended browser checks after publication | Sequential Tab/Enter entry, all eight ordinary Settings sections, compact Back and Close; 29 focus/geometry witnesses at 1440×1000 and 720×500. Complete run: 7.173 s, zero leftover processes. | Every exercised destination remains keyboard reachable with visible, unclipped focus; no horizontal overflow or lost draft. Half CSS viewport qualifies 200% zoom-equivalent reflow, not browser/OS zoom controls or all widgets. |
| #712 exact approved storage scope / full Python checks after publication | Existing real Core coordinator mount test now also saves an explicitly authorized session directory, writes there on the next mount, and refuses the sibling. With task-storage acceptance: 35 passed, zero skipped. | Exact scope only; denied/unknown allocation performs no filesystem effects. Joined Stories import/native review/export and actual resumed-delegate continuation remain unrun. |

Source identities, original failed setup attempts, limits and observed/desired
outcomes are in [the additive receipt](standard-ux-followup-20261016.json).
The original 199-workflow / 669-outcome ledger remains 618 covered-in-context,
36 partial and 15 unrun. The 51 residual outcomes include broad guarantees and
external acceptance; they are not 51 ordinary destinations left unvisited.

## Frozen inventory baseline

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

## Post-release 166 regression checklist

This additive checklist records every newly discovered boundary from the 166
follow-up. It extends the existing workflow journeys; it does not rewrite the
frozen inventory, add effect passes to its 199-workflow/669-outcome accounting,
or turn a fixture correction into a product improvement. `R166-*` IDs are stable
regression cases within the mapped workflows. Their baseline and current results
are recorded in [the completion report](standard-ux-completion-20261015.md#post-release-166-results).
Qualification was pending at this checkpoint; see the
[completed repair qualification](standard-ux-completion-20261015.md#completed-repair-qualification-october-15-pacific)
for the subsequent results and exact source limits.

| Case and existing workflow IDs | Required smoke actions and invariant | Regression test and routine coverage |
| --- | --- | --- |
| **R166-01 Preview ownership** — `appearance.library`, `settings.dirty`, `accessibility.async` | Hold a preview before delivery and after server acceptance; navigate away, return to the same page (A→B→A), and close/reopen Settings. Retire the old preview without applying shared appearance or losing its draft. A new owner can preview; cleanup must not cancel its successor. Include saved-library and settled previews. The custom-CSS entry is a supplemental Advanced boundary, not new ordinary-UI scope. | `appearance-preview-navigation-browser.mjs` in Public readiness; four `appearance-preview.test.mjs` cases through `npm test`. Existing `settings-completeness` remains in Extended browser checks. |
| **R166-02 Cross-client no-op acknowledgement** — `appearance.layout`, `accessibility.privacy`, `accessibility.async`, `accessibility.performance` | In four independent clients, change layout, then repeat the unchanged value while unrelated clients advance state. Changed values still require their client acknowledgement; no-op and duplicate receipts must not wait for unrelated state or trigger `/api/state` recovery. Preserve drafts/selection, stale-revision refusal and reused-ID refusal. | Compact/legacy HTTP cases in `test_state_transport.py` through Full Python checks; `active-client-performance-browser.mjs` in the Extended manifest, including acknowledgement/recovery counters. |
| **R166-03 Bounded native reading restoration** — `messages.history-pages`, `messages.native-alternate`, `messages.rail`, `nav.select`, `accessibility.privacy` | Open the newest bounded native page, navigate only the requested window, load ten preceding pages and verify contiguous unique rows. Page in an independent browser without moving the first reader. Switch chats and reload: restore the same visible message/offset with at most one 100-row page, no full-history navigation index, no model work and unchanged source files. | `retained-history-browser.mjs` in Public readiness; `test_native_anchor_survives_page_eviction_without_canonical_merge` in `test_transcript_windows.py` through Full Python checks. |
| **R166-04 Invalid native restoration reference** — `messages.native-alternate`, `messages.history-pages`, `accessibility.async` | Restore the current page, not its predecessor. Evict page bodies and recover from the bounded reference. Reject tampered references, another owner, another reader lifetime and changed source; never turn stale/expired authority into an unbounded transcript read or canonical-history mutation. | Restoration/bounded-read cases in `test_transcript_window.py` and managed/unmanaged eviction cases in `test_transcript_windows.py`, both collected by Full Python checks. No browser restart-expiry recovery pass is inferred from these backend refusal checks. |
| **R166-05 Streaming follow versus reading intent** — `voice.call`, `messages.history-pages`, `accessibility.responsive`, `accessibility.async` | While following the owned call, remove a notice so the browser clamps scroll to its new maximum before the layout frame; the next append must still follow. Actual wheel or focus scrolling must pause follow; explicit resume restores it. Check delayed append, resize, hangup and another selected chat/client. When paused, preserve visible message identity/offset. For ordinary typed replies, wait for exact rendered stream growth and preserve both the submitted-message anchor and the first visible stream paragraph. Exercise settled Work geometry with unchanged raw scroll, notice-changing geometry, and rejected real 80 px/latest-jump counterchecks. Also reject a stream-only displacement while the identified message and raw pane remain unchanged. | `voice-follow-latest-browser.mjs` and `chat-scroll-return-browser.mjs` in Public readiness; ordinary `chat-scroll-browser.mjs` covers the submitted-reply, visible-stream and raw-position boundaries, including the deliberate-jump and stream-only counterchecks. |
| **R166-06 Current and saved Work evidence** — `work.group`, `work.fields`, `work.usage`, `work.retry` | Load an accepted current Work projection, complete a live model row, retain its terminal token usage, reload and inspect the same group. The fixture must keep the actual reader and persist canonical provider/LLM events; a matching row elsewhere is not proof. Separately retire/reopen saved observations and verify the persisted usage. | `execution-inspection-browser.mjs` in Extended browser checks; execution/cold-compaction suites in Public readiness and Full Python checks. The one-off forced-retirement receipt supplements these routine checks; it is not a new continuously run browser assertion. |
| **R166-07 Current message-page fixture** — `messages.history-pages`, `messages.rail`, `nav.select` | Seed more than the current 100-message page, exercise actual sequential loading and rendering stability, and preserve the user's anchor when follow-earlier automatically requests a page. Do not simulate paging using a smaller fixture or the obsolete shared-history offset. | `message-render-stability-browser.mjs` and `retained-history-browser.mjs` in Public readiness. |
| **R166-08 Mobile geometry and retained viewer** — `nav.drawer`, `accessibility.responsive`, `messages.history-pages` | On narrow layouts, open/close navigation and Canvas without losing private draft, viewer state or visible reading anchor. For stable geometry also require unchanged raw scroll position. When a delayed Work notice disappears behind Canvas, require unchanged message/offset while allowing the necessary raw-scroll adjustment. | `mobile-navigation-browser.mjs` in Extended browser checks, including stable and changing-geometry witnesses. |
| **R166-09 Graceful fixture retirement** — `storage.inventory`, `storage.review`, `storage.delete` plus test lifecycle | Complete the cache UI assertions, request graceful fixture shutdown and await bounded exit. Close service resources and require zero remaining descendants. Keep the strict runner's cleanup failure; passing UI assertions alone do not pass the scenario. | `cache-usage-browser.mjs` and its signal-aware Python fixture through Extended browser checks; orphan/cleanup tests in `test_browser_check_runner.py` through Full Python checks. |
| **R166-10 Owned, distinct evidence** — all scenario receipts, especially `accessibility.performance`, `updates.failure`, `connections.images` | Allocate owned evidence before the first browser group; preserve partial evidence on failure. Active-client and state-transport reports must have different paths and both be uploaded. Record a failed setup or skipped downstream scenario explicitly; never infer its pass from an earlier group. | Public readiness evidence setup and always-run upload; Extended manifest/report upload; ownership/distinct-report contract cases in `test_browser_check_runner.py` through Full Python checks. |
| **R166-11 PDF prerequisite fidelity** — existing Canvas/output rendering journeys | Exercise the PDF browser against the declared artifact dependency group. A reused environment missing `pypdf` is a setup failure, not a renderer result. Retain that failure and rerun the same source with the declared prerequisite before assigning a product outcome. | `pdf-browser.mjs` in Public readiness; Public readiness, Extended browser checks and Full Python checks use locked `dev` + `artifacts` dependencies. |
| **R166-12 Built-client fidelity** — all browser journeys and release evidence | Rebuild the client after frontend changes, verify generated static output matches the committed candidate, then qualify that built client. Preserve earlier source-only/focused observations but do not label them proof of the newly built integrated package. | Work workflow frontend build plus `git diff --exit-code -- amplifier_web/static`; the Release workflow repeats the static consistency gate. Public readiness and Extended browser checks build their checked-out source before browsers run. |

All script names above are under `frontend/tests/`; Python test names are under
`tests/`. Routine jobs are the existing `public-readiness.yml`,
`browser-checks.yml`, `python-checks.yml`, `work-workflows.yml` and `release.yml`.
`post-release.yml` invokes the first three against the published revision. This
crosswalk adds no second broad suite or publication gate. Run the changed case
and its counterexample locally, then reuse the existing integrated jobs once per
candidate. A fail-fast readiness group can leave later cases unrun; the result
record must name them, and an owned run of those same scripts can expose
independent failures without repeating already-passed setup.

Desired correctness is exact: no stale preview, lost draft, cross-client leak,
unbounded history fetch, false Work completion, unintended follow/pause or orphan
process. Existing UX latency targets above remain targets, not newly achieved
results. Functional scenario duration is not interaction latency. In particular,
the 12-sample active-client comparison retains its unchanged thresholds and
sample-size limits; it does not establish the original global p95 targets.

### Image-acquisition failure coverage follow-up

**R166-10** also covers prerequisite failures in the existing managed-update
handoff check. Record image acquisition failure before application assertions;
try each official registry at most once for the same pinned OCI index and retain
the selected digest, operating system, architecture and image configuration ID.
Never retry the image build, container run or application exercise as an acquisition
fallback. Reject identity mismatches and preserve every failed attempt.

After acquisition, require the unchanged four real systemd scenarios: managed
self-restart with successor HTTP identity; rejected restart preserving the original
process and handoff; legacy erased-marker reconciliation; and the exact historical
updater's SIGTERM followed by successor reconciliation, preserving the historical
failure. `scripts/test_systemd_update.py` runs through **Managed update handoff**
(`systemd-update.yml`). Its 13 acquisition/no-replay controls in
`tests/test_systemd_update_script.py` are collected by Full Python checks. These
are additional boundaries of R166-10, not another workflow or broad release gate.

### PR787 environment-control coverage follow-up

**R166-13** extends the existing `workspace.environment` checklist without changing
its three outcomes or the frozen **199 workflows / 669 outcomes** baseline.
From the workspace heading, open **Details → Managed resources → Test environments**.
Start and run a harmless check; inspect an unknown result, then **Check and recover**
using the retained backend while new execution is unavailable. Stop, open
**Remove environment… → Save more files**, verify complete-copy defaults off for a files-policy environment,
toggle it, cancel, reopen, and explicitly confirm a complete copy. Check the exact
resource/revision, retained backend, `retentionMode: full`, verified size, unchanged
workspace files, and the next queued check's unchanged files-only default.

`test-environments-browser.mjs` now belongs once to **Task coordination contracts**,
with an always-retained evidence directory. Its controlled Incus transport exercises
real host actions/receipts; it does not allocate Incus instances or verify a real
archive. Desktop and narrow pointer interaction are covered here. The existing
`test-environments.test.mjs` cases through Fast `npm test` separately verify both
unchecked (`files`) and checked (`full`) confirmation handlers. Existing backend
recovery/retention tests cover backend ownership and archive-validation contracts separately.

Fast's existing release-contract step also runs `test_browser_check_runner.py` and
`test_active_client_diagnostics.py`, catching scenario ownership/report metadata
errors before a full Python run. This adds no new workflow or broad gate.

## Integration prerequisites and platform coverage

The [IC-01–IC-10 ownership checklist](integration-coverage.md) extends this
inventory with real runtime, CLI, browser/PWA, filesystem, reset, diagnostic
server, connected-document and profiling acceptance. A skipped host test is not
a passing smoke result; consult its designated owner and recorded limitations.

### Checkout identity across handoff waits (October 16)

**R168-01** extends checkout handoff coverage: after the initial Git preflight,
replace the source or destination checkout's `.git` identity while the host waits
for the saved worker boundary, and separately while runtime release is pending.
The move must never apply the substituted checkout. Before release, reject it
without invoking release and clear the preparation state. After confirmed release,
retain the unresolved receipt and require inspection/reconciliation; preserve the
original execution placement, messages and execution revision, and replay no input.

The four deterministic real-Git substitution cases in
`test_multi_repository_worktrees.py::test_repository_replaced_during_handoff_await_never_applies`
are owned by the existing Task coordination contracts and full Python checks. They model
runtime waits with explicit asynchronous barriers, not a physical filesystem lock
or an arbitrary concurrent external writer guarantee. They supplement the original
preflight replacement regression and do not change the frozen 199/669 inventory.
