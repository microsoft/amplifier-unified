# Voice transport

Calls use browser WebRTC for microphone/speaker audio and an authenticated server sideband for transcripts, app context, and Amplifier delegation. Set `OPENAI_API_KEY` in the terminal that launches the app. API keys stay on the Python host. A call remains pinned to the conversation that started it even when another conversation is selected in the interface.

`gpt-live-1` is the default preference and is tried first with the Live `/v1/live/sessions` JSON protocol and client delegation. Live delegation events contain metadata rather than request text; the server uses its accumulated user transcript to call the main Amplifier session. Results return as Live commentary only after loop-live publishes the identified completed manager generation for that input. Intermediate assistant content blocks do not complete requests. Realtime uses `/v1/realtime/calls` multipart signaling and its own function-call protocol with `gpt-realtime-2.1`. Its only function is `amplifier_delegate`. Reasoning, app inspection, controls, and work all enter the main AmplifierSession through loop-live; neither voice adapter executes app tools directly.

Selecting GPT-Realtime-2.1 as the saved preference connects directly to Realtime. Automatic fallback is limited to model/access availability errors. Invalid API credentials, rate limits, invalid configuration, and generic server failures are reported instead of concealed by trying another model. Reconnect to retry. There is no synthesized speech/transcription agent fallback in this version.

The host persists voice transcripts in the same conversation. Voice fragments are delivery fragments, not precise speaker turns. Both adapters receive current app context as the interface changes. Full app state and controls remain available through the main Amplifier app tool, behind its normal orchestrator and approval boundary. Live context append chunks respect the documented per-event limit conservatively.

Ending a call asks the provider to finalize, closes browser media, and leaves accepted Amplifier work running. A result arriving after hangup remains in the conversation without trying to speak through a closed connection. A false `finalized` flag means the call closed locally but final provider accounting was not confirmed. Closing the whole host process remains a separate application shutdown.

## Endpoints

- `GET /api/voice/config`: credential availability and model names, never credentials.
- `POST /api/voice/connect`: `{sdp, sessionId?, provider?: "auto"|"live"|"realtime"}` → `{id,sdp,provider,model,sessionId,fallbackReason?}`.
- `POST /api/voice/ready`: `{id}` after both media and data channel connect.
- `POST /api/voice/end`: `{id?}` → `{closed,finalized,workContinues}`.

All routes use the application's existing local authentication and origin checks. Browser `VoiceClient` takes the authenticated JSON `request` wrapper. Native microphone permission still belongs to the browser/operating system; agents can request a call but cannot grant that permission.

## Validation

Tests cover the delegation-only boundary, completed-generation correlation, pending-worker status, coalesced-result deduplication, continued steering, background notices, protocol-specific signaling and sidebands, fallback classification, missing configuration, pinned sessions, transcript deduplication, correlated results, and call/work lifetime separation. No paid API requests are made by the automated tests. Actual audio, account-specific model access, latency, and browser microphone/playback behavior must be verified with a real call.

Sources verified during implementation:

- [Live WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live)
- [Live delegation](https://developers.openai.com/api/docs/guides/live-delegation?delegation-mode=client)
- [Server controls](https://developers.openai.com/api/docs/guides/voice-server-controls)
- [Live session API](https://developers.openai.com/api/reference/python/resources/live/methods/create)
- [Realtime call API](https://developers.openai.com/api/reference/typescript/resources/realtime/subresources/calls/methods/create)

The local Relay example also informed lifecycle and sideband separation.


### Ending a call

`call.end` uses the same host action for the UI and agent. UI hang-up releases
microphone and speaker immediately. Agent actions default to `graceful: true`;
callers can explicitly pass `graceful: false` for an immediate stop.

A graceful end pins the call ID and stops accepting new voice delegations while
allowing an already admitted answer up to six seconds to return. The browser
mutes capture, keeps output connected, then drains for up to twelve seconds:

- Realtime waits for `output_audio_buffer.stopped` (or a cleared buffer), with a
  short transport tail. `response.done` and transcript completion are insufficient.
- Live has no output-audio-done event. The browser observes decoded media, waits
  for 1.2 seconds of quiet after sound, and includes output latency. This is a
  bounded best-effort pause, not proof of a complete utterance or audible delivery.
  Missing/blocked media evidence falls back to the timeout.

The host has an independent twenty-second deadline if the browser disappears.
Then normal provider finalization runs (at most eight seconds); final usage is
confirmed only when the protocol acknowledges it. An explicit hang-up, page exit,
or connection failure can interrupt draining. Ending voice never cancels admitted
Amplifier work, and final transcripts continue to be recorded during finalization.
No goodbye text matching, new cues, or automatic semantic hang-up is introduced.

Protocol references: [Live lifecycle](https://developers.openai.com/api/docs/guides/live-conversations)
and [Realtime server events](https://developers.openai.com/api/reference/resources/realtime/server-events).

`npm run test:voice-ending-browser` checks real Chromium MediaStream/Web Audio
playback with a synthetic tone. It does not make a provider call or prove physical
speaker/headphone delivery.
