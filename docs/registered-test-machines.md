# Registered test machines

Global Settings → Testing & resources → Other test machines connects an already approved SSH machine once. Waiting checks may then use an available compatible machine. Pausing a machine affects future admission; it does not stop work already owned by that machine.

## Supported setup

This first remote route requires an existing managed **Linux origin test setup** with the qualified source snapshot runtime. The origin's local container daemon need not have free capacity, but this is not yet a standalone macOS/Windows or Docker-free origin setup flow. Registered targets are native Linux Incus runners using an existing trusted SSH connection, configured qualified adapter, cached approved base image, and Python 3.13 or newer in that adapter. Registration installs only a small hash-qualified controller in private account storage. It never installs a daemon, changes BIOS/virtualization settings, enables SSH trust, or changes system services.

A machine's CPU, memory, free-space and dependency-build budgets are shared by cooperating native clients using the same OS account. Registered controllers also share mutable running/retained/disk admission limits by exact Incus daemon/project/account. Different accounts, older clients, unrelated container workloads, remote Docker daemons and Docker Desktop are not collectively governed by this authority. Current physical observations still include outside CPU/memory/disk pressure.

Automatic machine budgets and configured backend limits remain visible separately. Advanced overrides use revision checks; an empty field uses its default, zero disables that individual ceiling. Lowering a budget never kills active work. Saving limits wakes waiting checks on the current origin; other installations reconsider on their normal bounded retry.

## Identity and recovery

The ordinary job/allocation journal remains the canonical owner. It saves the selected controller, immutable package manifest, backend identity, source and resource/timing requests before source transfer or execution. Endpoint aliases reuse a durable per-account authority ID. That ID is distinct from the OS machine ID, because cloned machine images can have identical machine IDs.

The target executes the actual create/test/cleanup operations and persists its effect receipt before dispatch. Lost replies retain the original target. They never authorize moving the job elsewhere or repeating an unknown create/test. Exact terminal backend observations can settle an interrupted create. Cancellation reaches the original builder without waiting for its long operation lock. Incomplete dependency cleanup can continue through the existing owner-checking cleanup helper; completed cleanup remains an idempotent receipt.

A confirmed refusal before admission can return to the queue and choose another approved target. Read-only observations are shared within the scheduling pass, and unavailable targets use bounded backoff. A registration paused while its capacity observation is in flight is rechecked before binding.

Source and artifacts are retained before guest removal. Target exports have exact archive identities; downloads accept only private regular files under the owning allocation, refusing links and special files. Incomplete downloads leave the verified target export available. Controller refresh installs the current verified package for future work while existing owners retain their original package manifest and cleanup route.

## Qualification

Focused tests cover source/chunk retry, dispatch-before-effect, lost replies, returned unknown outcomes, terminal reconciliation, aliases, cloned IDs, cancellations, incomplete cleanup, archive containment, target pause races, settings conflicts and original-controller retention across refresh.

The existing registered Settings browser lane covers global setup, lost registration acknowledgement without replay, dirty drafts, target controls, zero overrides, narrow layout and cold reopen. It uses an isolated protocol fixture; it is not deployment evidence.

Live qualification used two approved native Linux machines and an identical tiny cached-image check containing an eight-second wait. Two serial full lifecycles took **67.746 seconds**; two concurrent lifecycles across the machines took **36.864 seconds**. Source preparation was 2.76–3.24 seconds per check, start 3.66–4.33, command 9.28–9.59, stop 2.18–2.35, and export/removal/download 6.65–17.54. These demonstrate bounded parallel progress for this workload, not general throughput or production load performance.

A separate real AppService/TestQueue run used a controlled capacity fixture for a saturated origin and first target. The second target performed actual source transfer, durable job execution, result delivery, stop, export/download and removal in **32.766 seconds**. The saturated target received only a read-only capacity request. No production policy was changed to simulate saturation.

The first development controller exposed the system interpreter's older archive API. Its stopped guest was preserved and recovered through explicit verified cleanup; the final controller enters the already-qualified adapter interpreter before processing requests. All seven development and qualification guests were confirmed absent after retaining source and results, with no remaining machine reservations for those owners.

## Remaining work

- The existing one-pending-check and one-owned-test-environment limits per chat remain. Allowing more queued checks requires an adjustable per-chat limit plus deliberate same-chat scheduling; this route increases capacity across chats and approved machines.
- Standalone remote-origin setup for Windows, macOS and Linux without a preconfigured local testing route, including portable existing receipt storage and independent snapshot runtime setup.
- Approved target provisioning for people without an existing SSH/Incus setup; target registration currently assumes that setup already exists.
- Shared authority across different OS users, older clients, Docker Desktop/VM hosts and additional remote backends.
- More representative workload/load benchmarks and any cost-aware or geographic routing policy. Current routing chooses the first compatible available approved target.
- Automatic removal of historical retained user data is not introduced. Sources, artifacts and recovery receipts remain preserved.
