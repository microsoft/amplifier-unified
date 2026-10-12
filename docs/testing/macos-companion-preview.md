# Amplifier Tempo host and browser acceptance

Host integration: PR #567. Latest main was merged at
`d14afec6d` (Unified 0.20.120) on October 12, 2026. Generated browser assets were
rebuilt from the combined source; no upstream implementation was discarded to
resolve the generated-file conflicts.

The native app is maintained separately in
[bkrabach/amplifier-temp](https://github.com/bkrabach/amplifier-temp)
(private access required). Tempo does not package or install a Unified host.

## Behavior and compatibility

- `GET /api/companion` requires existing host authentication and an attached
  client identified by `X-Amplifier-Client`. It returns protocol version 1,
  stable host identity, host instance ID, work counts, up to 50 active/pinned
  chats and up to 200 unread chat notices, with explicit truncation flags.
- Summaries use existing indexes, omit conversation bodies, prompts, tool
  arguments and paths, and do not hydrate chat history or select a browser chat.
  Internal chats are excluded. Repeated unchanged observations reuse a cached
  projection; attention acknowledgements invalidate the result.
- Replies use the existing session command API and stable command IDs. No new
  execution path, provider configuration, or background worker is introduced.
- A `?chat=<session-id>` link selects that chat in the opening browser client,
  removes the parameter after successful selection, and never submits a turn.
- Browser clients do not poll the companion endpoint. Ordinary browser launch
  does not issue a new selection command or compute companion summaries.
  Users without Tempo have no new setup step or notification behavior.

## Verified on the merged source

All application checks ran on spark-2 inside the owned synthetic DTU
`unified-macos-companion`, using a separate source copy at
`/opt/tempo-main-integration` with its own Python and frontend dependencies.
Existing native acceptance preview service/port 8994 was left running unchanged.
No shared application or real account state was used.

- 137 distinct host tests passed: companion observation, live clients and reply
  targeting/deduplication, server authentication, completion/disconnected
  attention, and active-client projections. The first run passed 136 tests;
  the companion module passed all three tests after adding the on-demand and
  browser-state regression case (two overlap with the first run).
- All 604 frontend unit tests passed, including bounded chat-link parsing.
- Production frontend build passed with Node 22 and locked dependencies.
  Build identity: `2b0959f3ba8ce90f`. Vite reports its existing large-chunk warning.
- Real-host Chromium acceptance passed: exact target selection, query cleanup,
  selection retained through reload, another window's selection retained through
  reload, no chat submission, and no browser requests to `/api/companion`.
- Observation tests verify authentication, required client attachment, origin
  rejection, bounded payloads, no history hydration, no private content leakage,
  read-notice removal, caching, and preservation of browser selection/draft.

Reproduce inside an owned DTU with Node 22 and Playwright Chromium available:

```sh
uv sync --locked --group dev
.venv/bin/pytest -q tests/test_companion.py tests/test_live_clients.py \
  tests/test_server_auth.py tests/test_completion_attention.py \
  tests/test_disconnected_attention.py tests/test_active_client_projections.py
cd frontend
npm ci
npm test
npm run build
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/companion-browser.mjs
```

The browser fixture starts a temporary host on a DTU loopback ephemeral port,
uses synthetic authentication and an isolated browser context, and cleans up at
exit. Resource ownership, revisions and cleanup are recorded in the workspace's
`WORKSPACE-MANIFEST.json`; logs are in `output/macos-companion/main-integration/`.
These checks do not constitute the entire repository suite or a performance
measurement.

## Remaining native acceptance and adoption

Native UI, artwork, packaging and release acceptance belong to the Tempo
repository. The private preview currently uses a manually supplied full-control
host token; browser pairing and restricted, revocable device credentials remain
future work. Native banner reply presentation, cold launch, accessibility,
sleep/wake, multiple displays and measured idle resource consumption still need
complete acceptance. Developer ID signing, notarization and update/uninstall
experience are separate native distribution work.

The installed Mac still connects to the existing synthetic preview. This PR
neither changes that connection nor deploys to the main host. Main-host adoption
and any account-data acceptance remain separate explicit decisions.
