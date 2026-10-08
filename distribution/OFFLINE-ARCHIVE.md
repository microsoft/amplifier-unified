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

When legacy private-client import is configured, the retained original database
must belong to the installation's account and be covered by an authoritative
tree, including its WAL, SHM and journal companions. The application state tree
can supply that coverage. A single database-file declaration, an omitted
companion, or a derived index cannot. This classification removes the legacy
client coverage omission only; it does not replace writer retirement or qualify
an atomic capture. The archive implementation still enforces owned paths and
the held capture boundary. Restored private-client grants remain bound to the
requesting browser; restoring the original does not send a draft.

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

## Prepare recovery of the same stopped installation

`amplifier-unified-archive prepare-recovery --request /absolute/private/recovery.json`
prepares a reviewed complete archive for the existing installation. Unlike the
inactive extraction command, this explicitly changes that installation's active
application paths. It still does not start the service or send conversation input.

The private request has schema `unified-installation-recovery-v1`, `directory`,
`commandId` (at most 100 characters), `archiveFile`, reviewed `archiveSha256` and
`manifestDigest`, `expected`, `stoppedCommandId`, `privateContentReviewed:true`,
`credentialsReviewed:true`, and `writerRetirementReviewDigest`. The last field is
the SHA-256 of the operator's reviewed retirement record for noncooperating native
writers and configuration editors. It is an operator assumption, not OS evidence.
Keep those writers stopped throughout preparation. `native` contains `engineId`,
the current trusted `configurationFile`, the archive's authorized `cwd`, and a
new native `destination` with a launcher-authorized `rootId` and `name`.

This path currently supports a single native engine in a `unified-installation-v1`
installation, with the original private administration runtime and native artifact
still available. The complete archive must bind the exact same saved service stop
and release. It refuses a resumed installation, newer stop, partial archive, other
installation, additional external application trees or uncertain service outcome.
New-machine recovery and the newer fresh-composition installer schema need separate
adapters. They must not be presented as supported by this command.

Preparation holds the supervisor's offline writer locks, extracts into a new
private recovery directory, asks native maintenance to restore and finalize its
own authority, and uses the catalog owner's offline interface to retain chat IDs
at the restored native home. Trusted launch configuration comes from the existing
installation; archived commands are never run. The application tree returns to its
original location so managed files, drafts and links retain their paths. The old
application tree and original configuration remain under `recovery-<commandId>`;
the original native homes are preserved. No supervisor ledger is rewound.

The receipt records each phase before publication. An interrupted publication
leaves `RECOVERY-PENDING.json`, which blocks both service reopening and application
composition. Inspect that receipt and both retained copies; never remove the marker
or repeat the operation merely because its acknowledgement was lost. An identical
completed request reads its previous receipt without repeating effects.

After a successful `prepared:true` receipt, use the existing service `serve` and
explicit `resume` commands with the returned original stop and expected identity.
The supervisor independently verifies the signed runtime and source currency.
No previous prompt, unknown receipt, schedule run or worker result is replayed by
recovery preparation. Normal scheduling and any later explicit user input remain
subject to their existing owner rules.

## Inspect and finish an interrupted publication

New recovery preparations retain a bounded publication proof before moving any
application tree. To inspect the original attempt, create a private JSON request
with its exact `directory` and `commandId`, then run
`amplifier-unified-archive inspect-recovery --request /absolute/private/inspect.json`.
Inspection holds the existing offline supervisor exclusion and verifies the
original and restored trees, native roots, launch configuration and saved stop.
It returns `reviewDigest`, `phase`, and the available decisions without starting
any service or agent.

After reviewing that result, add its exact `reviewDigest` and either
`decision:"complete"` or `decision:"rollback"` to a separate private request. Run
`amplifier-unified-archive reconcile-recovery --request /absolute/private/decision.json`.
Completing finishes publication of the already prepared restored data; rolling
back reinstates the retained original application and configuration. Both retain
the other copy and preserve uncertain receipts. Neither repeats native restore,
starts the service, or sends any prompt. Activation remains a separate explicit
service operation.

If reconciliation is itself interrupted, inspect again and use the new review
digest. Changed contents, a changed stop, ambiguous layout, missing proof or
exceeded proof bounds refuse the decision and preserve the fence. Proof work is
bounded across the whole inspection to 8 GiB, 200,000 entries and five minutes.
Older interrupted attempts without a publication proof cannot be inferred safe
by this command; preserve them for operator investigation.
