# Amplifier native runtime

`amplifier-native-runtime` owns shared **application lifecycle policy** for native
Amplifier workers. Its first interface is `WorkerReleaseParticipant(worker,
publish)`: the existing stop, settle, checkpoint and cleanup sequence extracted
from Unified's public `amplifier_web/ownership.py` at
`920ad44e199ac685dbd7d7ab40492498934c44fc`.

Foundation continues to own writer leases, release registration and activation
guards. Live continues to own runtime input and execution. This package owns no
history store, agent preparation, ACP transport or conversation transcript.
Importing it starts nothing and does not import Foundation, Live or an app.

## Release participant contract

The consuming native worker supplies its existing `command_lock`,
`shared_handle`, `activation_gate`, `activation`, `runtime`, `execution`,
`naming`, `session`, `controls` and `parked` fields. These are the concrete native
worker interface, not a general lifecycle callback framework. `publish(event)`
is synchronous. Consumers retain their existing registration and platform policy:

```python
from amplifier_native_runtime import WorkerReleaseParticipant

participant = WorkerReleaseParticipant(worker, publish)
participant.registration = await register_release_handler(
    worker.shared_handle, prepare_release=participant.prepare
)
```

`register_release_handler` is Foundation's public session API. A consumer that
does not support handoff on its platform must retain that behavior; constructing
this participant does not enable handoff. Registration must be assigned before
the consumer serves release requests, and closed through Foundation on shutdown.

`prepare(request)` preserves the existing sequence:

1. Under the command lock, check the requested acquisition, mark the worker as
   yielding, bind its activation and submit the existing Live cancel input.
2. Wait for execution without cancelling it through this wait, then cancel and
   join pending naming work.
3. Under the command lock and original activation, checkpoint `cancelled`, close
   controls, clean up the session, retire the activation and park the worker.
4. Return Foundation's `ReadyToRelease`. **Foundation releases the lease last.**
   Publish `yielded` only after its pending release task finishes and the original
   handle is inactive.

The participant never unlocks the writer itself. An ordinary persistence or
cleanup error returns `CannotRelease` and keeps ownership held. Initial stop
submission errors and cancellation propagate as before; this extraction adds no
retry, rollback or claim that cleanup completed after a failure. Consumers keep
their existing admission checks while `yielding` is true. Existing event fields
and failure text are preserved.

## Development and delivery

This directory is independently buildable and has no sibling source imports:

```sh
cd components/native-runtime
uv sync --group dev
uv run pytest
uv build
```

Foundation and Live use their existing public source declarations. A consumer's
lock and compatibility evidence select exact revisions; package metadata alone
does not qualify an arbitrary runtime combination.

This is a **producer-first** extraction. The current Unified and ACP consumers
still use their existing implementations. After accepting this public producer,
each consumer can select its exact public subdirectory source and remove its
local release policy. Unified must update both its host and separately prepared
worker dependency declarations; a host install alone does not update a retained
worker. The standalone distribution must be the only distribution installing
`amplifier_native_runtime`; do not also copy it into the Unified wheel.

The focused tests use real Foundation writer leases/release handlers and Live
inputs with controlled execution, persistence and cleanup. They do not claim a
full app start, provider, Windows handoff, history Load or deployed adoption.
