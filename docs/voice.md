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

### Mobile audio and interruptions

The browser requests `navigator.audioSession.type = "play-and-record"` where available for the duration of a call, restoring its previous audio mode on hangup or failed setup. Supported Media Session `togglemicrophone` and `hangup` controls use the same shared actions as the app buttons. System metadata shows only “Amplifier voice call”, without a chat title or transcript.

Audio-session and microphone interruptions are displayed without ending the call or changing the user's mute choice. Returning to the page or recovering audio retries playback on the existing audio element; it does not request another microphone stream, reconnect the provider, or replay messages. If playback requires a gesture, **Resume audio** retries it. A stopped microphone requires ending the call and starting again. Actual navigation away still ends browser media.

**Keep screen awake** remains an optional foreground fallback. Audio Session and Media Session provide audio integration and supported system controls, not permission to run indefinitely in the background. Screen lock, power saving, Bluetooth routing and incoming calls must be checked on physical devices. Installing a PWA alone does not establish that guarantee.

References: [Audio Session specification](https://www.w3.org/TR/audio-session/), [Chrome Media Session call-control sample](https://googlechrome.github.io/samples/media-session/video-conferencing.html).

Physical acceptance checklist (not yet executed): on iPhone Safari and its installed PWA, and Android Chrome and its installed PWA, disable the screen-awake fallback and test a locked screen through a quiet interval; then test Bluetooth car audio, an incoming-call interruption, a background/foreground transition, and battery-saving mode. Confirm both incoming audio and microphone capture, preserved mute, no duplicate transcript/delegation, and controls cleared after hangup.

Tests cover the delegation-only boundary, completed-generation correlation, pending-worker status, coalesced-result deduplication, continued steering, background notices, protocol-specific signaling and sidebands, fallback classification, missing configuration, pinned sessions, transcript deduplication, correlated results, and call/work lifetime separation. No paid API requests are made by the automated tests. Actual audio, account-specific model access, latency, and browser microphone/playback behavior must be verified with a real call.

Sources verified during implementation:

- [Live WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live)
- [Live delegation](https://developers.openai.com/api/docs/guides/live-delegation?delegation-mode=client)
- [Server controls](https://developers.openai.com/api/docs/guides/voice-server-controls)
- [Live session API](https://developers.openai.com/api/reference/python/resources/live/methods/create)
- [Realtime call API](https://developers.openai.com/api/reference/typescript/resources/realtime/subresources/calls/methods/create)

The local Relay example also informed lifecycle and sideband separation.

## Call controls

Mute disables microphone capture; it does not cancel speech already playing or work already accepted. End call closes audio and leaves accepted work running. While the call’s conversation has active work, its call strip also offers **Stop work**, which invokes the shared conversation stop action for that conversation even after navigating to another chat. The call stays connected so the user can give another instruction. The control reports **Stopping work…** while cancellation settles. It does not undo completed actions or guarantee that an external operation already submitted can be recalled.
