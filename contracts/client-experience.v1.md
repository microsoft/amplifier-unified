# Shared client experience — v1 (RATIFIED 2026-10-02)

Clause prefix: **CX**. Parent: [shared-client vision](../docs/clients/VISION.md).
These are proposed observable behaviors, not a claim of current parity.

## Who builds against this

Web, TUI, Amplifier for Mac and future Windows client builders, the Unified host,
and acceptance reviewers. [DC](desktop-client.v1.md) specializes desktop journeys.
Platform implementations retain their own presentation contracts.

## What it is

```text
Choose host/workspace/conversation -> read and follow -> compose -> send
  -> pending delivery -> accepted -> working / needs input -> settled
Disconnect -> reconnect -> reconcile the same conversation
Ownership blocked -> Continue here -> acquiring -> acquired / still blocked
```

The state and delivery meanings come from [CS](client-state.v1.md).
The current HTTP/SSE spelling remains in [the wire contract](../docs/clients/live-sessions.md).
The target AHP/ACP boundary follows [HP](host-protocol.v1.md) and [AP](agent-protocol.v1.md).

## The promises

1. **CX1 — Orient and navigate consistently.** Every client makes its execution
   host, workspace when present, and conversation discoverable. People can start
   standalone work, find required attention, and retrieve saved conversations
   without a mandatory project choice or knowledge of agent internals. Selection, creation,
   rename and history navigation remain available; selection responds locally
   while loading/preparation continues.
   Broken: returning requires reconstructing context, a click leaves the old
   selection unexplained, or reading needs an execution lock. Affected: people finding work.

2. **CX2 — Make sending feel immediate and remain truthful.** Send clears the submitted
   draft into one provisional bubble while preserving subsequently typed input. Delivery
   states and safe retry follow CS4; editing a definitively unsent final input need not fork.
   Broken: the submitted text lingers as a draft, disappears on failure, or appears twice. Affected: people composing work.

3. **CX3 — Give controls the same meaning.** Send, queue, steer, stop, force stop,
   answer, detach and takeover remain distinct. Clients expose only supported semantics;
   pending questions are answerable once and conflicts refresh the shared result.
   Broken: Detach stops work, Stop claims to undo effects, or an unsupported force stop is silently substituted. Affected: operators and observers.

4. **CX4 — Put ownership at the point of action.** A blocked composer shows its owner
   and explicit Continue here action, preserves the draft and remains blocked while
   acquisition is pending or fails. Successful acquisition alone never sends the draft.
   Broken: a distant banner is the only cue, or dismissing it enables a conflicting writer. Affected: users switching standalone and connected hosts.

5. **CX5 — Preserve meaning while revising history.** Earlier-message editing offers an
   explicit fork or replacement continuation when supported; abandoned history and artifacts
   remain inspectable. A revision does not undo or replay tool effects. Pending delivery must be resolved before editing its command.
   Broken: editing silently forks, destroys retained evidence or races an uncertain send. Affected: users correcting their instructions.

6. **CX6 — Show work where it happens.** User-initiated noninstant actions change
   state immediately. Routine background refresh retains useful content silently
   and preserves layout. Initial loading and failures remain distinguishable and actionable.
   Dialogs dismiss by outside click and Escape, preserving recoverable drafts; dismissal never approves, cancels accepted work or bypasses a blocking state. Feedback is keyboard-accessible and not color-only.
   Broken: a pressed control looks idle, a refresh silently blanks options, or a dialog requires its X. Affected: all interactive users.

7. **CX7 — Keep capabilities intelligible across surfaces.** Shared concepts and action
   meanings survive different layouts. Configuration uses CS5–6. Artifacts retain identity,
   label and a usable reference; unsupported rich/device actions are explained with an available handoff. Agent actions use the same authoritative operations.
   Broken: a terminal silently loses an artifact or a client-local shortcut grants different authority. Affected: people and agents changing interfaces.

8. **CX8 — Minimize interaction cost without overwhelming the interface.** Treat UX
   like golf: fewer strokes to achieve the intended result wins, provided choices
   remain understandable and accessible. Expose small, frequently used choice sets
   directly; reserve disclosure for large sets or uncommon complexity. Apply
   reversible preferences immediately. Explicit submission remains appropriate for
   drafts, credentials, destructive actions and actions with external effects.
   Removing a click must never remove required authority, meaningful feedback or
   recovery. Evaluate the whole journey, including menu opening and repeated setup,
   alongside cognitive load and keyboard/focus behavior. Frequently used actions
   may appear on independently scrolled surfaces when that avoids return travel;
   their labels and behavior remain consistent.
   Broken: two clicks to choose between two finishes, an unnecessary Save after a
   harmless preference, or a crowded screen that makes the shortest path harder to find.
   Affected: everyone completing routine tasks.

## Not in v1

Identical widgets, compulsory voice/video/canvas rendering in a terminal, or a new runtime.
The exact graceful/force stop support matrix remains to be agreed with runtime owners;
CX3 forbids misleading equivalence while that work is incomplete.

The approved [maintenance pause contract](../docs/maintenance-pause.md) distinguishes
holding new work, proven native suspension, explicit resume and emergency stop.
Support is qualified per runtime/owner; a cleanup grace period is not safe-boundary
suspension and a global resume cannot override a later user Stop.

## How the kit checks it

Use the [acceptance journeys](../docs/clients/development-plan.md), with browser and actual
terminal evidence, delayed responses, failed/unknown delivery and keyboard operation.
For interaction changes, record the entry point, intended result, before/after
action counts, and any justified disclosure or confirmation. Check keyboard
equivalence and discoverability; few clicks alone do not establish good UX.
Document/link checks establish structure only; ratification establishes no implementation verdict.

## Open questions

Which rich-artifact handoffs are required for the first connected TUI release?
Which existing names should be normalized without making platform commands unfamiliar?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-10-20 | Identify desktop consumers and DC; preserve CX1–8. | [Desktop coordination record](../docs/clients/desktop-clients.md). |
| 2026-10-20 | Apply the accepted Home/sidebar UX amendment. | [Product-owner decision](../docs/ux/decisions/20261020.md). |
| 2026-10-13 | Add CX8: interaction economy without clutter or lost authority. | User direction: UX is golf; minimize strokes. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Link target protocol owners; preserve existing experience promises. | [Architecture direction](../docs/architecture/README.md). |
| 2026-09-20 | Initial draft, CX1–7. | User journeys and current implementation assessment. |

## Storage measurement scope

Automatic workspace accounting includes registered strict descendants of the
configured workspace root and app-managed workspace files. A remembered session
working directory is not authority to recursively measure its containing home,
volume, or parent directory. Do not enumerate the configured root wholesale or
require a project to have a session before counting it. External project files
are excluded from this view; their retained conversation history remains in
Session data. State the scope and configured root in the UI. A changed root
invalidates prior scan cursors and reuse; measurements never authorize deletion.
