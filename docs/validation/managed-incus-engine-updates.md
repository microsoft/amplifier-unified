# Managed Incus engine update qualification

This is a backend extension of the global managed-runner updater. It does not
change production configuration, configure an Incus daemon, or establish
acceptance on every registered machine.

## Native observed acceptance

The isolated native Linux run completed on source checkpoint
`a4310ccd907d86a9fdb8d79a71cea674df5151b0`:

- Updated the deterministic core to Git revision `5235833098d9a630791452a46558d92f58af0389`.
- Installed and qualified the separate Incus engine at
  `ef4eb203c5959fca8272190dee2412ba7d2fbf43`, with package source digest
  `2019e9b6ed0d2a1e882f90d8767f3652a278e9a5df0902efcf2a4a3b869a09ff`.
- Copied a fixed Git snapshot containing committed and uncommitted input,
  executed its offline command, verified the retained sentinel artifact, and
  stopped/removed the owned guest.
- Composed the unchanged core's exact existing proof with the new engine's
  completed proof, then selected the new engine for future tests. This receipt
  identifies both evidence sources; it does not claim the core was rerun.
- Ran a new ordinary check through `task_environments.invoke` without an updater
  qualification bypass. Configuration validation, admission, transport, command,
  artifact export, and owned removal all passed.
- Independently observed all three exact guest names absent. The core and
  ordinary-check allocation rows were retired and their machine leases absent.
  Both declared source policies still track `main`.

The initial setup-policy receipt was an explicitly controlled private fixture.
The core installation, engine installation, runtime verification, target
controller, native guests, jobs, evidence, selection, and final ordinary check
were real. This is not evidence of automatic migration from an arbitrary legacy
installation.

Two failures were retained and corrected during acceptance. The initial source
export placed its archive inside the checkout and was refused before engine
installation. After correction, the engine qualification completed, but final
publication rejected the normal setup producer's `0644` lock. Real `FileLock`
interoperability now has a regression. The same engine intent resumed only its
settled publication using a newer fixed controller, preserving its original
controller and recovery lineage. Completed core and engine guest checks were
not repeated.

## Focused source evidence

The affected core/engine/controller/retained-configuration lane passed 209 checks
before the final narrow recovery corrections. The final verifier, engine
lifecycle, and origin lane passed 59 checks. Independent publication review
passed 49 checks, including live/unknown-effect refusal, changed intent and target
identity, lost origin receipts, lock ownership, and publication recovery without
repeating the guest. Earlier independent recovery probes covered lost removal
acknowledgements, exact absence settlement, failed artifacts, changed core proof,
and preserving possibly published generations after a lost selection reply.

The new focused suites are `test_managed_incus_target.py`,
`test_managed_incus_composition.py`, and `test_managed_incus_updates.py`. Existing
Incus worker, registered-controller, bytecode-verification, core-update and
retained-configuration suites cover their affected shared boundaries. Passive
platform tests exercise missing POSIX capabilities without importing native
controller modules. Full dependency fingerprinting occurs at execution and
selection; ordinary configuration reads use the exact retained receipts.

## Limits and retained storage

Actual acceptance used the local native Linux route. Registered-machine routing
and identity guards are implemented, but this run does not qualify a remote
registered-machine engine promotion. Native Windows automatic engine updates
remain unsupported; passive Updates inventory is covered. No native macOS,
Windows, WSL, or production adoption is claimed.

The selected engine generation occupied 3,820 KiB in this run. Known settled,
unpublished failed or skipped runtimes can be removed while preserving receipts
and artifacts. Prior selected generations, possible publication after a lost
acknowledgement, and unknown installer/guest outcomes remain retained. Finite
reference-aware retirement of those older engine generations is a follow-up;
the core updater's retirement policy must not be presented as covering them.
