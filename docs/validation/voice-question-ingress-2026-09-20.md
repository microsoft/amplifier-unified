# Voice answer after reconnect: complete adapter path

The isolated `voice_question_acceptance.py` run passed **16 checks** on Unified
integration base `89c301e68570c3eb619fbfb6f4aa27620aa4f2f4`. It used five local
scripted provider calls and two local voice connections; paid calls: **0**.
The adjacent JSON contains exact synthetic question, input and source-message
identities and the SHA256 of the executed harness. No product source changed.

The test starts the production host and actual worker/loop with a deterministic
local provider. It creates a required question through the public action API,
connects through `/api/voice/connect` and `/api/voice/ready`, and sends protocol
transcription/delegation events over an actual local aiohttp WebSocket. Only the
remote HTTP/WebSocket transport is redirected. Production `VoiceCall.receive`,
`handle`, `record`, `execute`, `voice_delegate`, worker input admission and mounted
`app_control` tool execution are exercised unchanged.

The first call performs an independent tool read while the question stays
pending. The entire host/worker is shut down and restarted, then a second call
reconnects to the same task while another task is selected with an unsent draft.
Its spoken-answer event creates the real voice transcript. The main worker reads
app state and calls `question.answer` with that exact `sourceMessageId`. Assertions
verify the voice ID/item ID, original text, user-origin provenance, saved answer,
accepted delivery and actual worker result. A function result returns over the
real sideband receive/send path.

Duplicate wire events and duplicate delegation IDs admit one voice input and one
question-answer input. Repeating the actual answer command returns its existing
receipt without delivery. A second full host restart retains the accepted answer
and does not replay it. Selection/draft remain intact; no permission approval is
created. Exact SDP CRLF bytes survive the signaling adapter.

Run in the existing development environment with current loop-live installed:

```sh
python scripts/work_profile/voice_question_acceptance.py --output /tmp/fresh-voice-question-fixture
```

The output path must not exist. It contains private synthetic state and a public
report; the harness closes all host workers and sockets. Preparation attempts
corrected harness API routing, fixture configuration and current-utterance
parsing before the final passing run. They are not counted as passing evidence.
No account settings or production homes were read or changed by the harness.

This proves real host voice ingress, reconnect, delegation, tool execution and
answer provenance. **It does not test physical microphone capture, WebRTC media,
audio quality or a remote speech model.** The local voice fixture supplies
transcription/delegation events rather than performing speech recognition.
