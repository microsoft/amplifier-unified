# Conversation diagnosis and recovery

`session.inspect {id}` returns the app and runtime IDs, workspace, bundle, current status and a bounded, classified failure. Provider failures are captured at the public provider boundary before loop-live replaces the exception with its no-replay wrapper. Raw SDK requests, exception bodies and credentials are not copied into diagnostics. Older conversations can be diagnosed from their own saved provider error events; a missing cause stays explicitly unknown.

`session.recover {id}` creates an idle independent root conversation. It retains readable user/assistant history, voice references, artifact references and configuration. Native tool calls/results become labelled historical text, and image/provider-specific blocks are excluded from the new model context. The original transcript and UI history remain untouched. Goals, approvals, jobs and ownership are not inherited. No message is sent and no tool work is replayed. Computer-use safety stops remain in effect. Reattach images needed for future work.

Both actions are shared by the UI and agent action surface. Details and copyable IDs remain available under the conversation settings after an error notice is dismissed. Recovery is disabled while work or configuration changes are active.

## Context Intelligence library assessment

Reviewed Microsoft `amplifier-bundle-context-intelligence` at `9d9759d6357ffc99fdbfb9e040136996b6c4ca9c`:

- `context_intelligence.read_native_transcript(CaptureLocator, TranscriptRequest)` reads bounded, lossless pages of captured prompt/response text, with explicit partial/error states. Use it for recovering conversation text when the canonical transcript is missing; do not implement another parser for those events.
- `extract_transcript`, `extract_events`, `extract_metadata`, and `discover_sessions` reconstruct session history from a configured CI server. They need server access and captured content. Reconstruction must remain a separate explicit operation with source/completeness evidence.
- `context-intelligence-recover` durably delivers native captures that have not reached a configured server. It repairs telemetry delivery, not a model's conversation context.

The reported Spark failure has an intact canonical transcript. CI's local reader was exercised against a private capture copy: it returned five captured conversational messages, while the canonical context had 65 rows including tool exchanges. Reconstructing or re-uploading those records would not repair a provider that encodes a computer-tool halt message as image bytes. Unified therefore uses Foundation's existing `SessionHistoryStore` for this recovery copy, retains the original evidence, and supplies only the host-specific portable-context policy. No CI networking or telemetry replay is triggered by inspection or recovery.

## Known limit

This change makes failed sessions diagnosable and provides a safe continuation path. It does not change the OpenAI provider's native computer-result encoder. That encoder must separately reject non-image strings and preserve safety-stop/error semantics without manufacturing a screenshot. A new computer failure can therefore still stop a future turn until the provider/computer-use integration is corrected.

## Update source warnings

Unregistered bundle names on unselected imported history appear under older conversation settings, with workspace, native session ID and an Open conversation action. They do not raise an application configuration error or disable cached-source updates. A current selected conversation, enabled app bundle, module source or unreadable settings file still produces an actionable warning. All original bundle choices and transcripts are kept.
