# Live-client integration verification

This candidate combines independent live clients, the modular shell from PR #58,
management/history latency fixes from PR #59, and the work-harness streaming
bridge. It builds on the released 0.10.9 issue fixes and prepares 0.10.10.

## Client behavior

The production browser fixture uses two independent Chromium contexts against
one real authenticated HTTP/SSE server with a synthetic runtime. It verifies:

- Independent selected conversations, per-conversation drafts, settings panels,
  and restored page identities.
- Shared accepted input, partial assistant output, and completed responses.
- Offline/online catch-up without submitting again or stopping host work.
- A third terminal-protocol participant and duplicate command delivery producing
  one execution and one displayed input.

Backend tests also cover delayed admission while switching conversations, late
draft saves, independent attachments and canvas tabs/controls, scoped device
effects, competing approval responses, persisted receipts after host restart,
authentication checks, shell identity/restoration, and separation of shell
invalidations from dedicated session streams. The Python adapter is exercised
over real HTTP/SSE, including a Unicode snapshot larger than the transport's
usual single-line buffer.

Browser regressions cover the coordinated delayed-input fixes, chat composer and
attachments, workspace exploration, and modular-shell production hot-loading,
validation, replacement, independent instances, recovery, and presentation
changes while chat/draft/iframe/work remain intact. Browser control helpers now
explicitly identify the page they are controlling, including after reload.

## Runtime and storage

The isolated runtime uses loop-live merged commit
`7a2a9b9ed0ddf2f1927f5d0e7aba42698c1e4c8d`, whose tree matches the tested feature
head `2f08207b1aede0cebd789d86907dd3e4ef2d8fa5`; loop-streaming is pinned to
`603aa6eefacd28367fc886d58eb89de993c58dea` and context-simple to
`2bc8b15770f4ecb49bd6216a8b5336e9c36adfc6`. The host plan and packaged runtime
dependency file select the same loop-live revision.

Opt-in real Core/Foundation probes exercise child lineage, checkpoint resume,
approvals, delegate compatibility, persistent steering, provider instances,
runtime controls, multimodal input, ephemeral canvas instructions, and warm
session reuse. Providers are local fixtures; these are not live-model or voice
acceptance tests. Existing persistence and Foundation ownership remain in place.

## Scale and packaging

A production fixture with 22,500 summaries and 4,000 workspaces passed bounded
navigation, actual Capabilities bundle reads, and no-model-call checks. Its
single-sample run measured a 559,230-byte state response, 133 ms state retrieval,
146 ms view update, and 2,411 DOM nodes. These local fixture measurements are not
production latency guarantees.

The frontend has a deterministic production build, and release verification
compares wheel contents with the tested Python source and generated assets.
Source archives exclude development environments and node dependencies.

An initial concurrent full run had one MCP subprocess startup timeout. All 18
MCP tests passed in isolation afterward; final release checks use a stable build
without concurrent browser performance jobs.

## Boundaries

- The tests simulate two devices with browser contexts; no Spark instance was
  updated or restarted for verification.
- The dedicated SSE contract reconciles complete current snapshots. It does not
  provide durable incremental event replay.
- The common API and Python adapter are ready for TUI integration; no separately
  hosted amplifier-app-tui repository is changed here.
- Event-log-only recovery, compaction replay, execution migration between hosts,
  and multi-device voice arbitration remain separate work.
