# Optional native provider responsiveness

Unified retains request-boundary steering by default. A bundle can explicitly
set `session.orchestrator.config.native_provider: true` to use the optional
**provider-owned** `amplifier_module_provider_openai.native` adapter. This host
contains no Responses wire-protocol implementation. The installed provider must
include that module and its `native` optional dependencies; the existing runtime
already includes WebSockets. During local review use a local provider source
override; after the provider change is merged use its existing `@main` source.
No models, defaults, credentials, or source pins are changed by this feature.

Only the currently selected OpenAI `gpt-6-astra` instance at the official endpoint
is wrapped, before normal observation/capacity/surface wrappers. A missing module,
unsupported model/endpoint, preexisting instance wrapper, or disabled option
reports ordinary boundary steering. A later selection change immediately uses ordinary transport and requires
remount before that new identity can use native transport. Changing effort does
not reuse the old native identity or poison it as an uncertain send. Finite children retain their
ordinary execution path.

The Limits & context panel and `runtime.control` share `native.status` and
`native.compact`. Status is descriptive. Explicit compaction requires the session
and dependent work to be idle, a prior successful native request, and a settled
transport. It makes one provider request through the same capacity admission and durable
call receipt path as ordinary completions. Reported token counters are retained;
missing usage stays unknown. Missing accounting prevents compaction. An active
agent cannot compact its own
in-flight generation; it must defer until idle. Agent native controls are bound
to the calling session, including through the generic runtime-control alias.
Client-supplied actor labels do not grant authority.

Acceptance means a correction is queued. Only an observed successor response
marks it applied. Identity-only receipts are stored privately before sends and
on actual provider events. Restart reconciles submitting/running/pending state to
**unknown**, never success/cancelled. Unknown transport is not automatically
resumed or replayed; use explicit session recovery, or explicitly change transport
configuration. A locally cancelled await or closed connection cannot establish
remote cancellation. The provider rejects unsupported automatic-compaction /
steering combinations; there is no silent summary fallback.

Private files under the owned session control directory:

- `native-provider-state.json`: bounded 128 identity-only protocol receipts and
  current outcome, with host session/generation/request binding.
- `native-provider-checkpoint.json`: the provider's opaque full JSON compaction
  window, capped at 16 MiB and bound to exact source prefix/config/model/instance.

Original native transcript storage is checkpointed before deriving a window.
Opaque values never appear in app state, tool results, the panel, or native
status. The host does not interpret/reconstruct hidden reasoning and does not
restore this format through context-managed summaries. An incompatible prefix,
request fitter result, or configuration discards only the derived state. Normal
history remains intact; compaction does not select a chat or alter drafts.

Validation includes provider protocol fixtures and a production-build app/Chromium fixture using shared controls and a synthetic SDK response. Desktop/mobile, agent status, reload, private payload exclusion, selection and draft retention are covered. Separate bounded live Astra checks observed accepted/applied steering and the completed successor. An additional continuation restored the original opaque checkpoint in a fresh provider and consumed it with only the new user input; no historical tool result was resent, no tool executed and no new compaction or retry occurred. Saved model defaults remained unchanged.

The first retained-checkpoint acceptance failed because turn-scoped serialization removed old reasoning when a new user arrived, changing the wire prefix. That failure is retained. The provider fix verifies the unchanged canonical/config binding and exact former wire prefix, permits only removal of old reasoning, and slices the actual current prefix. Changed text, tool results, policy or request fitting still reject checkpoint reuse. The corrected single live continuation returned the expected result with217 total reported tokens; this bounded result does not establish recovery of an interrupted remote generation.

A failed native computer result still fails closed in the current turn. Only a later explicit user message permits the provider's bounded textual history projection of that failed pair, without changing canonical history, replaying the action or clearing a safety halt. Remote cancellation confirmation remains a separate limitation.

Bounded acceptance reports: [steering](validation/native-provider-live-smoke.json), [retained initial failure](validation/native-continuity-live-report.json), [offline exact-state verification](validation/native-continuity-offline-report.json), and [corrected live continuation](validation/native-continuity-resume-report.json). Opaque checkpoint contents and credentials are excluded.
