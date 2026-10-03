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

`requireCompleteProduct:true` refuses partial coverage. Otherwise the receipt
and manifest explicitly report incomplete scope. Missing owner authority,
installer provenance, credentials or native artifacts cannot be upgraded to
complete coverage by changing a serialized flag. Derived/reproducible sources
can be omitted only through the declared inventory. This archive does not infer
or scan arbitrary external trees. External native roots use the native artifact;
other external roots must be declared omitted in this initial implementation.

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
