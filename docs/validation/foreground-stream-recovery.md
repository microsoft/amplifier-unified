# Open-chat transport recovery

An open chat keeps its React tree and presentation identity when its event stream
is replaced. After a chat has loaded, a permanently CLOSED EventSource retries
with a two-second delay, doubling up to thirty seconds until a baseline arrives.
CONNECTING streams retain the browser's native retry. Retries do not restart
hidden pages; returning to the foreground restarts the stream instead.

Hidden-to-visible transitions, online and persisted pageshow events also refresh an apparently
OPEN stream, which can be stale after suspension. Wake events within one second
are coalesced. Recovery obtains a fresh baseline through the existing transport
owner, preserving pending view overlays and mounted Canvas inputs. It does not
reload the page, navigate chats, submit messages or retry user commands.

The production browser check uses a real disposable server and native
EventSource. It closes a stream, rejects the first replacement with HTTP 204,
and verifies recovery. It then simulates a persisted pageshow and repeated wake
signals. A held composer save and a local-only input in the built-in sandboxed
Canvas app survive; the client identity stays the same, subsequent state updates
arrive, and no message/edit/create/select command is emitted.

Local qualification in the owned Linux DTU:

- Production build and 551 frontend unit tests passed.
- The new browser check passed the scenarios above, including ignoring a redundant
  visibility event while already visible.
- The full chat reading browser passed reply anchoring, scrollback, chat switching
  and reload checks.
- The existing outbox browser check passed delayed admission, lost responses,
  receipt reconciliation, pending edits and reload without replay.

An earlier fixture used a custom renderer whose validation Chromium could not
launch inside the DTU. That failure is retained separately; no validation receipt
was fabricated. The final check uses the built-in Canvas app and still asserts
preservation of input that exists only in the browser.

Physical device suspension, iOS Home Screen restoration, host restart, and real
voice handoffs remain separate acceptance work. Simulated lifecycle events do
not substitute for those device checks. Initial connection failure still uses
the existing explicit Retry connection experience.
