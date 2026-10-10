# Realtime result narration at a speech boundary

When backend work finishes during user speech, its result can be added to the
voice conversation immediately, but narration must wait for the user's voice
response and audio playback to finish. Ending speech does not mean the provider
is idle: server VAD creates a response after that boundary.

`VoiceCall` owns this scheduling. It keeps a pending VAD response distinct from
active response generation and playback. With interruption disabled, VAD can
skip automatic creation while another response is active; that case must not
leave a task result waiting indefinitely.

The behavior follows the [Realtime VAD documentation](https://developers.openai.com/api/docs/guides/realtime-vad)
and the [server event contract](https://developers.openai.com/api/reference/resources/realtime/server-events).
Live commentary uses a different provider protocol and is unchanged.

## Regression coverage

`tests/test_voice.py` supplies synthetic provider events to the actual call
handler and observes outbound protocol messages:

- A task completes during speech; speech stops before `response.created`.
- The same sequence interrupts an earlier response, whose completion and
  cleared playback arrive before the new response starts.
- Interruption is disabled and VAD skips a response while one is active.

The first two sequences fail without the pending-response guard: the app sends
a competing `response.create`. With the guard, narration waits for the user's
response and playback, sends once, and never replays backend work.

These are deterministic protocol regressions, not a measurement of live
provider timing or physical audio quality. Microphone interruption, Bluetooth,
mobile handoffs and physical-device acceptance still need separate evidence.
