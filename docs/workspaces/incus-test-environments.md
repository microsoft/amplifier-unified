# Qualified Incus test environments

The Incus adapter implements the same owned environment protocol as the Docker
adapter. It is an explicit operator-managed Linux option, not a consumer
prerequisite or an automatically installed fallback. It uses the pinned Smart
Tool core only to export the immutable source snapshot. The legacy Incus bundle
owns admission and instance lifecycle; it is not the Docker Smart Tool backend.

## Qualification and configuration

The host selects `backend: "incus"` in its private test-environment configuration.
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

Version `0.4.0` introduced capacity admission in bundle
[PR #37](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/pull/37).
Version `0.4.1` adds an exact offline image check before launch admission in
[PR #38](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/pull/38).
`package_digest()` in the adapter calculates it from sorted package-relative
Python paths and their content hashes. The app verifies the exact version/pin pair and
the installed bytes on every helper invocation. The launcher must use an explicit
Python interpreter; normal virtual-environment interpreter symlinks are accepted.
Operators retain and protect that exact installation, its dependencies, client
configuration and admission directory while any owned resource remains.
Validation does not create instances or alter the daemon.

Updating Unified preserves the normalized configuration and ownership identity of
an existing qualified `0.4.0` installation. Stop and remove its owned environments
before changing that installation or switching the operator configuration to a
new backend generation. An app upgrade does not authorize substituting a new
backend configuration for an existing owner's recorded configuration.

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
Because a stopped Incus instance cannot run a source/artifact export command, the
first adapter retains an opaque **complete instance export**, including
`/workspace` and `/artifacts`, without extracting it on the host.

The export has an 8 GiB limit, a 300-second deadline, and a 9 GiB private free-space
preflight. A watchdog terminates the owned export client if the spool crosses the
limit. It preserves the partial export and stopped environment on failure. After
export succeeds, the adapter hashes and synchronizes the archive and manifest
before owner-checked deletion. Failure to preserve that evidence blocks deletion.
The complete image adds retention cost: the qualification fixture exported about
629 MiB even for a tiny repository. This is a conservative first implementation;
selecting larger base images does not make the export budget unlimited.

## Bounded evidence

Qualification on an owned Linux Incus project covered exact snapshot preparation,
offline launch without inherited profiles, readiness, a real command with 200 KB
of output and exit code 7, stop freeing active capacity while retaining one
instance, complete export before removal, and absent-instance observation.
Inspection of the retained archive confirmed source and generated artifact bytes.
All qualification instances and the owned project were removed. These results do
not qualify other operating systems, arbitrary base images, remote previews,
network-dependent installs, or the surrounding product's browser experience.
