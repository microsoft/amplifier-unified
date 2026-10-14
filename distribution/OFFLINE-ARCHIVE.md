# Offline installation archives

The installed `amplifier-unified-archive` command captures declared installation
state into a private file and restores it into a new, inactive directory. It
never stops a service, adopts a process, starts an agent, or replays work.

This is an operator CLI. Browser requests cannot supply storage paths or adapter
modules. Inventory comes from the trusted distribution composition API and is
reviewed by its exact digest. Native data stays in the native owner's immutable
artifact, verified through its public inspection and bounded-read protocol.

## Capture boundary

1. Produce and review the storage inventory and any native artifact while their
   owners are available. Native external-writer exclusions require independent
   operator review of the exact attestation; they are not operating-system proof.
2. Explicitly stop the installed application using the service owner. The stop
   receipt must bind the installation, release, instance and all declared owner
   participants. Older receipts without the owner census can still resume, but
   cannot qualify this archive; obtain a new qualified stop.
3. Close the supervisor. Capture holds exclusive writer transactions on both
   retained supervisor ledgers for its entire duration and exports coherent
   standalone SQLite copies. It never copies their raw WAL or SHM files.
4. Supply a private create-request file and invoke
   `amplifier-unified-archive create --request /absolute/private/request.json`.

The create request has `directory`, `inventoryFile`, `inventoryDigest`,
`stoppedCommandId`, `expected` (the exact saved service identity), `outputFile`,
and `privateContentReviewed:true`. For qualified installed coverage, also supply
`compositionInventoryFile` (the base composition inventory) and the reviewed
`captureRequirements` from `createInstalledStorageInventory`. The command
regenerates this augmentation while the supervisor writer locks are held,
including bounded signed-release checks, and rejects any changed digest or
capture requirements. A declared-only capture without that fresh evidence is
explicitly partial. Raw installer configuration may contain
credentials, so capturing it also requires `includeCredentials:true` and
`credentialsReviewed:true`. Content is **not automatically redacted**. Archives
are private files, not encrypted transport packages.

For each native descriptor supply a local `nativeSources` entry with `id`,
`artifactPath` and `inspectionFile` produced by the trusted native adapter.
`nativeWriterReviews` holds `artifactId`, `attestationDigest` (SHA-256 of canonical
sorted-key JSON for `inspection.authority.externalWriters`) and
`operatorAssumptionAccepted:true`. The supplied attestation must exactly match
the inventory and native inspection. Native tar remains opaque to distribution.

The prebuilt-artifact CLI reports `completeCoverage` separately from
`completeProduct`. It never qualifies a complete product capture: an immutable
native artifact may predate the stopped application and frozen ledgers, and a
native capture attestation does not prove uninterrupted writer exclusion between
the two snapshots. `requireCompleteProduct:true` therefore refuses this path
even if all declared data is covered. Otherwise the receipt and manifest report
`captureConsistency.status: unqualified`. Missing owner authority,
installer provenance, credentials or native artifacts cannot be upgraded to
complete coverage by changing a serialized flag. Derived/reproducible sources
can be omitted only through the declared inventory. This archive does not infer
or scan arbitrary external trees. External native roots use the native artifact;
other external roots must be declared omitted in this initial implementation.

## Coherent capture through trusted local adapters

`createCoherentInstallationArchive` is the separate trusted local coordination
API. It is not an option to trust an existing tar or a browser-supplied flag.
The launcher reviews a composition inventory with all exact native root paths
declared but no prebuilt native artifact. The coordinator then:

1. Authenticates the qualified stop, exact service identity and owner census;
   freezes both supervisor ledgers until the entire archive is sealed.
2. Acquires the trusted launcher's writer-retirement boundary, retaining its
   exact review digest. Noncooperating writers and external configuration editors
   remain an explicit operator-reviewed retirement assumption, not OS proof.
3. Opens only private native maintenance/control connections and acquires every
   native capture gate **before full native prepare**, when source copying
   actually begins. No Core worker, session or inference starts.
4. Keeps each native admin and native-home gate through prepare, snapshot,
   inspection and bounded artifact reads. The live native owner binds the new
   artifact and exact SQLite manifest digest to its connection, nonce and gate.
5. Regenerates the installed inventory under the held boundary. A process-local
   capability binds final inventory, stop receipt, owners and native artifacts;
   serialized requests cannot mint, reuse or restore it. Liveness is checked
   during capture and on both sides of final file/directory sync.
6. Seals the archive, records its exact hashes, releases native gates, then the
   retirement boundary, then supervisor locks. Only acknowledged releases finish
   the returned success receipt.

Supply trusted `nativeCaptures` adapters (for example
`createNativeCoherentCaptureAdapter` with a launcher-owned private transport) and
`acquireWriterExclusion`. The latter returns `assertHeld`, `release` and exact
review evidence; it must own the continuing retirement of all noncooperating
writers for that installation. The API does not accept a JSON replacement for
these adapters. Native adapter operations are `maintenance.capture.acquire`,
`inspect` and `release`, distinct from a snapshot's shorter maintenance lease.

Only complete declared coverage plus this held interval yields
`completeProduct:true` and `captureConsistency.status: qualified-at-capture`.
That historical evidence never means locks are still held when inspecting or
restoring. Missing roots/credentials, unknown authority, a lost/replaced gate,
or unbound old artifact cannot qualify. The private `<archive>.capture.json`
receipt is allocated exclusively before effects and records release as pending,
released or unknown. A sealed archive survives unknown cleanup; inspect that
exact archive and receipt without replaying capture. A torn receipt is unknown.

The existing CLI remains coverage-only. Older partial archives remain readable;
older coverage-only `completeProduct:true` assertions are rejected because they
have no common capture evidence. Automatic rebinding or service activation is
never implied by either capture mode.

## Review and inactive restore

`amplifier-unified-archive review --archive /absolute/archive.unified --output /absolute/private/review.json`
writes the full private manifest and exact archive/manifest digests. It validates
member names, declared roots, byte counts and hashes before reporting coverage.

`amplifier-unified-archive restore --request /absolute/private/restore.json`
accepts `archiveFile`, an absent `destination`, the reviewed `archiveSha256` and
`manifestDigest`, and `privateContentReviewed:true`. It verifies the whole archive
before allocating the destination. Existing destinations are never overwritten.
Captured roots are restored below `roots/<root-id>`, native artifacts below
`native/<artifact-id>`, with a manifest and inactive restore receipt. A failed
extraction retains an incomplete marker and never deletes unrelated files.

Restore requires explicit configuration rebinding and later activation. Original
paths in retained configuration and receipts remain evidence; they are never used
as extraction destinations. Unknown receipts remain unknown, with no automatic
adoption, reconciliation, launch or repeat of previous commands.

Initial platform coverage is macOS and Linux with Node 22.16 or newer. Windows,
live capture, arbitrary external file roots, native archive extraction, automatic
rebinding and browser archive controls are outside this command's contract.
