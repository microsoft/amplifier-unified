# Shared-client development plan

**Proposed work derived from DRAFT contracts.** This is a coordination note,
not a new source of behavioral promises or a claim that implementation started.
Sources: [vision/contracts](direction.md), [baseline](assessment.md) and the user's
request to develop multiple concurrent clients with a consistent experience.

## Existing ownership to preserve

| Area | Current owner/boundary | Coordination required before overlap |
| --- | --- | --- |
| Shared client direction, live protocol and connected TUI integration | Live Multi-Device work | Own this packet and integrate the first adapter; inspect current TUI direction before edits |
| Runtime lifecycle, update generation, worker admission | Harness | Agree stop/exit fence, preparation and update behavior; do not mix its urgent release with these docs |
| Durable questions/tasks and related public operations | Codex Parity | Reference its operation contracts and supported capability versions; do not duplicate that implementation |
| Settings experience | Settings Design, currently mockup/research | Agree visible scope/provenance/apply state and editor conflicts before implementing settings controls |
| Composer/new-chat interactions | Chat UI | Coordinate optimistic delivery and controls instead of editing its active surface in parallel |
| Canonical execution reader/timeline | Execution-view owner | Coordinate state/artifact presentation and large-history rendering |
| Shared metadata/settings/ownership mechanisms | Foundation maintainers | Propose only demonstrated missing reusable mechanisms; leave UI policy in apps |

Owners were consulted for this document packet. This table reserves existing
boundaries; it does not assign new work to those teams or promise their release dates.

## Implementation sequence

| Item | Clauses | Bounded work / files and repositories | Completion evidence |
| --- | --- | --- | --- |
| D1: settle first client contract | CX1–7, CS1–8, CD1–3 | This packet plus TUI references/adoption note; review conflicting terminal/runtime semantics first | Accepted revision and explicit first-release capability list; unresolved promises remain DRAFT |
| D2: extend publication design | CS3, CS5–6 | Unified service publication, client subscriptions and configuration invalidation; preserve existing wire v1 clients | Journey J4 and J5; instrument payload bytes, projection counts, queue depth and latency before/after |
| D3: connected TUI vertical slice | CX1–4, CS1–4, CS7, CD3 | TUI backend/transport boundary and connection setup; consume Unified public API without importing execution into widgets | J1–3 using the actual Ratatui entrypoint and browser; isolate standalone backend selection |
| D4: common action semantics | CX3–5, CS4, CS7 | Operation-to-TUI mapping, capability discovery and supported controls; coordinate Harness/Parity/Chat UI rather than replace their actions | J3/J6; graceful/force/queue/steer behavior documented per supported host; no implicit substitution |
| D5: scoped settings experience | CX6–7, CS2, CS5–6 | Shared actions/state, web settings owner's surfaces, TUI settings adapter | J5, including simultaneous editors and secrets excluded from snapshots |
| D6: rich output and device fallback | CX7, CS8 | Client capability declarations and artifact adapters; coordinate execution-view owner | J7 with meaningful terminal fallback; device-specific actions stay targeted |
| D7: integrated qualification/release | All adopted clauses, CD5–6 | Validation records, compatibility/adoption entries, release dependencies | J1–7 where applicable; explicit remaining gaps and actual released host/client versions |

D2 and D3 can proceed independently once their shared interface is agreed;
D3 can initially use snapshot v1. Neither should claim broad multi-client scale
until D2 is measured. D4–6 use the same operations, not parallel frontend-only APIs.
The first connected TUI milestone includes visible approvals/questions and ownership
blocks even when advanced controls or rich viewers have explicit unsupported states.

## Acceptance journeys

Use isolated fixture workspaces and synthetic histories. Identify separately any
actual provider, personal service or physical-device check. Record client/host
revisions, setup, expected result, observed result and evidence artifact for each run.

| Journey | Scenario | Evidence that would disprove success |
| --- | --- | --- |
| J1: concurrent views | Two browser tabs and two real TUIs; three view A, one views B. Send from each supported client; switch while an acknowledgement is delayed. Keep distinct drafts. | Wrong target, duplicate input/runtime, another view's selection/draft changes, or UI freezes while awaiting reply. |
| J2: reconnect and no viewers | Disconnect all A viewers while work continues; reconnect through the other client type. Interrupt transport before and after acceptance; restart host where safe. | Replay, lost accepted input, duplicated history, restored stale partials or unknown outcome presented as success/failure. |
| J3: ownership and lifecycle | Standalone CLI owns A; connected clients read but cannot write. Explicit takeover; then reverse. Detach one viewer, stop work explicitly, test stop timeout and subsequent admission. | Receipt alone enables writes, draft sends itself, detach cancels work, replacement begins before old execution settles, or timeout removes the fence. |
| J4: scoped load | Repeat J1 with a slow subscriber, then zero A viewers; stream A while B/settings remain open. Exercise large history and earlier pages. | A detail built/sent for uninterested views, unrelated settings rebuilt, unbounded backlog, wrong scroll anchor or blocked input. |
| J5: configuration | Two editors change one project source revision; session A inherits it, B overrides one field. Include idle and busy workers, catalog refresh and a second host. | Lost draft/update, missing conflict, override erased, saved labelled mounted, raw secret published or unrelated host changed. |
| J6: decisions and feedback | Answer the same decision from two clients; delay/fail send and edit the last unsent input; revise earlier input; try async controls and dismiss dialogs. | Two accepted answers, unsafe retry, forced fork of unsent input, silent history loss, no pending cue or dismissal bypassing ownership/approval. |
| J7: native presentation | Actual terminal at narrow and wide sizes, keyboard-only browser, streamed Markdown/tool results and rich artifacts; optional device effect from one client. | Reprinted committed scrollback, missing artifact identity, inaccessible blocking action, color-only status or effect delivered to another device. |

For J4, collect click-to-selection, click-to-readable-history and
send-to-pending-bubble separately from worker-ready and model-first-token latency.
Record cold/warm history, history bytes/count, client count, network and host setup;
report percentiles and sample counts, not one favorable click. Agree numeric
performance budgets from that baseline before calling D2 a performance acceptance.

## Shared change impact record

Include this information in a shared-behavior PR or a linked design note; no new
template is required for an ordinary internal fix that preserves the promises.

```text
Change and user-visible reason:
Contract revision / affected clause IDs:
Consumers and existing owners consulted:
Changed semantics vs unchanged wire fields:
Minimum host/client versions and capability fallback:
Implementation slices, owned files and dependency order:
Acceptance journeys and evidence level:
Unverified cases / migration or rollback constraints:
Release owner and actual deployed-version evidence:
```

## Decisions left explicit

- Agree the first release's graceful stop, force stop, queue and steer capabilities.
  The TUI must retain a clear distinction between them; unavailable operations
  cannot be disguised as equivalent commands.
- Choose scoped revision/subscription extension details after measuring current
  publication. Wire v1 snapshots and global revision semantics remain as documented.
- Choose a useful minimum rich-artifact fallback for the terminal. Voice and canvas
  parity are not inferred from the presence of those features in the browser.
- Review portable session controls with Foundation. Existing native names/history
  support does not establish arbitrary private-module state recovery.
- Leave event-only recovery, provider lazy loading and interchangeable worker pools
  outside this implementation sequence. They are separate or paused work.
