# Unified 0.11.6 live-client handoff validation

The deliverable is the [TUI integration contract](../clients/tui-handoff.md) and
a released host/adapter baseline. It does not include an implementation in the
separate amplifier-app-tui repository.

## Revisions and correction

- Unified: 0.11.6, based on 0.11.5 (`8f1b943`). The immutable release tag identifies
  the final host commit.
- loop-live: merged `11a730ac24463cb5bea8bd65385f494aa8f0f454`, selected by both
  the host mount plan and packaged runtime manifest.
- Core 1.6.1 and Foundation `695f875c0908f45f8dc78b1fcde80ecddebffd7c`.
- Local acceptance platform: macOS, Python 3.13, Chromium.

The runtime fix reserves the generation before scheduling its asynchronous
turn. Previously, a concurrent host control could see neither queued input nor
an active generation in that gap, park the worker, and release ownership before
the new turn began. The correction is isolated in
[loop-live PR 5](https://github.com/bkrabach/amplifier-module-loop-live/pull/5);
its main-only suite passed 46 tests with one opt-in skip, and Linux/macOS CI on
Python 3.11/3.13 passed. The experimental Work profile is not required by this
Unified release.

## Repeatable host and terminal checks

`tests/fixtures/live_terminal_probe.py` starts the production HTTP/SSE service,
two public `SessionClient` adapters and a real process-isolated worker using the
pinned Core/Foundation/loop. A local provider supplies deterministic responses
and bounded gates; it uses no real provider account, credential or conversation.

Verified behavior:

- Both clients receive the first response through their live streams.
- After idle and a runtime configuration inspection, a second turn runs.
- Retrying the identical command returns a duplicate receipt and invokes the
  provider once, with one accepted user message and one final response.
- Client A closes while its provider is waiting. The worker remains alive and
  client B receives the eventual result.
- Reattached A reads the completed history without resubmitting input.
- An explicit stop from B cancels an in-flight provider coroutine, retires the
  worker, and is visible to both clients. This does not establish cancellation
  of every external subprocess or reversal of prior tool effects.

Run the probe through pytest with `UNIFIED_RUNTIME_PYTHON` set to an interpreter
containing the dependencies from `amplifier_web/runtime_deps/pyproject.toml` plus
the host package. The opt-in warm-worker test additionally uses
`WARM_FOUNDATION_PATH` pointing to the matching Foundation checkout. Both use
temporary app, settings, ownership and workspace directories.

The browser fixtures independently pass:

- `test:empty-host-browser`: actual draft debounce before a conversation exists,
  no validation error, reload preservation and first send creating one chat.
- `test:live-clients-browser`: independent views/drafts, shared partial/final
  responses, offline catch-up, reload and terminal command deduplication.
- `test:review-during-send-browser`: responsive review during pending admission,
  captured original target, newer completion protection and no duplicate send.

These browser fixtures use a synthetic runtime; the terminal lifecycle probe
uses the real worker with a fixture provider. They provide different evidence.

Final local gates: **932 Python tests passed, 5 skipped**, with the actual
terminal-runtime and warm-worker probes enabled; **152 frontend tests passed**.
All three browser scenarios above passed against the release candidate.
The production frontend was regenerated for 0.11.6. Source archive and wheel
builds passed, and release verification matched their source, version, release
notes and static assets to the tested checkout.

## Coordinated real-provider browser evidence

A separate acceptance task ran two Chromium clients with real Terra inference,
Foundation children and tool execution. Its text interaction passed 11/11 checks
and compaction interaction passed 9/9, including shared streaming, an accepted
correction, offline completion, reconnect and separate drafts. Screenshots were
visually inspected by that task.

Those runs used Unified 0.11.4 (`9112dc3`) and loop fix candidate `171414e`.
The loop implementation and regression test were checked byte-for-byte equal
to merged `11a730a`. They are supporting live-model evidence, not a claim that
the final 0.11.6 release was itself rerun against every provider/profile.
The detailed sanitized report lives in the loop repository's
[Work profile validation](https://github.com/bkrabach/amplifier-module-loop-live/blob/ee09dba/docs/work-profile-validation.md).

Voice is outside the TUI text-client gate. The coordinated audio acceptance
passed a direct spoken correction, while a different forwarded phrasing failed
one correction-intent check. Physical microphone/speaker/barge-in behavior was
not established. No broad voice reliability claim follows from these runs.

## Boundaries

- Actual TUI event-loop/rendering integration still needs its own acceptance.
- No current user preview, Spark service or existing session was restarted for
  this validation. The stale local preview needs its own draft-safe update.
- Saved receipts, history and existing ownership remain in use. Event-log-only
  recovery, compaction replay after restart, tool-effect reconciliation and
  execution migration are not implemented by this milestone.
- Network snapshots are current projections, not a durable replay of every
  intermediate event; application/authentication failures require explicit
  handling by the client.
