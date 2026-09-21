# Shared client state — v1 (DRAFT)

Clause prefix: **CS**. Parent: [shared-client vision](../docs/clients/VISION.md).
This specifies proposed ownership and synchronization behavior, not a new wire format.

## Who builds against this

Unified service and runtime owners, Foundation maintainers, and all connected client builders.

## What it is

```text
Host + conversation identity -> one execution owner -> canonical session storage
Client A -> conversation X + private presentation + explicit subscriptions
Client B -> conversation X or Y + its own presentation + explicit subscriptions
Configuration: persisted scope -> effective merge -> mounted runtime revision
Command: pending -> rejected / accepted / unknown; accepted -> execution outcome
```

[The scope map](../docs/clients/state-scopes.md) assigns data to these boundaries.
[Wire v1](../docs/clients/live-sessions.md) defines the currently available operations.

## The promises

1. **CS1 — Identify the host and the target.** Commands capture the intended host and
   conversation before waiting. Multiple clients share that conversation's execution;
   detaching a view does not stop it. Client identity is not authentication.
   Broken: switching selection retargets pending input or a second viewer creates a second runtime. Affected: concurrent users.

2. **CS2 — Separate shared state from each view.** Accepted work, questions and durable
   conversation properties are shared; selection, drafts and reading state belong to
   the client. An authorized agent may inspect a targeted nonsecret view without making it global.
   Broken: another window overwrites unfinished input or a secret becomes public view state. Affected: people and agents sharing a host.

3. **CS3 — Publish by interest and dependency.** Detailed conversation updates go only
   to its subscribers; summaries and configuration changes reach their own interested
   subscribers. No viewers requires no detailed view construction. Slow clients cannot stall execution or grow unbounded queues.
   Broken: activity in X rebuilds Y's unrelated detail or every settings panel. Affected: all users as client count grows.

4. **CS4 — Reconcile without repeating work.** Retain command identity and payload
   before sending; distinguish rejected, unknown, accepted and completed. Exact retries
   follow the wire receipt rules and original identity; reconnect restores authoritative state without replaying commands or device effects.
   Broken: a missing reply means failure, a snapshot duplicates messages, or reconnect repeats a tool. Affected: users recovering connectivity.

5. **CS5 — Name configuration scope and provenance.** A setting identifies its host/root,
   global/project/workspace-local/session scope and effective source. Shared scope does
   not imply cross-host replication. A stale edit conflicts or merges explicitly without erasing unrelated updates or local drafts.
   Broken: a workspace override is silently lost or a Mac save is presented as a Spark save. Affected: settings editors and dependent sessions.

6. **CS6 — Separate saved, effective and mounted.** Saving configuration reports where
   it persisted and when it can apply. Affected sessions expose the applied revision or
   pending/restart requirement; unrelated sessions and explicitly overridden fields remain unaffected. Refreshing a catalog is not editing configuration or mounting a provider.
   Broken: saved defaults are labelled active in an unchanged worker. Affected: operators choosing runtime behavior.

7. **CS7 — Preserve canonical storage and exclusive execution.** Foundation mechanisms
   coordinate cooperating hosts using configurable roots. Connected clients mutate through
   Unified; standalone apps acquire ownership before loading writable state. Reacquisition reloads newer history/configuration; lock release does not prove external effects stopped.
   Broken: two owners write, a stale worker overwrites history, or a second store controls the name. Affected: all shared-session participants.

8. **CS8 — Target device effects explicitly.** Microphone, camera, clipboard, local files,
   notification permission and similar effects target a capable authorized client/device.
   Their delivery is separate from shared conversation updates and has an explicit unavailable/unknown result.
   Broken: every viewer opens a microphone or reconnect repeats a clipboard write. Affected: people with several devices attached.

## Not in v1

Execution migration, shared filesystem locking across independent machines, automatic
credential replication, event-log-only recovery, pruning, or exactly-once remote tool effects.
Current snapshot reconciliation remains valid; CS3 does not require incremental event replay.

## How the kit checks it

Exercise [the acceptance journeys](../docs/clients/development-plan.md) with two conversations,
multiple clients, overridden configuration, a slow subscriber, disconnects and an owner handoff.
Measure publication work as well as bytes; no draft receives a formal conformance verdict.

## Open questions

What scoped revision and capability envelopes extend wire v1 without breaking old clients?
Which session controls become Foundation-portable versus host-private capabilities?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-09-20 | Initial draft, CS1–8. | Shared-state discussion and source/owner boundary review. |
