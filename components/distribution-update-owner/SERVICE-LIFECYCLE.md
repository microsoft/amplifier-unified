# Owned POSIX service lifecycle (0.9)

This opt-in service owner controls the Node application it actually launched.
It does not adopt an existing system service, supervise a process by PID, or
automatically restart after a crash. Windows and non-Node entrypoints are refused.
Linux/macOS use the inherited child IPC channel; installed acceptance currently
covers macOS, with Linux deployment and complete application owner coverage still
requiring qualification.

## Scope of this adapter

This document records the existing inherited-Node-child adapter and its bounded
qualification. Its per-participant service fences, reverse release sequence and
supervisor/application interpreter equality are existing implementation details,
not requirements for the installation-wide lifecycle in
[MA1–MA6 and MA12](../../contracts/maintenance.v1.md). Preserve their existing
meaning for legacy receipts and fixtures. A new platform adapter must qualify
complete process-tree custody and independently qualified role entrypoints,
with shared application admission/drain and a single installation activation
authority. Direct child exit alone does not satisfy that contract. Existing
archive, transfer and cleanup proofs remain separate and unchanged.

## Composition

`runSupervisor` and `runProductionSupervisor` accept optional trusted configuration:

```ts
serviceLifecycle: {installationId, ownerId}
```

Keep both identifiers stable in the private installation configuration. When a
provisioning authority is present, installationId must match its allocation.
The production factory supplies the authenticated `ports.service` host client.
Custom composition must provide the same public service host port explicitly.
Omission keeps service commands unavailable. The fresh installer does not silently
enable this option for an application graph whose participants are unqualified.

The supervisor injects installation and owner identifiers into the child launch.
The host combines them with its verified runtime identity:

```ts
const identity = await inspectRuntimeService(actualRuntimeIdentity);
const serviceLifecycle = {
  identity,
  verifyRelease: createHostServiceReleaseVerifier({
    service: supervisor.service,
    inspectRunningService: () => inspectRuntimeService(actualRuntimeIdentity),
  }),
};
```

Pass this to the host's `quiescence.serviceLifecycle`. Each required participant
must declare `serviceStop: {version: 1}` and actually implement that contract.
The host refuses missing coverage before acquiring any participant. This is a
separate service-stop admission and release proof; generic update/recovery proof
cannot release a service fence. Ownership of capability/platform integration
remains with those components, not this process adapter.

## Explicit actions and receipts

`SupervisorClient.service` exposes `inspect`, `stop`, `resume`, `adopt`, `receipt`,
`proof`, and `reconcile`. Stop and resume commands are durable and return promptly;
subscribe to `serviceReceipt` notifications for progress. No action waits for a
scheduled poll. Keep the same command ID to retrieve a lost response, rather than
issuing a new command that might repeat work.

Stop requires the exact installation, owner, scope, instance and release digest.
The owner verifies retained release bytes, obtains and independently inspects the
host's held service fence, rechecks the owned instance, then sends the stop over
its retained authenticated child channel. Only that child's actual exit event
creates a stopped receipt. A vanished endpoint, saved PID or signal-delivery
acknowledgement does not prove exit.

Resume requires the stopped command ID and its durable exit proof. It verifies
retained bytes without requiring the old release to be latest, records one-use
resume authority before launching, then verifies the new instance and exact
release. It refuses an observed existing service. A resumed `ready` receipt is
distinct from `admissionSettlement: pending|settled|unknown`. Further effects are
blocked until the host independently confirms the fence is released. Update and
service owners mutually block competing mutations.

The private ledger survives a stopped supervisor reopening. Reopening alone
does not start anything. A live child after supervisor loss is unowned and cannot
be adopted; an uncertain admission or lost process ownership requires operator
investigation. Reconciliation can collect an exit already observed by the retained
child handle, verify an existing resumed instance, and finish a recorded host
release. It never sends another stop or launches another process.

## Foreground shutdown

Register `attachSupervisorSignalHandlers` before initial launch. SIGINT/SIGTERM
request the same qualified stop as an explicit client action. Startup, missing
coverage, busy work, and unknown outcomes are refused without forcing a process
exit. Repeated signals during a request do not send additional stops. Once the
child exit is confirmed, the supervisor closes its control plane and the caller
can exit. There is no managed systemd/launchd registration or crash policy here.

`runSupervisor().close()` refuses to close a configured service owner while it
still reports running or unknown. Use `stopService(commandId)` first. The low-level
lifecycle `close()` method is only isolated test cleanup; it is not the production
service stop API.

## Qualification boundary

The installed fixture uses separately packed owner and real host packages, an
actual signed application, persistent host state, and a durable fixture
participant. It exercises busy refusal, child exit, reopening a stopped
supervisor, explicit resume, exact runtime identity, participant reconciliation,
and pushed progress. It does not establish whole-product participant coverage,
managed service registration, production deployment, or native agent acceptance.

### Startup authority preservation

An existing Service ledger must retain its `owner`, `binding` and `commands`
tables and the original mandatory binding singleton. Startup validates bounded
metadata and binding read-only before opening writable, then revalidates under
the existing transaction. Missing or unsupported authority refuses initialization;
it never creates an empty command history or substitutes the caller's binding.
Compatible unmarked Service ledgers receive the atomic current profile marker.

Manual ingress validates its own `held`/`releases` profile, keeping an unknown
hold closed and preventing reuse of released fence identities. The exclusive
`lock.sqlite3` is synchronization, separate from these authority tables. An
existing authenticated ingress namespace with a missing intake database is an
unconfirmed interrupted bootstrap, including when no sidecars remain. It refuses
instead of inventing a completed startup. The original authority/key remain intact.
A completely absent namespace and all files cannot reveal historical loss without
external evidence; this limitation is explicit. These guards add no process
adoption, stop/resume, repair, or command replay behavior.
