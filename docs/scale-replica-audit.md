# Private scale replica audit

These `scripts/scale_replica_*.py` helpers support the explicitly authorized
October 15, 2026 reading audit. They are operational tools, not production
startup code or a migration. Private evidence and captured data stay outside Git.

## Capture and provenance

The capture process reads the source host without creating source-side archives.
SQLite databases use the online-backup API into memory; private client state and
explicit credential fields are removed, followed by VACUUM before serialization.
Regular files record before/after identities and streaming SHA-256. Each completed
compressed archive is retained read-only and extracted with per-file hash checks.
The snapshot is per-file consistent, **not globally atomic**. App histories can
continue changing on the source while later shards are captured.

Initial reading requires all database, primary transcript/metadata, app blob and
ancillary display-resource shards. Event history has 32 separate shards. Partial
event capture must never be described as complete Work-history fidelity. The
active clone receives later verified event shards; a performance baseline must
record its exact completed-shard set and any concurrent copy activity.

Primary recovery `.backup` and `.append-pending` files are included because the
canonical reader uses them. Arbitrary backups, source code, dependency caches,
provider keys, auth/cookies, TLS files, browser drafts and production server
configuration are excluded. History prose and tool output remain private data;
field redaction is not a claim that arbitrary historical text is secret-free.
The separate settings inventory retains structure only, replaces every scalar
value, and is never imported as configuration. Exact model/configuration value
acceptance is consequently outside this reading tier.

## Runtime isolation

The replica lives beside the retained synthetic development instance inside the
owned project DTU. It uses a private RootDirectory and network namespace. Copied
data is mounted at the original canonical home paths, preserving native IDs.
Existing source/dependencies are exposed read-only. Workspace paths are empty
skeletons only when the source inventory confirms the directory existed; missing
source paths remain absent. Code, git, filesystem and runtime acceptance cannot
be inferred from these skeletons.

The service uses `env -i`, explicit Amplifier roots, a fresh authentication token,
private temporary directories and its own browser profile. It has no external
listener. Its private loopback port and Unix socket are for owned audit access.
The LXC global service drop-in can override PrivateNetwork; the service-specific
late drop-in and an actual denied-external/allowed-loopback probe are mandatory.
The launcher additionally checks that the current namespace exposes only `lo`.

Before construction/startup, the launcher disables schedules, collaboration
draining, observations, notifications, external tools and runtime execution.
Copied update candidates are cleared in the writable clone only. A narrow action
allowlist permits navigation/inspection while denying sends and other mutations.
Normal authentication and the real display/storage/query paths remain in use.
This profile cannot qualify execution, outbound integrations, update activation,
historical replay, or live-provider concurrency.

## Lifecycle

`scale_replica_prepare.py` prepares but does not start the named service. It refuses
an existing root marker/service and must not be reused as an overwrite/reset tool.
`scale_replica_transfer.py` resumes completed archived shards; interrupted partial
archives require explicit inspection rather than automatic deletion. Preserve the
failed extraction receipt if a retained archive is subsequently re-extracted.

Record resources, source revision, capture tier, private evidence, ownership and
cleanup commands under `readinessReplica20261015` in the workspace manifest.
Retain the raw compressed capture plus one writable clone, not two expanded
copies. Cleanup may stop/remove only this named replica and its dependency bind;
it must preserve the existing synthetic preview and shared DTU.
