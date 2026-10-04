# Saved installer authority inventory

`createInstalledStorageInventory()` in `src/installed-storage-inventory.js` is a
trusted maintenance reader. It augments the already composed storage inventory
with the saved installer's private authority. It does not start or stop a process,
connect to a running supervisor, open a SQLite database, fetch a release, resolve
Git refs, or capture a backup. It is not a browser capability.

**Supported layout:** the current reader handles `unified-installation-v1` with
`application.json`. It does not handle `unified-full-owner-installation-v1` with
`installer-composition.json`. The installed archive creation paths also use that
legacy reader. A full-owner startup inventory or corrected ready-receipt
eligibility field must not be treated as installed backup/restore coverage.

A separate full-owner adapter must bind the exact saved composition and source
provenance; classify all configured data and private authority; preserve consumed
claims, failed attempts, native canonical histories and uncertain receipts;
require real host/owner stop, supervisor exports and native capture exclusion;
and qualify inactive restore/rebinding without automatic work replay. The fresh
installer inspector is not a substitute for an installed-state reader.

```js
const {inventory, captureRequirements} = await createInstalledStorageInventory({
  inventory: composedInventory,
  directory: validatedInstallationDirectory,
  includeCredentials: true,
  credentialsReviewed: true,
  maxReleases: 32,
});
```

The reader calls the public `readInstalledServiceConfiguration()` and
`readInstallationConfiguration()` APIs, binds the saved application account,
namespace, installation and exact application-state directory, and requires the
actual `application-updates` participant and its component revision in the
composed census. It never invents a participant, resolves an inherited unknown
owner, or drops an existing omission. Repeated augmentation and preexisting broad
installer-root captures are refused.

## Explicit authority

The exact private files are `installer-input.json`, `application.json`,
`supervisor-configuration.json`, `initial-provisioning.json`,
`initial-provisioning.claim`, `installer-attempt.json`, `host-token`, and
`supervisor-token`. The first three raw configurations and both tokens require
both credential flags. They may contain credentials even when the signing trust
keys themselves are public. Otherwise the inventory declares five explicit
`credential-excluded` roots and blocking omissions. Values are never returned in
the declaration or error messages.

The supervisor's two authority ledgers are:

- `supervisor/owner/updates.sqlite3`
- `supervisor/service/service.sqlite3`

They are declared as authoritative file roots for compatibility with the existing
inventory schema. Their `reason` forbids raw live-file capture. The **required**
companion DTO is outside the strict inventory schema:

```json
{
  "schema": "amplifier-unified-installed-capture-requirements",
  "version": 1,
  "installationId": "saved-installation-id",
  "dataScope": "saved-scope",
  "participantId": "application-updates",
  "inventoryDigest": "exact-augmented-inventory-digest",
  "offlineOnly": true,
  "requiresStoppedApplication": true,
  "requiresClosedSupervisor": true,
  "credentials": {"included": true, "reviewed": true},
  "sqliteExports": [
    {
      "rootId": "installed:ledger:updates",
      "kind": "updates",
      "sourcePath": "/owned/install/supervisor/owner/updates.sqlite3",
      "required": true,
      "method": "updates-owner-frozen-sqlite-export"
    },
    {
      "rootId": "installed:ledger:service",
      "kind": "service",
      "sourcePath": "/owned/install/supervisor/service/service.sqlite3",
      "required": true,
      "method": "updates-owner-frozen-sqlite-export"
    }
  ],
  "retainedReleases": [],
  "recheckUnknownEntries": true
}
```

An archive consumer must validate the companion's exact inventory digest and
installation binding, obtain the Updates owner's frozen exports while the
application is proven stopped and supervisor ledgers are closed/exclusively
held, and substitute those exports for these exact ledger roots. It must refuse
if an export/proof is missing, not fall back to filesystem copying. A declared
`completeEligible: true` proves declaration consistency only. It does **not**
claim an archive exists, writers are excluded, or the export requirements are
satisfied. This reader intentionally provides no boolean or caller-supplied
export shortcut.

Known SQLite `-wal`, `-shm`, and `-journal` companions are explicitly omitted;
their authoritative transaction state belongs in those required frozen exports.
Known process discovery files and release staging scratch are rebuildable or
transient exclusions. Their exclusion never authorizes replay of interrupted
commands. Restored process identity and endpoints must be established afresh.

## Retained signed release and source provenance

For each exact `releases/releases/<digest>/receipt.json`, the reader verifies the
retained signed channel using the saved public trust keys and binds its candidate
ID/digest. Expired historical channels may still authenticate retained evidence;
they do not authorize a new installation or update. The receipt contains the exact
release file manifest and each component's repository, ref, and revision.

Before excluding a retained `package/` tree as reproducible code, the reader
streams each file through SHA-256 and checks exact manifest membership, size,
mode, and absence of links/untracked files/directories. Matching cached `.tgz`
bytes are likewise checked against their signed digest before exclusion. Changed,
untracked, unsigned, missing or linked code remains a blocking omission and is
never silently treated as safely reproducible. No package is executed.

Each `captureRequirements.retainedReleases` row contains:

- `rootId`: the authoritative receipt root ID;
- `identity`: exact signed release ID/version/revision/digest;
- `receiptSha256`: SHA-256 of the retained receipt bytes;
- `artifact`: signed URL, SHA-256 and bytes;
- `components`: signed source repository/ref/revision/name/version/root rows.

The offline archive must revalidate the exact retained receipt bytes and current
filesystem classifications at capture. This static reader does not freeze
external changes. Reproducible exclusions require restoration from the retained
signed artifact/provenance and fresh qualification before execution; they are not
an executable-code or dirty-source backup.

## Limits and incomplete coverage

This inventory reads only explicit installer locations during the requested
maintenance operation. It does not enumerate native sessions or hydrate the
application-state tree. The existing composed application-tree declaration and
native artifact attestations remain unchanged.

The default retained release bound is 32 (explicit maximum 64), with a global
100,000-entry / 1 GiB hashed-byte budget, a 64-directory-depth bound, 16 MiB per
signed receipt, and 1 MiB returned companion limit. The existing inventory's
256-root/omission limits still apply. Exceeding a bound refuses the operation
instead of silently truncating authoritative coverage. A broader backup needs a
paged owner-specific contract; raising these limits incidentally is not allowed.

Missing required files, unknown installer/supervisor/release entries, unsafe
links, unknown root ownership, unsupported external owner declarations, and
excluded credentials remain incomplete or refuse outright. The saved service
lifecycle must be explicitly configured. Installations without that validated
binding are unsupported here. This module neither invents native-writer exclusion
nor changes the native full-artifact contract.
