# Legacy process interruption recovery

This is an explicit maintenance path for an older installation that retained an
unknown update admission but cannot prove graceful retirement. Ordinary update,
service-stop and admission-abort protocols remain unchanged. There is no automatic
fallback, public recovery command, or live-installation enablement in this change.

## Truth and authority

The original update stays `unknown` / `admission_requested`, with its original
signed target, error, admission debt and timestamps unchanged. A separate sealed
`legacy-process-maintenance-v1` receipt records interruption of the old local
processes and readiness of a newly selected signed replacement. It does not claim
that the original update succeeded, that Native retirement completed, that remote
work stopped, or that uncertain business operations had no effects.

`binding.prepared` is the independently qualified maintenance replacement. It can
and normally will differ from the target of the failed original update. The old
operation's digest includes that original target and cannot be rewritten.

Trusted private composition supplies the installation/owner/scope identities,
exact original command and fence, ordered complete owner census and storage
bindings, configuration, retained-history and unknown-outcome digests. These are
read from authoritative stores. A client-provided receipt, file existence, PID,
health endpoint or absence of an active row cannot supply this authority.

## Sequence

1. Qualify the replacement's exact signed bytes and retained-state compatibility.
   Inspect the original unknown operation, fence and complete owner bindings.
2. An explicitly authorized external maintenance controller excludes every
   declared launcher and writer, freezes the exact old service, and captures the
   original unit identity and process census. It runs outside the old cgroup.
3. Bind kernel pidfds for every member of the recursive cgroup subtree. Verify
   boot ID, invocation ID, process start ticks and required writer identities;
   reject visible descendants outside the subtree. For a transient service that
   needs scoped inhibition, use the explicit profile described below.
4. Persist a one-use interruption intent, then interrupt the whole old service.
   Confirm every retained pidfd has exited, the recursive cgroup is empty and
   external exclusion remains held. Main-process exit alone is insufficient.
5. Preserve the old operation and all business histories. Persist a separate
   `restart_requested` receipt before invoking the retained launcher once.
6. `createLegacyRetainedLauncher` consumes an exclusive fsynced launch claim and
   uses the replacement supervisor's real `PosixOwnedProcessLifecycle`. It never
   uses initial provisioning or adopts an existing process. The retained
   installation has a new process instance, not a new identity or data scope.
7. Qualify authenticated process-owned readiness and the replacement's actual
   writer locks with all original intake holds still closed. After a lost reply,
   `observeLegacyProcessRecovery` can finish this observation for that saved exact
   instance; it cannot interrupt or launch anything. Replacement gate qualification
   is repeated during passive observation, not inferred from a ready endpoint.
8. Explicitly compose `createLegacyMaintenanceReleaseVerifier` into the signed
   replacement. It returns the existing Host release proof shape, bound to the
   exact old command/fence/census and the new ready instance. This is not a fallback
   after the ordinary release verifier fails.
9. Reconcile the old owner holds using their existing durable release APIs.
   Partial release and lost final acknowledgement are retried by inspection of the
   original receipt. A newer/different hold refuses reconciliation. Once Host
   proves the whole exact fence released, the supervisor atomically records a
   separate settled maintenance disposition and the new installed release. It
   does not change the original update's unknown result or replay work.

## Explicit systemd inhibition profile

The default custody adapter still requires the original loaded, transient/disabled,
Type=simple, Restart=no, nondelegated service policy. It performs no systemd
mutations. An ordinary runtime mask may not override a transient unit: a successful
mask command alone is not inhibition evidence.

`LegacySystemdInhibitionProfile` is the shared guard/observer contract:

```ts
{
  original: LegacySystemdCustodyWitness;
  mask: { path: string; dev: string; ino: string; uid: number; target: '/dev/null' };
  assertHeld(): Promise<void>;
}
```

The trusted external guard captures `original` through the strict observer before
masking, verifies it against real pre-mask unit bytes/processes, creates its own
exact `/run/user/<uid>/systemd/user.control/<unit>` mask, and continuously holds
its declared launcher exclusion. The observer independently verifies mask
path/inode/device/owner/target, effective systemd masked state and original
invocation/process identities, then retains its own pidfds. Its receipt digest
binds both the original witness and mask. JSON without the live guard is not enough.

Reloading a masked transient unit resets some reported policy fields, including
Type and KillMode. Those defaults do not replace the original policy witness.
After exit, MainPID=0 or a cleared invocation is only an observation; acceptance
still requires all retained pidfd exits, recursive emptiness and effective
inhibition. Keep the old unit masked. Launch the retained successor under a
separate, explicitly owned maintenance service while preserving installation
identity. Unmasking the old unit is not part of launch or settlement.

## Crash boundaries and exclusions

The private authority directory permanently prevents a second recovery controller
from creating a new effect permit at that location. The controller SQLite lock
only serializes controllers; it is released by the kernel on death. It is not a
product writer lock. The permanent retained-launch claim prevents another launch
even after an uncertain write, spawn or startup reply. A stopped or uncertain
receipt does not authorize deleting a claim and trying again.

A lost controller before launch completion leaves the phase truthful. Only a saved
`restart_requested` or `ready` receipt can enter passive readiness reconciliation.
Failure before the launch claim is consumed is not automatically reclassified as
no-effects. An absent replacement stays unknown; recovery must not manufacture a
new ID or launch another process.

## Qualification limits

Local tests cover real supervisor journals and durable Node intake journals with
22 representative names; they do **not** instantiate all 22 business owners.
The retained-launch test owns a real Node child and deliberately forbids initial
provisioning. Linux tests exercise actual transient systemd units, frozen cgroups,
kernel pidfds, the bound mask profile and denial of subsequent start/restart.
All are isolated fixtures, with no installed application service or user history
changed.

Live use additionally requires independent qualification of the concrete signed
composition, complete writer/launcher coverage, pre-existing lock protocols,
continuous exclusion through sequential gate handoff, compatible retained stores,
all real owner holds, and actual replacement readiness. Existing storage-inventory
files are capture catalogs, not writer-exclusion proof. Previously detached or
unrecorded writers cannot be discovered by this observer. Refuse an installation
whose coverage cannot be established. The product does not claim atomic transfer
of many independent SQLite/flock gates.

The external guard and installed-composition adapters are separately reviewed.
This source does not install a maintenance CLI, force-stop a user's service,
rewrite their ledgers, claim a pristine installation, or silently enable recovery
from the normal update screen. A future user-facing action must explain the
interruption and preserve the same authority and outcome boundaries.
