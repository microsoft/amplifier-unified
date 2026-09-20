# Shared clients: source baseline and gaps

**As of 2026-09-20. Source assessment, not conformance certification.**
The draft clauses are not ratified. Tests cited below are inspected test coverage,
not tests rerun for this documentation change. Prior validation reports remain
bounded by their own revisions, environments and stated limitations.

Reviewed implementation revisions:

- **U:** [Unified 83eb5a53](https://github.com/bkrabach/amplifier-unified/tree/83eb5a53b6bb45c490cec1e14733e04048260ecb), release 0.19.4 source.
- **T:** [TUI 20afde06](https://github.com/bkrabach/amplifier-app-tui/tree/20afde064897f3e4dd3a027134492cb17854672e), current reference checkout when reviewed.

During drafting, Unified main advanced to
[4f66d251](https://github.com/bkrabach/amplifier-unified/commit/4f66d251926fa8f97ab511315c365ee826ea01b2)
through runtime-recovery PR 152. The source baseline above remains pinned; this
packet does not turn that later merge or its deployment into a new acceptance run.

Relative Unified source links below describe U, not later edits on a moving branch.
TUI source and existing acceptance can be found in its pinned
[handoff](https://github.com/bkrabach/amplifier-app-tui/blob/20afde064897f3e4dd3a027134492cb17854672e/docs/UNIFIED-HANDOFF.md),
[plan](https://github.com/bkrabach/amplifier-app-tui/blob/20afde064897f3e4dd3a027134492cb17854672e/notes/PLAN.md),
and [acceptance notes](https://github.com/bkrabach/amplifier-app-tui/blob/20afde064897f3e4dd3a027134492cb17854672e/notes/ACCEPTANCE.md).
"TUI" below means the existing standalone implementation; **no connected TUI
implementation or actual web + Ratatui live acceptance is established by this review.**

## Experience clauses

| Clause | Web / Unified observations | TUI observations | Remaining evidence or work |
| --- | --- | --- | --- |
| CX1 | Selection/preparation separation, bounded history and navigation are implemented. | Native session picker, latest-history window and older-page actions exist. | Measure actual connected TUI navigation; characterize cold/large histories separately from warm clicks. |
| CX2 | Optimistic outbox and pending/failed/unknown rendering exist in [message-outbox](../../frontend/src/message-outbox.jsx); browser checks exist. | Standalone draft/admission retention exists. | Connected durable outbox, exact retry and late-reply tests with real terminal; do not equate sessionStorage with recovery on a different device. |
| CX3 | Shared send/stop/worker/approval actions exist; one universal graceful/force contract is absent. | Distinct first/second Ctrl-C and queue/steer semantics. | Agree operation semantics before mapping keybindings; integrate durable questions from their owning work. |
| CX4 | Composer ownership overlay and explicit takeover exist in [composer-ownership](../../frontend/src/composer-ownership.jsx). | Cooperative ownership, read-only busy state and Continue here exist. | Connected TUI presents host ownership instead of acquiring another local writer. |
| CX5 | [History revision](../../amplifier_web/history_revision.py) and [outbox browser checks](../../frontend/tests/message-outbox-browser.mjs) cover edit/retry paths. | Preserved canonical history and terminal scrollback; no connected edit flow. | Specify supported edit/fork actions and recovery display without rewriting committed terminal scrollback. |
| CX6 | Shared [activity feedback](../../frontend/src/activity-feedback.js), region cues and browser tests exist. | Terminal input, busy/read-only views and modal interactions exist. | Cross-surface feedback/accessibility audit; current code existence does not prove every control or dismissal path. |
| CX7 | Rich canvas/artifact UI and public agent actions exist. | Native text/Markdown, links and inspection views exist. | First-release rich-content fallback and capability declaration; physical device behavior remains separate. |

## State clauses

| Clause | Source observation | Gap / boundary |
| --- | --- | --- |
| CS1 | [Client views](../../amplifier_web/client_views.py), [live endpoints](../../amplifier_web/live_clients.py) and [tests](../../tests/test_live_clients.py) cover independent clients and explicit targets. | Python terminal adapter tests do not prove a connected Ratatui client; no execution migration implied. |
| CS2 | Client-scoped selection/drafts/canvas/device commands exist. Settings owners identify some agent-visible editor drafts. | Classify each setting/view field; do not assume every unsaved field or theme is already client-private. |
| CS3 | [Service publication](../../amplifier_web/service.py) loops all subscribers, invalidates client snapshot caches and builds browser projections. Session streams filter the resulting payload. Queues are bounded and progress is batched. | **Scoped transport output is not scoped publication work.** Conversation/config dependency routing and no-viewer optimization need implementation and instrumentation. |
| CS4 | Persisted receipts, snapshot replacement, host-instance reset and reconnecting [SessionClient](../../amplifier_web/session_client.py) exist. | Wire v1 is full snapshots, not event replay; exact retry retains original client identity. Connected TUI recovery/outbox is missing. Unknown external effects stay unknown. |
| CS5 | Shared settings have global/project/local/session scope and field-preserving writes; see [shared configuration](../SHARED-CONFIGURATION.md) and [tests](../../tests/test_shared_settings.py). | Complete cross-client editor conflict behavior and dependency-scoped settings publication are not demonstrated. |
| CS6 | Configuration stamps and worker reacquisition exist; mounted session controls are distinct from defaults. | A uniform saved/effective/mounted revision model is not established across both clients. Catalog cache redesign is proposed, not shipped. |
| CS7 | Canonical names/history/configuration and cooperative ownership mechanisms are integrated. TUI parks by disposing/remounting; Unified may retain warm workers after releasing ownership. | Private control portability is incomplete. Current recovery uses transcript/metadata; event-log-only context reconstruction is **not implemented** by this work. |
| CS8 | Client-targeted device commands and browser permissions exist; their isolation has source tests. | Future mobile devices, voice-call arbitration, cross-device effect recovery and a full capability matrix remain unverified. |

Additional current protocol limits matter to implementers: `expectedRevision`
uses a global app revision, not a conversation revision; the host control token
is not a restricted per-client credential; presentation IDs are not authorization.
The attachment response describes protocol/transports, not a complete native
device-capability negotiation. See [wire v1](live-sessions.md) before designing extensions.

## Development clauses

| Clause | Observed baseline | Next gap |
| --- | --- | --- |
| CD1 | Unified wire/handoff and TUI DRAFT contracts already exist. | Review this shared set and add a referenced adoption revision in TUI; avoid duplicate rules. |
| CD2 | Existing work is split among runtime, settings, chat UI and execution-view owners. | Make shared semantic impact explicit in affected PRs. |
| CD3 | Protocol v1, action discovery and documented optional preparation exist. | Record supported host/client combinations and capability fallback tests for the actual TUI. |
| CD4 | Owners were consulted for this packet; their boundaries appear below and in the plan. | Assign bounded implementation slices after direction review, preserving active ownership. |
| CD5 | Source tests and prior browser/worker evidence are available. | Add actual multi-client terminal/browser acceptance and publication measurements. No formal DRAFT verdicts. |
| CD6 | Existing release validation distinguishes build from deployed service. | Add client-contract revision and supported-client record to future coordinated releases. |

## Owner-reported work beyond the baseline

These are coordination inputs received on 2026-09-20, **not independently tested
main-branch behavior in this assessment**. They should be rechecked at integration.

- **Settings Design:** current work is mockup/research only. Preserve scope,
  inheritance and saved/effective/mounted distinctions. Model catalog refresh is
  separate from editing configuration. No settings implementation lane is reassigned.
- **Harness:** its runtime-manager update/shutdown separation fix was subsequently
  merged in [PR 152](https://github.com/bkrabach/amplifier-unified/pull/152).
  Update generations and admission fences are host-owned. A free lock
  or published stopped state is not proof all external effects have settled.
- **Codex Parity:** [PR 147](https://github.com/bkrabach/amplifier-unified/pull/147)
  is a separate integration effort for durable questions/tasks, operations and
  related controls. Its owner reports a stop fix that waits for process exit;
  it does not introduce universal graceful/force semantics. It is unreleased at
  this checkpoint. Unknown-delivery and cross-host transfer limits remain explicit.
- **Chat UI and execution-view owners:** the parity owner identifies separate
  ownership of composer/new-chat controls and canonical reader/timeline work.
  This packet changes none of those files.

## What this assessment does not establish

No new browser, actual-terminal, physical-device, live-provider or production
performance test was run for this documentation change. The often-reported
"same session" goal does not prove current UI parity, event-only recovery,
private-control portability, configuration replication or cross-host execution.
The [plan](development-plan.md) specifies the missing acceptance instead of
marking these gaps complete because related APIs or fixtures exist.

## Review of this document packet

Local reference targets, contract headings/clause counts and whitespace checks
passed. These checks validate documents, not application behavior.

Bounded owner reviews on 2026-09-20 covered settings scope (CS5–6 and the scope
map), runtime ownership/lifecycle (CS and the plan), and parity action semantics
(CX3–5 and the plan). The settings review corrected CS6 so an override protects
the overridden field rather than exempting the entire session from inherited
changes. The other requested boundaries had no reported contradictions.
These reviews do not ratify the contracts or establish cross-client conformance.
