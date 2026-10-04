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

The trusted composed application exposes `await app.storageInventory(options)`.
It derives owners from its actual quiescence participant census and revisions
from its installed public component manifest. `externalRoots`, `externalCoverage`
and native artifact attestations are explicit operator/adapter inputs. Known
external transfer staging/exchange paths and every configured engine remain
blocking omissions until represented. Unrecognized/custom owners remain
unresolved. This call is explicit and reads only the small component manifest;
it never parses arbitrary engine launchers, enumerates history or reads peer
private stores. Capture still checks its participant census against the exact
qualified stopped receipt. Clients cannot call this private-path API over AHP.

## Full-owner startup declarations and receipt

The launcher passes the reviewed composition's `storageInventory` object directly
to `app.storageInventory`. The
[full-owner declaration example](../examples/full-owner-storage-inventory.json)
covers ingress and three separately configured data directories. It is an
options example, not a runnable composition or a native capture plan. Adapt its
absolute paths and participant IDs to the actual reviewed composition:

| Root | Exact configured directory | Participant attribution |
| --- | --- | --- |
| ingress | `application.manualIngress.stateDirectory` | Actual ingress participant |
| portability-stage | `application.portability.stageDir` | Actual portability participant |
| portability-exchange | `application.portability.exchangeDir` | Actual portability participant |
| managed-session-files | `application.host.managedSessionRoot` | Actual managed-files participant |

The application state tree is already declared by the producer. Do not add a
duplicate nested capture when any directory is inside that tree. These
declarations do not expand `allowedWorkspaceRoots`, capture the entire workspace,
or grant permission to read unrelated stores. An owner's `externalStorage` may
be `declared` only when all its configured external authority is accounted for.
The producer keeps omitted or mismatched configured paths as blocking omissions.

Managed allocation custody belongs to the **host**, not solely to the
managed-files forwarding facade used for participant attribution. The root
contains the allocation ledger, markers and files. Capturing it requires the
qualified full host and configured-owner stop and consistent database capture;
holding only the facade is insufficient. Declaration is not writer exclusion.

The `full-owner-ready-v1.storageComplete` field retains its existing name for
compatibility, but projects **`inventory.completeEligible === true`**. There is
no `inventory.complete` field. The ready receipt reports declaration eligibility;
it does not certify captured bytes, stopped writers, a sealed archive, or restore
support. Native owners remain unresolved and `nativeArtifacts` remains empty at
ordinary startup. A reviewed native capture plan also remains incomplete until
its exact artifact roots are covered by a genuine gated and sealed capture.

Fixing these declarations and this receipt does **not** add full-owner installed
backup/restore support. The [saved installer inventory](INSTALLED-STORAGE-INVENTORY.md)
and installed archive creation currently use the legacy installed-service reader.
The full-owner saved layout requires its own reviewed adapter before it can claim
complete installed capture. Native canonical history, consumed claims, failed
attempts and uncertain receipts must be preserved; uncertain work is never
replayed. Inactive archive extraction is not qualified runnable restoration.
