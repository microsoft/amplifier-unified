# Shared client observations: review acceptance

The review identified unreachable items after fixed cutoffs and history loading
being counted as agent execution. The revised observation slice replaces those
implementations with generic cursor resources and shared lifecycle/attention
helpers. See [the public contract](../clients/observations.md).

## Owned environment and checks

Final combined-source validation on main `cb8946ac1` (Unified 0.20.122):
302 host tests passed, along with the chat-link parser, both browser acceptance
checks and the independent browser-link production build.

Checks run on spark-2 in the owned DTU `unified-macos-companion`. Observation
source/dependencies are isolated at `/opt/tempo-main-integration`; independent
browser-link acceptance uses `/opt/tempo-chat-links`. Hosts bind ephemeral DTU
loopback ports and use synthetic state, credentials and runtime only. The
existing port 8994 native preview and main/shared installations are unchanged.
Ownership, revisions, evidence and exact cleanup are in the workspace manifest.

- The host regression suite covers generic API and legacy adapter traversal beyond
  50 sessions and 200 notices, including 205 approvals in one chat; bounded and
  sparse pages; query/revision/tamper rejection; incremental large counts; ETags;
  authentication/origin/client requirements; no history hydration; correct
  idle/loading/preparation/root/delegate semantics; shared read/fingerprint races;
  acknowledgement without approval; and explicit reply/stop/approval targets
  after selection changes, same-ID retry and changed-payload rejection.
- Shared web attention/navigation and existing live-client regressions exercise
  the refactored canonical helpers. Browser acceptance independently covers links
  through login, unavailable/deleted targets, reload and multiple windows.
- A real web client creates a manual unread marker, reads the generic summary and
  catalogs, acknowledges the exact fingerprint and verifies selection is retained.
- The synthetic construction benchmark traverses 10,000 sessions and 20,000
  notices. On this DTU, with allocation tracing enabled, it required 13 count
  batches, 50 session pages and 100 attention pages; the maximum measured request
  was 53 ms, total count construction 0.64 s, and additional traced peak memory
  807,666 bytes. These measure projection construction on synthetic metadata,
  excluding the preexisting catalog and network. They are not native idle usage
  measurements or an end-to-end latency guarantee.

## Reproduce inside an owned DTU

```sh
uv sync --locked --group dev
.venv/bin/pytest -q tests/test_client_observations.py tests/test_companion.py \
  tests/test_live_clients.py tests/test_completion_attention.py \
  tests/test_disconnected_attention.py tests/test_active_client_projections.py \
  tests/test_chat_navigation.py tests/test_navigation_summary.py \
  tests/test_pinned_chats.py tests/test_questions.py tests/test_observations.py \
  tests/test_server_auth.py
.venv/bin/python tests/fixtures/client_observation_benchmark.py
cd frontend
npm ci
npm run build
node --test tests/chat-deep-link.test.mjs
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/companion-browser.mjs
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/observations-browser.mjs
```

Node 22 and Playwright Chromium are required. Separate browser instances are used
for the authenticated and sign-in cases because Chromium single-process mode
cannot safely host multiple independent contexts. Test fixture startup errors and
synthetic-catalog teardown errors encountered while extending coverage were fixed
before recording final acceptance; they were not product failures.

## Explicitly separate work

The browser-link PR can land independently. The observation PR uses current
owner-authorized access; it introduces no scoped credentials or public native
setup/distribution flow. General device enrollment and capability enforcement
are a separate delivery slice with its acceptance criteria in the client contract.
No AHP/ACP adoption, account migration, production deployment, native-device
revalidation, or merge is included in this work.
