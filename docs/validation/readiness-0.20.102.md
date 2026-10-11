# Workspace and feedback readiness — 0.20.102

This candidate follows 0.20.101 (`c9780695f6aa17743b149a0be16efb0e66cd8f48`).
All application checks run inside the project-owned Linux DTU
`dtu-unified-public-readiness-20261008`, using `/opt/unified-readiness102`,
independent dependencies and synthetic state. No shared installation or user
workspace was changed. Physical Mac/Windows/WSL, microphone acceptance, broad
CPU work and the AHP/ACP track remain outside this delivery.

## Changes and coverage

| Issue | Delivered behavior | Evidence |
| --- | --- | --- |
| #451 | Read another registered chat's schedules without selecting/hydrating it; mutations retain caller ownership | Schedule API tests, including nonempty peer results and unchanged selection/drafts |
| #236 | Historical imported workspace warnings remain quiet until that workspace is selected or used | Update source classification tests |
| #264 | Ordinary attachments can target a reviewed public repository; consent binds repository ID, visibility and exact file hashes | Initial upload tests and real UI with mocked GitHub |
| #294 (partial) | Public/private follow-up attachments use separate immutable review and durable phase receipts | Follow-up attachment tests, including concurrent issue changes and lost-response reconciliation |
| #422 | Formatted Markdown copy with exact plain-text alternative; no raw HTML, remote images or viewer controls | Browser clipboard checks for headings, lists, tables, code, denial and text-only fallback |
| #295 | Reveal an artifact on the named owning host, with explicit remote/no-desktop alternatives | Native argument, host identity, missing-path and stale-instance tests; real Thunar selection in a private DTU display and D-Bus session |
| #291 | Opt-in retained execution summaries, timestamps and identifiers in frozen Markdown exports | Export range, privacy and snapshot tests; summaries exclude raw arguments/results and are not a complete execution audit |
| #286 | Archive/search/restore workspace registrations without deleting files or chats | Backend persistence tests and browser archive/search/reload/restore journey |
| #289 | Storage overview/drilldown and reviewed permanent workspace-file cleanup, retaining/archiving chats | Bounded accounting, overlap/link/config/active-work rejection, interrupted receipt, restart/no-replay tests and browser deletion in synthetic folders |
| #283 | Explicit mark-unread synchronized across browsers; already-open peer tabs cannot immediately acknowledge it | Backend attention tests, frontend tests and two independent browsers |
| #273 | Per-chat plan progress, recorded trajectory and expandable todo details in the workspace chat list | Canonical todo receipt tests and browser plan expansion |
| #274 | Cmd-comma on Mac / Ctrl-comma elsewhere opens Settings and preserves the draft | Chromium simulation of the Mac modifier contract; physical PWA acceptance is separate |
| #292 | Explicit fallback voice model/name, off option and actual connected model/voice disclosure | Provider failure classification and settings tests; no fallback after uncertain call allocation; physical voice acceptance remains pending |

Storage shows logical file sizes with unique inode accounting, not physical free
blocks. Incomplete, linked and unreadable scopes are labelled. Shared CLI data
is read-only. Archive does not reclaim space. Cleanup reviews the exact path and
inventory, warns about Git/uncommitted files, requires typed-path confirmation,
refuses active/overlapping/configuration-bearing folders and journals before
mutation. Interrupted cleanup never automatically replays; its recorded original
and quarantine paths require inspection. Restoring a registration cannot restore
deleted files. Managed-chat history deletion remains a separate existing flow. Hosts without
symlink-safe, descriptor-relative folder removal retain inspection/archive and
show cleanup as unavailable before mutation.

## Other tracked work

- **Automatic Gemini images:** one real generation, zero retries and zero chat
  requests, using a scoped process-only Google credential. Empty image settings
  selected the existing Gemini connection by priority. The real provider and
  image tool produced and saved a 1376 × 768 PNG through `outputs.attachImage`.
  Model: `gemini-nano-banana-2.1`; 96,498 bytes; SHA-256
  `fdb8b56fb2a368034050a745d89c98c31d5d03e6d7fa48f5fb01ab10ebdca4d1`.
  Provider source: `5eb610ab0894acd082cbeaeb527421ef70476d98`;
  image tool source: `ad077609c84cb2e2b4ce971ddd16c504080ca937`.
  This verifies provider/tool/default-selection/persistence integration, not a
  complete first-chat inference or physical-device check.
- **First-use journey:** browser checks cover returning to zero connections,
  assistant setup handoff, a single new chat, Settings in Canvas, retained
  navigation on reload and narrow/mobile layouts. These are scripted journeys,
  not uncoached novice research.
- **Appearance/accessibility:** keyboard activation, immediate duplicate-save
  guard, retained drafts, failure/retry, inactive completion, forced colors,
  reduced motion and narrow layout pass in the browser fixture.
- **Original recovered conversation:** synthetic repair/identity coverage remains
  available, but this delivery has no scoped copy of the user's original failed
  conversation. It does not claim original-data continuation acceptance.

## Explicit remaining work

### #297 — provider preparation isolation

The deployed Core dependency is 2.0.1. Isolation requires the before-load hook
and rollback behavior merged upstream in Core PRs 117 and 116, respectively:
`e78bcad13f82c64c8f84243a00c8d9f90d8b876a` and
`ca9b393bd78626391fa48ab8a3cb3938914f8624`.
Those changes are not in the published wheel used by this release. Unified
intentionally installs Core from native wheels and forbids building it during
installation. No unrelated failed account is pruned or silently substituted.

Next: consume a published Core wheel with both hooks; integrate explicit host
admission with Foundation's provider-failure policy and routing availability;
qualify exact account identity across root/child/default/named/role/glob paths,
partial mount rollback, two accounts sharing a module, and resume. This issue
remains open rather than shipping an unproven failure-isolation policy.

### #294 — replacement of original GitHub title/body

Reviewed later attachments are delivered. True original title/body replacement
remains open: GitHub issue edits provide no atomic conditional revision update,
so read/check/PATCH can overwrite an intervening maintainer edit. The existing
append-only correction workflow preserves concurrent changes and remains the
supported operation. No unsafe PATCH or replay of an unknown edit was added.

## Final candidate checks

The broad backend run completed with 6,124 passing and 201 skipped tests. Its
five failures were three missing test-runtime imports, one process retaining the
previous version during the version bump, and a genuine voice fallback admission
regression. The latter is fixed: native task ownership is checked again after a
provider rejection, before a fallback request. With the declared test runtime
installed, all 120 voice, voice-transfer, provider-recording and release-note
checks pass. The skipped runtime/platform integrations are not counted as passes.
The final storage suite passes six tests, including unsupported-host rejection.

Frontend unit tests: 579 passed. Final two-browser workspace/storage, formatted
clipboard, initial public feedback upload, attachment follow-up component,
first-use setup and appearance/accessibility checks passed. GitHub was mocked in
feedback checks; no test issue or attachment was published. The follow-up
component check is not a claim of full installed two-client HTTP/SSE acceptance.

For #295, `canvas.views.reveal` ran against real Thunar 4.18 through
`org.freedesktop.FileManager1.ShowItems` in owned Xvfb display `:182` and a private
D-Bus session. The native window opened and copied exactly the target file URI
from its selection; the file contents remained unchanged. The private display,
bus and desktop processes were retired after the check. This used synthetic
files and the private display's clipboard, not the host clipboard. Physical
Mac/Windows checks remain separately deferred.
