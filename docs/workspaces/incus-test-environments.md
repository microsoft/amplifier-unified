# Qualified Incus test environments

The Incus adapter implements the same owned environment protocol as the Docker
adapter. It is an explicit operator-managed Linux option, not a consumer
prerequisite or an automatically installed fallback. It uses the pinned Smart
Tool core only to export the immutable source snapshot. The legacy Incus bundle
owns admission and instance lifecycle; it is not the Docker Smart Tool backend.

## Qualification and configuration

The host selects `backend: "incus"` in its private test-environment configuration.
The top-level optional `retentionMode` is `files` by default or explicitly `full`.
Its `incus` object contains:

| Field | Requirement |
| --- | --- |
| `executable` | Absolute regular executable for the installed `amplifier-digital-twin` CLI. |
| `executableSha256` | Exact SHA-256 of that installation's launcher, including its explicit interpreter path. |
| `packageSha256` | Reviewed package source digest below. |
| `incusExecutable` | Absolute regular executable named `incus`. |
| `configDir` | Private, owned client configuration directory. |
| `admissionDir` | One private, owned admission directory shared by every cooperating task on this host/user/daemon/project. Do not allocate it separately per task. |
| `project` | Explicit actual Incus project. |
| `image` | Exact 64-character fingerprint of an already cached, approved Linux image containing Python 3 with safe tar extraction support. |
| `daemonFingerprint` | Optional exact server certificate fingerprint; when supplied, every action verifies it. |
| `maxRunning`, `maxInstances`, `minFreeBytes` | Optional nonnegative operator limits. An omitted value preserves the operator environment policy, then the legacy default. |

The app accepts these exact reviewed version and Python package digest pairs:

| Version | Package digest |
| --- | --- |
| `0.4.0` | `5875826fc5f7dcb3eb70709b20043cb5b7ee6632bbb7c471246013e42cbe7f39` |
| `0.4.1` | `b70849c08aff3b8103e078ecf99d25180ac2afe0330ee7d44f72f88e7538df06` |
| `0.4.2` | `0911d18ec7256913253b53ce66520fac2f6cc78d19696a3f7b724a646dd909b8` |
| `0.4.3` | `2019e9b6ed0d2a1e882f90d8767f3652a278e9a5df0902efcf2a4a3b869a09ff` |

Version `0.4.0` introduced capacity admission in bundle
[PR #37](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/pull/37).
Version `0.4.1` adds an exact offline image check before launch admission in
[PR #38](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/pull/38).
Version `0.4.2` adds bound asynchronous local creation operations in
[PR #39](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/pull/39).
New runner setup installs this current qualified generation. Checking an existing
runner preserves its exact installation; it does not upgrade or redirect owners.
`package_digest()` in the adapter calculates it from sorted package-relative
Python paths and their content hashes. The app verifies the exact version/pin pair and
the installed bytes on every helper invocation. The launcher must use an explicit
Python interpreter; normal virtual-environment interpreter symlinks are accepted.
Operators retain and protect that exact installation, its dependencies, client
configuration and admission directory while any owned resource remains.
Validation does not create instances or alter the daemon.

Each new environment retains a private immutable snapshot of its qualified
backend configuration. Stop, remove and explicit reconciliation resolve that
recorded configuration, including after the operator selects another backend.
The old exact installation, dependencies and connection must remain available;
a snapshot does not reinstall them or accept changed executable bytes. New
execution continues to require the current configuration. Existing rows without
a snapshot can use only their identical qualified active configuration. An app
upgrade never substitutes a different backend for an existing owner.

An auto-updating cached image can disappear when Incus refreshes its cache. Use
an explicitly approved, task-owned local image copy with `cached: false` and
`auto_update: false`, and pin its exact fingerprint. Do not change shared cache
policy. A missing or unobservable approved image fails before a new launch; it
does not consume an active slot or trigger capacity retries. The adapter supplies
this preflight for older qualified backends. Existing uncertain launch receipts
remain uncertain, and inspection, stop and removal do not depend on the base image
remaining available. Selecting a replacement image requires explicit operator
configuration and a new request; the adapter never substitutes one automatically.

The legacy defaults separate 15 active/starting instances, 256 total retained
instances, and a 10 GiB storage-pool free-space floor. An explicit existing
`AMPLIFIER_DTU_MAX_INSTANCES=15` still means **15 total**; it does not become an
active-only policy during migration. Change that policy deliberately if retained
instances should not consume an execution limit. Stopped work still consumes disk.
The admission lock coordinates cooperating clients with the same host, user,
private admission directory and actual daemon/project. It makes no global promise
for older clients, direct daemon callers, other hosts, or other users.

## Isolation and recovery

Every launch uses one stable owner-derived name and request identity. The fixed
profile is offline, unprivileged, nesting-disabled and resource-limited. It uses
no inherited Incus profiles, network devices, host source mounts or provider
credentials. The adapter verifies expanded devices/configuration before source
installation or command execution. A cached base image must be small enough for
the retention budget; it is not modified to install missing tools or dependencies.
The instance filesystem is writable, unlike the Docker profile's read-only image.
There is no per-instance disk quota or preview endpoint in this slice.

Source extraction is attempted once. A durable readiness receipt binds the
successful extraction to the exact snapshot and environment. A partial copy or
lost response remains uncertain; inspection never repeats extraction. Command
output is bounded to 8 KiB per stream. Overflow sets `truncated: true` while
preserving a known exit code. A timeout kills the owned client process group and
returns uncertainty; it does not assert that a daemon-side command finished.
The caller must reconcile and stop that owned environment, never replay the check.

`capacity` returns structured active, stopped-retained, pending, total, storage,
limits and blocker information. It is advisory: launch serializes and rechecks.
`admissionDenied: true` is returned only for a proven pre-launch capacity refusal.
An uncertain launch remains associated with its stable request and environment;
retrying under a new name would be incorrect.

## Stop and removal

Stop releases active capacity and retains the instance filesystem. It does not
require export space. Removal first requires an observed stopped, owned instance.
The default `files` retention mode streams an uncompressed stopped-instance
export through a bounded tar filter and saves only `/workspace` and `/artifacts`.
No guest command is started, and guest paths are never extracted onto the host.
An explicit `full` retention mode preserves the complete opaque instance export.
An Incus removal request may deliberately select `retentionMode: "full"` for that
operation, including after a selected-file export failed. The existing workspace
removal confirmation offers this choice under “Save more files”. It does not
replace the original backend configuration or silently enlarge every export.
Docker rejects full-environment retention before effects.
Existing complete exports remain unchanged.

Archive-only retention follows the shared [inert metadata policy](test-environments.md#finish-incomplete-cleanup):
zero-length FIFO entries are retained without opening, creating or extracting
them. Nonzero FIFOs and other unsupported special entries remain rejected.
Ordinary `files` and artifact-only validation still reject FIFOs; opaque `full`
retention is unchanged.

Incus may still create a temporary complete backup during export. Smaller retained
archives reduce storage after cleanup; they do not remove that temporary space
requirement. Export has a bounded size, deadline and free-space preflight. Unsafe
archive paths, links that cannot be safely retained, missing selected roots,
truncation or persistence failures keep the stopped environment and available
partial evidence. Cleanup never reports preserved files when export failed.

A durable manifest records selected paths, hashes and byte counts before
owner-checked deletion. The host stores that bounded accounting with the
resource; ordinary panel refreshes do not traverse retained file trees. The
reported total covers the source snapshot and latest verified archives, not earlier
partial copies, filesystem allocation or daemon image/cache usage. Incomplete
copies remain available for deliberate recovery and are not automatically deleted. Saved check results and manifests remain
separate small records. Missing older accounting is unknown, not zero. Retained
files have no automatic age-based expiry; deliberate cleanup must preserve any
unique or unresolved evidence.

## Explicit recovery

`workspace.environments.reconcile` checks the same owned resource with an observed
revision and stable request identity. UI and agent callers use the same action.
It does not start, rerun, stop or remove an instance. It verifies the retained
backend configuration and actual daemon/project identity, owner, launch request
and instance identity. Confirmed running or stopped state can repair the current
resource projection. Ownership is retired only after physical absence is proven
and no unresolved admission or launch can still create that instance.
Inventory reads names first and fetches metadata only for the owned target,
so other instances' configuration cannot exhaust the bounded control response.

An unreachable daemon, changed daemon identity, missing installation, pending
launch or insufficient legacy identity evidence remains unverified. Empty
inventory by itself is not proof that a timed-out launch will never complete.
For the qualified `0.4.2` and `0.4.3` generations, an exact failed launch receipt plus observed
absence can settle a definitively rejected or failed creation. A remaining owned
instance still requires normal stop/retention/removal; failure does not grant
authority over a different instance. Unknown receipts remain unresolved.

The new bundle records operation IDs for offline exact-image launches on a local
Unix-socket daemon. It observes those operations within bounded time and count
budgets, without repeating creation or provisioning. Incus can expire completed
operations within seconds; IDs are not a permanent server completion archive.
Legacy unknown receipts, lost submission responses and expired operation records
are not retrospectively fixed by selecting the newer package. Ordinary inspection
cannot infer terminal failure from absence or elapsed time.

Recovery has its own durable receipt and leaves original uncertain operation
receipts unchanged. Repeating the same recovery request returns that receipt;
a later observation uses a new request and current resource revision. The
workspace panel exposes “Check and recover” and cleanup even when new execution
is unavailable, provided the original configuration remains verifiable.

## Bounded evidence

Qualification on an owned Linux Incus project covered exact snapshot preparation,
offline launch without inherited profiles, readiness, a real command with 200 KB
of output and exit code 7, stop freeing active capacity while retaining one
instance, complete export before removal, and absent-instance observation.
Inspection of the retained archive confirmed source and generated artifact bytes.
All qualification instances and the owned project were removed. These results do
not qualify other operating systems, arbitrary base images, remote previews,
network-dependent installs, or the surrounding product's browser experience.


Version `0.4.3` also recognizes current Incus single-address `Addrs` metadata
when selecting the tracked local transport. Setup selects that qualified
generation. Existing `0.4.2` owners and immutable configurations retain their
original exact package identity and remain eligible for observation and cleanup;
an update does not rewrite or redirect them.
