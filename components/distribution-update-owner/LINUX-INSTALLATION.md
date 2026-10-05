# Installed Linux service lifecycle

The explicit `linux-user-unit` profile runs an installation in one owned systemd
user unit. Initial startup, update activation, rollback, normal stop and recovery
all use the existing `ServiceLifecycleOwner` and its `ServiceStore` commands.
The distribution update journal links those commands; it cannot issue a second
restart proof. Legacy configurations without this profile retain their inherited
Node child behavior described in `SERVICE-LIFECYCLE.md`.

```js
serviceLifecycle: {
  installationId,
  ownerId,
  platform: {kind: 'linux-user-unit', unit, unitDirectory}
}
```

The fresh full-owner installer selects this profile on Linux and records its
stable unit name under the installation's own supervisor directory. The explicit
provisioning authority must match the installation. Existing services or old
installation directories are never inferred or adopted.

The supervisor publishes authenticated discovery before starting the initial
app. The first start records its one-shot provisioning claim and actual systemd
InvocationID before waiting for application readiness. The signed app begins
with intake closed. Only an authenticated proof for that consumed claim, exact
signed release, instance and current unit generation can open intake. There is
no fabricated prior process or stop receipt.

An update checks and prepares signed bytes, asks the same service owner to stop,
then activates the new release only after complete process-tree exit. Host
admission closes to new work while accepted work drains. Ordinary stop does not
have a timer that escalates to killing work. Unit observation budgets return an
unconfirmed outcome and preserve custody; they do not authorize interruption.
Independent signed role entrypoints determine interpreters, not the supervisor's
interpreter or the app's former process ID.

Manual checks and installs enter the owner immediately; automatic polling is not
required to begin them. Source checks and readiness verification remain necessary.
Accepted callbacks can finish while admission is closed. A ready replacement is
not a settled update until Host releases the exact retained service fence.

Rollback uses verified retained release bytes and the same service commands. It
does not require a retired release to match today's forward source revisions.
Controller restart observes the original operation and current unit generation;
it does not reclaim initial authority, send another stop, or repeat a launch.
If a crash occurs before a resume was attempted, reconciliation retains the
stopped/unknown state instead of inventing that missing command.

Qualification uses an isolated Linux user unit, separately packed current owner
and Host components, test-signed releases, synthetic state and a real Host client
callback. It covers initial startup, A-to-B through the normal supervisor factory,
controller loss/reopen and preservation of uncertain history. This evidence alone
is not qualification of every full-app participant, browser/device interaction,
real provider account, existing-service migration or production deployment.
