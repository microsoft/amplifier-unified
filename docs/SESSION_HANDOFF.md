# Cooperative ownership with CLI and other hosts

Unified's execution worker owns the Foundation session lock. Web, native, and
terminal clients use the service's shared command surface. `session.takeover`
is the deliberate **Continue here** action; selecting or reconnecting a chat
does not request another application's release.

When a CLI or another compatible host owns the chat, Unified requests a safe
release through Foundation and reacquires before loading/continuing. A warm
worker must check ownership again: its earlier ready notification does not prove
that it still owns a parked session. A competing new owner is never silently
asked to yield under the original request.

When another host requests release, Unified blocks new input, stops the live
loop and dependent work, checkpoints through its existing native persistence,
cleans up, invalidates its activation, and releases last. Failure to save leaves
ownership held with a visible failure. Other sessions continue.

The worker publishes `runtime.ownership` transitions. App state exposes an
`ownership` object with `status` and optional `source`/`detail`; the UI reflects
read-only, moving, taking-over, and failure states without discarding drafts.
After yielding, deliberate `session.takeover` is required before resumption.
Ordinary idle parking retains its existing reacquisition behavior.

The protocol uses configurable Foundation coordination roots and local private
endpoints. It does not change native history, introduce checkpoints, migrate
execution to another machine, or provide complete event-only recovery.

For cross-host development verification, install local Foundation and CLI
overrides and the configured live-loop package into an isolated test virtualenv,
then run `tests/test_session_handoff.py`. Its reciprocal lifecycle fixture uses
real Foundation locks and native history without calling a model provider.
