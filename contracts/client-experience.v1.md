# Shared client experience — v1 (DRAFT)

Clause prefix: **CX**. Parent: [shared-client vision](../docs/clients/VISION.md).
These are proposed observable behaviors, not a claim of current parity.

## Who builds against this

Web, TUI and future native client builders, the Unified host, and acceptance reviewers.
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

## The promises

1. **CX1 — Orient and navigate consistently.** Every client identifies the execution
   host, workspace and conversation, and offers discoverable selection, creation,
   rename and history navigation. Selection responds locally while loading/preparation continues.
   Broken: a click leaves the old selection unexplained, or viewing a chat needs its execution lock. Affected: readers switching work.

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

6. **CX6 — Show work where it happens.** Noninstant actions change state immediately;
   refreshing regions retain useful content with an updating cue. Failures remain actionable.
   Dialogs dismiss by outside click and Escape, preserving recoverable drafts; dismissal never approves, cancels accepted work or bypasses a blocking state. Feedback is keyboard-accessible and not color-only.
   Broken: a pressed control looks idle, a refresh silently blanks options, or a dialog requires its X. Affected: all interactive users.

7. **CX7 — Keep capabilities intelligible across surfaces.** Shared concepts and action
   meanings survive different layouts. Configuration uses CS5–6. Artifacts retain identity,
   label and a usable reference; unsupported rich/device actions are explained with an available handoff. Agent actions use the same authoritative operations.
   Broken: a terminal silently loses an artifact or a client-local shortcut grants different authority. Affected: people and agents changing interfaces.

## Not in v1

Identical widgets, compulsory voice/video/canvas rendering in a terminal, or a new runtime.
The exact graceful/force stop support matrix remains to be agreed with runtime owners;
CX3 forbids misleading equivalence while that work is incomplete.

## How the kit checks it

Use the [acceptance journeys](../docs/clients/development-plan.md), with browser and actual
terminal evidence, delayed responses, failed/unknown delivery and keyboard operation.
Document/link checks establish structure only; drafts receive no formal conformance verdict.

## Open questions

Which rich-artifact handoffs are required for the first connected TUI release?
Which existing names should be normalized without making platform commands unfamiliar?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-09-20 | Initial draft, CX1–7. | User journeys and current implementation assessment. |
