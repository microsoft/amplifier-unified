# Offline storage inventory v1

`createStorageInventory` and `validateStorageInventory` describe the exact storage
census for a reviewed offline archive. They do not scan, open history, start an
agent, freeze a writer, authenticate an attestation or authorize a backup. This
private object includes paths and account identity; it is never a browser topic.
The archive owner independently validates filesystem identity, byte hashes,
stopped application proof and closed supervisor ledgers while it captures data.

The schema is `amplifier-unified-storage-inventory`, version1. It has namespace,
account, applicationStateDirectory, owners, roots, nativeArtifacts, omissions,
completeEligible and digest. Digest is SHA256 of recursively key-sorted compact
JSON excluding digest. Arrays retain their declared order. The object is capped
at1MiB, with at most256owners/roots/omissions and32native artifacts.

Each owner records id, schemaVersion1, revision, participantId, rootIds and
externalStorage (`none`, `declared`, `unresolved`). Participant IDs must match the
actual configured host fence owners, never a guessed display name. Roots record
id, reciprocal ownerIds, normalized absolute path, coverage, capture and optional
reason. Coverage is `authoritative`, `derived-rebuildable`, `reproducible-code`,
`external-excluded` or `credential-excluded`. Capture is `tree`, `file`,
`native-artifact` or `omit`; omissions require a reason. The complete application
state directory is captured once as an authoritative tree. Overlapping directly
captured roots are refused. Canonical path and symlink validation happens at
capture time under the archive owner, not in this pure declaration validator.

Native artifact descriptors contain id, engineId, artifactId, sha256,
manifestDigest, declaredRootIds, completeNativeAuthority,
externalWritersExcluded and structured externalWriterEvidence. manifestDigest
identifies **exact native-manifest.sqlite3 bytes**, not its JSON description.
The archive reader must match every field to the sealed native attestation and
hash actual chunks/archive bytes. Native inventory completeness does not prove
outside writers are stopped. The native attestation records cooperative gates
and operator-attested-stopped policy; the archive operation requires separately
authenticated explicit operator verification for noncooperating writers and
retains that condition in its receipt. A supplied boolean alone is insufficient.

Unresolved owners, blocking omissions, omitted authoritative roots, uncovered
native roots or incomplete/unverified native artifacts make completeEligible
false. The validator recomputes this property; callers cannot set it to true.
The property means declaration consistency only. A complete product backup still
requires the archive owner's actual captured-byte and exclusion qualification.
Unknown receipts and pending work are preserved on restore. Destination identity
is absent/inactive; rebinding and activation are separate explicit operations.
