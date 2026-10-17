# Voice status development preview (#36)

This is unmerged development work on the 0.20.176 baseline. It does not change
spark-1 or claim microphone, speaker, provider access, audio quality or audible
interruption acceptance.

## Contract and current qualification

[Voice ownership contract](../voice-amplifier-boundary.md) defines the exact
read-only exception, pacing, media fencing and generation-tail limits.

| Flow | Released 0.20.176 observation | Preview target and current result |
| --- | --- | --- |
| Status during a held real worker | A second input is submitted; no immediate answer; two local provider calls after release | Passed: immediate host observation, one provider call, no second accepted input |
| Short work | No host pacing policy | Passed: no progress before five seconds; no late wait when work settles |
| Long work / phase changes | Provider instruction encourages brief wait acknowledgements | Passed controlled clock: work-span grace survives phase chatter, phase cooldown, distinct finite elapsed observations, then silence |
| User speaks during a pending wait | No typed host progress slot | Passed: discard the single unsent slot before any provider item |
| Playback / VAD / response busy | Existing result continuation arbitration | Passed: progress waits before provider items, resumes scheduling when flushed; verified result wins |
| Call replacement / transfer during a send | Media observations lacked the new status fence | Passed held-send controls: already handed-off item retained as evidence, no subsequent stale speech request |
| Stop / idle / truncated attention | No narrow host status phrase policy | Passed: no false completion; incomplete counts cannot claim no active work |
| Hangup during accepted work | Work survives | Passed in unchanged and changed actual-worker proofs; exact generation remains in chat, no post-hangup speech |
| Rapid generation-tail gap | Observer tracking grows with call | Passed: bounded tail/anchor, no historical replay, explicit incomplete unsolicited coverage; exact accepted result still delivered |
| Real Live/Realtime acoustics and timing | Not measured here | Not run; requires separately authorized real provider/audio/device acceptance |

The focused candidate run currently has **148 passed, zero skips** across voice,
status projection, real-worker and integration-owner tests. The local provider is
synthetic and credential-free, but Core/Foundation, loop-live, the subprocess,
accepted input and generation boundary are real. Initial implementation failures
and the fixed additive-owner oracle remain in the task evidence; they are not
relabeled as passes. Final preview/browser and independent review evidence are
reported separately when completed.

## Repeatable owners

- Ordinary unit collection and the public-readiness owner run
  `tests/test_voice.py` and `tests/test_voice_status.py`. The existing status
  emitter tests cover the reused pure resident attention facts.
- The native integration owner includes
  `tests/test_voice_status_worker.py::test_voice_status_held_native_worker_never_replays_or_cancels`.
  It requires `UNIFIED_RUNTIME_PYTHON` with the repository's declared native
  runtime dependencies. Missing prerequisites remain an explicit skip in ordinary
  collection and a failure under the required native owner. The historical 87
  skip records remain unchanged.
- `tests/fixtures/voice_status_worker_probe.py ROOT --baseline` runs the same
  held-worker control using a separate unchanged 0.20.176 source tree. Keep its
  output distinct from the candidate proof; use a short private temporary root
  for native ownership sockets.
- `tests/fixtures/voice_status_review.py` produces seven controlled-clock
  scenarios using the actual policy and VoiceCall transport classes. Its fake
  socket records provider events, not sound.

## Retained review instance

`tests/fixtures/voice_status_preview_server.py ROOT PORT` is a development-only
fixture. It runs the ordinary app with a real worker and a local synthetic
provider, disables background updates and schedules, and uses private state.
No account credentials are needed. Its extra `GET/POST /api/voice-preview` route
requires the instance's owner bearer credential, rejects other input fields,
and accepts only `{"provider":"live"}` or `{"provider":"realtime"}`. Each
result contains policy, seven scenarios, controlled timestamps and captured
transport events. It has no microphone or speaker side effects and is absent
from the shipping server routes.

The retained preview's exact source, environment, listener and evidence ownership
are recorded in the workspace manifest fragment. Review and eventual adoption
are separate decisions; this branch must not be merged or released by its owner.
