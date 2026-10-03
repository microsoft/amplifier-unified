# Existing-state launcher handoff

`launchExistingStateHandoff` transfers a **stopped, same-release** installation
from its original owned launcher to a fresh supervisor. It is an explicit trusted
operator entrypoint, separate from pristine installation and signed release
activation. It never discovers, adopts, kills, or resumes a process by PID.

## Required authority

The original launcher must have authentic durable stop evidence, a held host
fence covering every configured owner, and proof that its exact owned child
exited. `createOwnedServiceHandoffSource` is the first-party adapter for a
`ServiceLifecycleOwner` with a retained `PosixProcessOwner` child connection.
A different manual launcher needs its own authenticated implementation of
`ExistingStateHandoffSource` and qualification of these same obligations. Saved
PID files, missing endpoints, operator assertions, or copied JSON are not exit
proof or launch authority. If that evidence is unavailable, this contract cannot
perform the handoff.

The operator supplies the full expected owner census. It must match the actual
held census exactly. Original installation ID, owner ID, data scope and release
identity remain unchanged. Only the child instance ID changes. The destination
must have a fresh service ledger and no previously owned child. Use separate
source and destination supervisor/control namespaces, while retaining the same
application and native data paths.

```ts
const source = createOwnedServiceHandoffSource({
  service: originalSupervisor.service,
  bindings: existingPaths,
});

// The destination uses the same qualified release and actual existing-path
// launch configuration. Do not request startInitial or pristine provisioning.
const accepted = await launchExistingStateHandoff({
  destination: newSupervisor.service,
  source,
  bindings: existingPaths,
  command: {
    commandId: operatorCommandId,
    stoppedCommandId: authenticStop.commandId,
    expected: authenticStop.expected,
    target: qualifiedSameRelease.identity,
    participantIds: configuredOwnerIds,
  },
});
const completed = await newSupervisor.service.waitFor(accepted.commandId);
```

This API is deliberately absent from remote/browser RPC. The trusted operator
must first resolve and verify the actual launch configuration against the same
bindings; the owner cannot interpret an arbitrary application's config format.
No new application source or different owner census is activated through this
path. Use the separate qualified distribution update mechanism for those changes.

## Bindings and durable transitions

`ExistingStateBinding` names an existing canonical absolute file or directory.
The entrypoint checks filesystem identity without creating or copying data.
Directories bind path/device/inode, allowing ordinary content changes. Files are
bounded to 1 MiB and additionally bind their content hash through a no-follow file
descriptor. Use files for small launch configuration and directories for mutable
data. Symlinks, replaced roots and changed configuration refuse the handoff. Only
the combined digest is placed in public receipts, never the paths or contents.

1. Destination records an operation and the exact next child instance before
   asking the source to transfer authority.
2. Source verifies its saved stop, exact child exit and held-owner coverage. It
   durably records `handoffRetired` and consumes its resume right before replying.
   Its inspection then reports `retired`; service and update mutations remain
   blocked, including after reopening its ledger.
3. Destination verifies the returned claim and imports the authentic stop/exit/
   fence evidence. It consumes that stopped authority before any child launch.
4. A new child completes the real inherited IPC ownership handshake and exact
   same-release readiness check. The authenticated new host then reconciles its
   retained fence using the persisted stop and handoff receipts.
5. Success requires both the new ready receipt and settled admission. Ordinary
   stop, busy refusal and resume work normally for this new owned child.

Repeated identical commands observe the same receipt. A changed command binding
refuses. A lost claim, launch or release response remains uncertain; reopening or
reading a receipt never repeats the operation. Passive reconciliation can confirm
an already-started exact child and settle its fence, but never creates one. If
source retirement completed and its reply was lost, both sides preserve that
uncertainty for operator review instead of automatically replaying the handoff.

## Validation boundaries

Source tests exercise actual children, durable retirement, duplicate destinations,
lost replies, same-release enforcement, configuration/root binding, and ordinary
service lifecycle after transfer. The opt-in installed service test packs this
owner, independently installs it with the qualified host archive, starts a signed
real host, holds two durable fixture owners through exit, and checks handoff,
preserved history, busy refusal and subsequent stop/resume.

These fixtures do not qualify any production installation's full owner inventory,
a different manual launcher adapter, system service integration, or a live upgrade.
Manual update checks/install requests retain immediate event-driven dispatch;
this handoff adds no periodic waiting loop to those paths.
