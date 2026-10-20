# Desktop client experience — v1 (RATIFIED 2026-10-20)

Clause prefix: **DC**. Parent: [shared-client vision](../docs/clients/VISION.md).
Records the accepted desktop behavior for independently released clients.
Approval of the promises is not evidence that every platform implements them.

## Who builds against this

The Unified host, Amplifier for Mac, future Windows client maintainers, and
desktop acceptance reviewers. Platform appearance, packaging and OS integration
belong to each client. [CD](client-development.v1.md) owns change coordination.

## What it is

The workspace, quick interaction and optional companion are ways into the same
host-owned work. A client can hand off to the web workspace while its native
workspace is still planned. Shared state and command recovery remain in
[CS](client-state.v1.md); common action meanings remain in [CX](client-experience.v1.md).

```text
Host attention -> desktop indicator -> exact chat -> focused reading
  -> canonical read receipt -> desktop attention clears
Quick reply -> saved intent -> host admission -> accepted / rejected / unknown
Passive companion interaction -> previous application retains keyboard focus
Explicit Open chat / Compose / Settings -> deliberate focus transition
```

## The promises

1. **DC1 — One attention authority.** Desktop updates consume the host's
   [attention receipts](../docs/clients/observations.md), not a second unread store.
   Work alone is distinct from attention; delivery deduplication is local.
   Broken: reading the latest response leaves a second unread badge indefinitely.
   Affected: people moving between desktop, web and mobile.

2. **DC2 — Status survives presentation choices.** Attention, error, work, idle and
   disconnection retain their meanings across artwork, themes and platforms.
   Text or symbols explain status without relying on color or animation alone.
   Broken: changing an avatar changes whether a status appears to need review.
   Affected: people recognizing work and assistive-technology users.

3. **DC3 — Passive interaction preserves focus.** Optional companion controls,
   scrolling and dragging leave the current application's keyboard focus intact.
   Opening a chat, composer or settings deliberately changes focus.
   Broken: collapsing notices makes subsequent typing disappear into the client.
   Affected: people working in another application.

4. **DC4 — Navigate to the intended work.** Chat activation follows the shared
   [chat-link behavior](../docs/clients/chat-links.md); supported warm handoffs
   reuse the intended workspace without reloading it. A link is not a read receipt.
   Broken: repeated notification clicks multiply windows or acknowledge unseen work.
   Affected: people opening updates and operators of the host.

5. **DC5 — Optional surfaces add no authority.** Hiding the companion leaves the
   client's connection and notification policy intact; quitting ends its client
   connections, not accepted host work. Chat pairing grants no local browser or
   desktop execution. Broken: enabling artwork grants device control or hiding it
   cancels work. Affected: desktop users and other observers.

6. **DC6 — Recovery keeps the destination.** Quick and notification replies use
   CS1/CS4 with the captured host/chat and durable command identity. Reconnect,
   wake and repeated callbacks never silently resend old uncertain input.
   Broken: a recovered reply goes to the current chat or executes twice.
   Affected: people replying while offline or changing connections.

## Not in v1

Identical Mac/Windows widgets, colors, badge geometry or update frameworks.
Embedded workspace and local device execution require separate capability and
acceptance work; neither is established by this contract or chat pairing.

## How the kit checks it

Run [DJ1–DJ7](../docs/clients/desktop-clients.md#shared-acceptance-journeys) on each
actual platform against identified host/client builds. Record source, fixture,
physical-device and released-install evidence separately. A Mac pass does not
qualify Windows; missing physical checks remain open.

## Open questions

Which Windows integration and installation mechanisms best satisfy these journeys?
What minimum host release can be established by a focused compatibility matrix?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-10-20 | Name DC1–6 and separate shared desktop behavior from platform presentation. | Product-owner approval to align vision/contracts across the Mac client and Unified, including the planned Windows experience; existing desktop requirements preserved. |
