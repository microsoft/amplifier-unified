# One Amplifier owner, three input channels

The voice connection owns audio and conversation timing. It has one backend operation: delegate the current spoken request to the existing main AmplifierSession. Both GPT-Live client delegation and GPT-Realtime's `amplifier_delegate` reach the same host inbox used by typed messages and steering. App state inspection, skin changes, task routing, worker creation, tools, approvals, and further model reasoning happen inside the configured Amplifier bundle and loop-live. Passive compact UI context is sent to the voice model so references such as “this worker” remain meaningful; it grants no separate tool path.

A sideband event or an assistant text block is not a completion receipt. The portable loop-live change adds identified manager generations. Hosts resolve an input only from `generation.finished.input_ids`, and distinguish the returned final assistant text from `active_job_ids`. A turn can answer a side question while workers continue. New voice corrections enter the inbox immediately instead of waiting behind earlier voice-result waits. When a later worker report produces another manager generation, an active call can speak that verified update. Hanging up closes the media connection and preserves accepted work.

The host journals accepted voice operation identities before scheduling. Repeated provider event identities are ignored; reuse with conflicting content is rejected. This is execution deduplication, not automatic recovery/replay of interrupted work. Reconnection starts a new media session attached to the selected Amplifier conversation and does not reconstruct old tasks from transcript prose.

## Concrete comparison with Astra Live Workbench

The inspected source is `bkrabach/astra-live-workbench` revision `354a04414bc4ed942fe35d2813bafa4e8574391f`.

| Actual source behavior | Amplifier host decision |
| --- | --- |
| `agent/task-manager.mjs` calls a separate `routeIntent` model, then creates isolated task runners. | Routing belongs to the main AmplifierSession. Its bundle can delegate independent worker sessions without adding a second voice-side reasoning agent. |
| `agent/managed-session.mjs` returns background job handles, delivers corrections on valid continuation boundaries, and collects completed jobs once. | Loop-live already has background delegate handles, a durable job ledger, and request-boundary steering. Preserve those mechanisms through a standalone Foundation host rather than reimplementing a second worker engine. |
| `agent/session.mjs` tracks native steering as queued, accepted, and applied when a successor response arrives. | The portable core now separates accepted input identities from delivered/applied identities. Existing optional native adapters need an explicit `steering.applied` signal at the confirmed successor boundary. Acceptance alone cannot complete the user's request. |
| `voice/realtime.mjs` schedules completion speech only when response, playback, and user speech are idle; result narration disables tool use. | Adopted for Realtime's verified-result continuation. Its only executable function remains delegation into Amplifier. |
| `agent/task-manager.mjs` removes stale queued notices by task revision before speaking. | Current host deduplicates generation notices and uses final correlated manager output. Per-worker revision invalidation of already queued audio remains a separate gap; transcript evidence and current app state remain authoritative. |
| Astra's Live and Realtime close handlers call `manager.cancel`, stopping all workspace tasks. | Do not copy this lifetime coupling. The user's requested call/text/chat design requires work to survive hangup. |
| Astra supports native model-specific response steering and imported job handles during explicit worker handoff. | Foundation/provider compatibility does not automatically confer those capabilities. The app must report actual runtime support. Conventional providers retain request-boundary steering; native handoff is not claimed by this change. |

## Portable upstream change

The reviewable changes in `repos/amplifier-module-loop-live` touch only its runtime/orchestrator, tests, and generation-event documentation. They add no CLI, application-adapter, or provider-SDK imports and retain ordinary finite execution without a live runtime. The core's full fixture suite passes. No upstream publication or push was performed.

For an installable host before an upstream release, package the reviewed core source with its original base revision, exact patch, and content hashes. Pin the streaming engine dependency to the core's existing commit. Once reviewed upstream, replace the local patch with an immutable released revision and rerun the same generation-correlation fixtures. Do not depend on mutable working-tree paths or require the CLI adapter at runtime.

## Verification limits

These tests establish local protocol and lifecycle behavior, not live audio quality or model access. Native provider steering, cancellation outcomes, worker handoff, and recovery require capability-specific end-to-end evidence. The completion event proves the manager turn returned; it does not certify that every background operation completed or that the user heard the audio.
