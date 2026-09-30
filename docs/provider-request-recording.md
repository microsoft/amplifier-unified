# Provider request recording

Unified displays request details from a provider's recorded `llm:request`
event. It does not reconstruct a supposed wire request from chat messages.
Without the provider's raw logging option, the event normally contains only
model, counts, and other metadata, so the request cannot be displayed later.

## Enable recording

Open **Settings → Advanced → Troubleshooting & recovery → Diagnostics**, select
**Record provider requests for troubleshooting**, and save. This option is off
by default. The agent-side equivalent is `diagnostics.configure` with
`config.providerRequests: true`, retaining the other desired configuration.

The setting is stored in this app instance's private `diagnostics/config.json`.
It supplies a default to supported provider instances in the root and all
nested agent declarations, and to separately prepared child overrides. Existing
provider-specific options, including explicit `false`, retain precedence.
Naming calls use the mounted providers and receive the same policy.

Changing the setting makes idle workers prepare again for their next turn; it
does not interrupt an active call. Old calls cannot gain data that was never
recorded. Provider declarations retain the provenance of host-injected defaults
outside the adapter's config, so disabling the setting also removes those
defaults from saved snapshots and resumed children. Explicit provider opt-ins
continue to apply.

Requests can include messages, instructions, tool arguments and attachments.
Unified scrubs recognizable credentials, including known credential environment
values, before the community logging hook observes the diagnostic copy. It
limits each captured raw field to approximately one million characters (plus
JSON formatting), bounds depth, and marks truncation. Redaction failure replaces
the raw copy with a capture-unavailable marker; it does not interrupt inference
or forward the unsanitized raw field. Neither redaction nor bounding modifies
the actual provider request. Arbitrary private text is still private text;
credential redaction is best effort, not a guarantee that a log is public-safe.

Some provider raw flags also enable response recording. The same redaction and
size bound applies to those raw response fields. Existing session logging and
bundle exporters remain in control of storage and external delivery; this
setting does not enable app forwarding or create a new destination. Shared
event files keep their existing retention behavior and are not deleted when
capture is disabled. The app's metadata-index retention limits do not delete
native session logs.

## Adapter audit

Audited all eleven Microsoft provider entries in the current upstream
[`amplifier/docs/MODULES.md`](https://github.com/microsoft/amplifier/blob/main/docs/MODULES.md)
on September 29, 2026, as well as the app's configured/provider-environment
composition paths. A read-only configuration inspection confirmed that raw/debug
capture was not enabled and no app diagnostics configuration existed. No runtime
state or credentials were changed.

The revisions below are the source evidence for this implementation, not pins
to apply to user installations.

| Module | Audited revision | Applied option | Actual emitted evidence |
| --- | --- | --- | --- |
| provider-anthropic | `9e2f20c9342253666d0bdb3dcf593a58456e72f9` | `raw` | Redacted SDK parameters in `llm:request.data.raw`. |
| provider-openai | `887923d9f2607d5097ccdb59effa4ba8a7c5c81a` | `raw` | Redacted final request parameters in `data.raw`. |
| provider-openai-chatgpt | `4a6ffdc11483a4192e1514189916fc4a91c202ed` | `raw` | Redacted built request payload in `data.raw`. |
| provider-azure-openai | `00a3cf982778376cfe094b2a31bbdaa0e137365c` | `raw` | Inherits OpenAI request capture. |
| provider-chat-completions | `a0b0f03793c3a46f1d047e84d49b893d3e901a44` | `raw` | Redacted chat-completions parameters in `data.raw`. |
| provider-gemini | `e191990d3c7e031086ec1a40ed1f23fdac943cac` | `raw` | Structured Gemini request parameters in `data.raw`; `debug` and `raw_debug` are not substitutes. |
| provider-github-copilot | `65ea0628654ad4d72a3abf41e3b7e22e576ee74a` | `raw` | Adapter summary: prompt/system lengths, tool schemas and settings. The SDK owns the final wire request. **Not full prompt/wire capture.** |
| provider-ollama | `036626853c196521c2c12d6f5d11e3cfdc8affc0` | `raw` | Request parameters in `data.raw`, for streaming and nonstreaming. Unified adds redaction before recording. |
| provider-vllm | `0fe391bde630a527d563e9e3f95d4d2e1acf7079` | `raw` | Redacted request parameters in `data.raw`. |
| provider-litellm | `92f2ac5d627c28311eb7a096bca5d651934666d0` | `raw_debug` | JSON string in **`data.raw_request`**, already limited by the adapter to 16,384 characters. Unified now indexes this alternate field. This is LiteLLM input, not necessarily the downstream provider's final HTTP body. |
| provider-mock | `a90821f6b3767f8e52df5e754a96c20352684b43` | None | Debug emits a separate `llm:request:raw` count summary. No actual network request exists; Unified does not pretend that enabling debug creates one. |

The setting covers every audited Microsoft real-provider adapter. Unknown/custom adapters
keep their own declared policy until their contract is known; Unified does not
invent unsupported flags or reconstruct missing payloads. Provider-emitted
`raw` or `raw_request` fields are still available for these adapters when
recorded. An upstream Copilot SDK/adapter feature would be needed for true full
wire capture; this Unified change cannot recover it. No upstream adapter change
is required for the other audited capture paths.

The catalog also lists three community providers. Their current contracts were
checked on September 30, 2026; they are not silently given Microsoft adapter
flags or included in the capture claim above.

| Community module | Audited revision | Current boundary |
| --- | --- | --- |
| provider-bedrock | `61ef9ca84e9877c425258aaae7ded0c20ddf3281` | Requires both `debug` and `raw_debug`, and emits a separate `llm:request:raw` event with `params`. Supporting it requires an explicit event normalization and redaction path; the app does not enable this alternate logging path. |
| provider-perplexity | `768fa6c1ca0371af660e1dbb0c6806943c579d79` | Emits request counts, with no raw request capture flag or payload. Provider support is needed before the app can display an actual recorded request. |
| provider-openai-realtime | `6a27a86183d62d5122f09065cc1ff9144becdeab` | Uses `provider:request` and `provider:response` events and an older response contract. These are not `llm:request` capture and require separate compatibility work. |

These community paths remain unchanged. Their own configured logging behavior
is not covered by the app's `llm:request` and `llm:response` redaction hook.

## Verification and acceptance boundary

- Backend tests cover config validation/private storage, all adapter defaults,
  explicit opt-outs, nested declarations, actual root/child preparation,
  Foundation snapshot round trips, and configuration invalidation.
- A real Core hook registry proves the diagnostic copy is scrubbed before a
  priority-100 capture hook, that raw request objects remain unchanged, and that
  redaction failures do not leak the original payload.
- Event-log tests prove LiteLLM's existing recorded field survives lazy readback
  exactly, without reconstructed history, and retain redaction/truncation status.
- Frontend interaction tests cover default-off, explicit save, disabling again,
  and independence from app forwarding. A browser test verifies save/reload on
  desktop and mobile, with no horizontal overflow.
- No paid inference or live account calls were used. Adapter coverage here is
  source-contract coverage plus host tests, not a claim that every current
  account/endpoint was exercised. Release and installed-worker
  acceptance must be verified separately after integration.
