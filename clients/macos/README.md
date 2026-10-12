# Amplifier Companion for macOS

An initial native client for an existing Unified host. It shows work in the menu
bar and an optional movable glass companion, delivers native notifications, and
sends a notification reply to the chat that produced it. It does not contain an
agent runtime or install, restart, or replace the Unified host.

Status: developer preview implementation. macOS device acceptance and public
distribution are separate remaining milestones. The initial app uses the neutral
name **Amplifier Companion**; Arc, Relay, and Crest are selectable visual designs.

## Build

On macOS 13 or later with Xcode command-line tools and Swift 5.9 or later:

```sh
cd clients/macos
bash build-app.sh
open 'dist/Amplifier Companion.app'
```

The build tests the shared client logic and produces an arm64/x86_64 universal
app and ZIP. Without `COMPANION_SIGNING_IDENTITY`, this is an ad-hoc signed local
developer build, not a notarized public download. The separate macOS CI workflow
builds the same preview and retains its ZIP as an artifact. It does not publish a
release or use distribution signing credentials.

## Connect

Use an isolated candidate Unified host with this branch's `/api/companion`
endpoint. In Settings, enter its HTTPS origin and its control token, obtained by
the host owner from `<Unified data directory>/config/auth/control-token`. HTTP is
accepted only for loopback addresses, including an explicitly configured SSH
tunnel. Normal system certificate validation stays enabled. The token is saved
in macOS Keychain and never placed in a browser URL, preferences, or logs.

This first developer slice uses the existing **full control credential**, not a
restricted companion credential. Consumer pairing and revocation UX is needed
before general distribution. It does not alter or import CLI credentials.

Choose a design and enable notifications if desired. Notification permission is
requested only on that action. Pinned chats are the default notification scope;
“Include unpinned chats” expands it. No message body, question text, or tool
arguments are displayed in a notification. Existing activity establishes the
first connection's baseline instead of generating a historical notification burst.

Drag the glass form to reposition it; double-click to open Unified. Hide/show is
available in the menu bar, and the position is retained. Motion follows working,
waiting/offline, idle, or needs-attention state; near-cursor tracking uses the
sixteen generated directions. macOS Reduce Motion uses a static frame. All art
remains abstract, without eyes, faces, feet, or limbs.

## Replies and navigation

- Only completed-response notifications offer **Reply**. Questions and approvals
  offer **Open chat**, preserving their distinct answer/authorization semantics.
- The exact host, chat, text, and command UUID are saved before sending. Accepted
  means the host accepted the command, not that the agent finished its work.
- An uncertain reply stays in the menu's saved replies section. There is no
  automatic resend on app launch, reconnect, timeout, or host restart. **Retry**
  reuses the original ID and payload. Check the chat first.
- A duplicate OS callback reuses the saved entry. Changing the configured host
  cannot retarget an old reply; durable host identity is verified before sending.
- Opening a chat uses `?chat=<id>` in the authenticated web UI. This selects the
  chat only in that new browser client. No token or command travels in the link.
- Quit disconnects the companion; host work continues. The app does not start at
  login automatically and does not request Accessibility or screen-recording access.

Private delivery and notification state lives in
`~/Library/Application Support/Amplifier Companion/journal.json` (owner-only).
A corrupt journal blocks sending instead of silently discarding deduplication
identities. Keep the file for recovery. Deleting it can remove saved replies and
notification history; this app offers no automatic reset.

## Architecture and acceptance still needed

The standalone Swift package separates `CompanionCore` (transport, observation,
durable delivery, URL handling) from AppKit/UserNotifications. The adapter targets
the [shipping HTTP contract](../../docs/clients/live-sessions.md), not an assumed
AHP migration. It can move to a separate client repository without importing
Python host internals. The small host projection uses existing session/attention
indexes, is bounded to 50 chat rows and 200 notices, and never hydrates histories.
It polls every five seconds, backing off to sixty seconds when disconnected;
there is no full-state event stream or per-chat subscription fanout.

Before a consumer release: scoped device pairing/revocation; Mac notification
reply and cold-launch acceptance; VoiceOver, Reduce Motion, sleep/wake, multiple
displays and Spaces; measured idle CPU/memory; pending-reply review and deletion;
Developer ID signing, hardened-runtime/notarization/stapling; update delivery and
clean uninstall. Windows remains a separate native adapter. No production
installation or live-state adoption is performed by this change.

Apple API references: [text-input notification actions](https://developer.apple.com/documentation/usernotifications/untextinputnotificationaction),
[notification responses](https://developer.apple.com/documentation/usernotifications/untextinputnotificationresponse),
[NSPanel](https://developer.apple.com/documentation/appkit/nspanel).
