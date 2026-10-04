# Held owner snapshot staging

This trusted composition API stages immutable Operations store images. It does
not stop a service, create a full-product archive, restore or activate a runtime.
The existing offline archive contract remains in [OFFLINE-ARCHIVE.md](OFFLINE-ARCHIVE.md).

`createDistribution(config, ports)` optionally accepts
`ports.beforeRecoveryMaintenance({context, stageOwnerSnapshot})`. This callback
runs inside an actual Recovery job's already-held host and native administration
maintenance scopes, before the job's native work. `context` is its exact retained
`{fenceId, commandId, purpose:'recovery', instanceId, dataScope}`. The coordinator
must bind its separate capture command and private-content review to that whole
context and its original reviewed intent. The callback is trusted code; browser
arguments cannot install it or select filesystem paths.

The supplied `stageOwnerSnapshot` accepts only:

```js
{
  ownerId: 'capability:observations', // exact configured Operations participant
  snapshotCommandId: 'reviewed-original-capture',
  directory: '/canonical/owned/staging-directory',
  privateContentReviewed: true
}
```

The adapter inserts the actual fence identity; callers cannot replace it. Every
started capture promise is joined before native work proceeds, even if the
trusted callback forgets to await it. A callback/capture failure follows the
original Recovery job's unknown/fenced path. No extra release proof or bypass is
introduced. Normal completion retains the Recovery owner's original durable
release evidence and verifier.

The returned distribution also provides `stageOwnerSnapshot` with explicit
`fenceId` and `commandId` for a trusted coordinator that is outside a maintenance
scope. It enters `host.withQuiescenceMaintenance` itself. It requires the original
fresh live recovery lease, not a reconstructed or retained fence. Ordinary callers
must not nest this entrypoint inside an existing maintenance callback; use the
scope-bound function above. `inspectOwnerSnapshot({ownerId,commandId})` passively
reads the exact original owner receipt without capture or activation.

Before capture, the root adapter saves an exclusive private intent under
`directory/sha256(snapshotCommandId)/staging.json`. It binds the full context,
owner, capture command and explicit review. All repeated calls return that
original intent/receipt; unknown never redispatches capture. Lost acknowledgements
can be inspected through the original Operations receipt. The adapter does not
yet resume a partial local byte export automatically. Partial files and uncertain
intents remain evidence.

Only a fresh Operations recovery lease that negotiated snapshot API v1 supplies
capture authority. Release, unknown release or owner closure retires it. Exactly
seven stores are covered: intake, schedules, operations, questions, observations,
transfer and legacy. Missing stores remain explicit. The total image limit is
64 MiB; reads are at most 64 KiB. The exact manifest, receipt context signature,
every image length and SHA-256, and each page offset/EOF are verified before
`status:'sealed'`. The receipt always says `completeProductBackup:false` and
`workReplayed:false`.

The staged intake image retains its original held fence. Inactive restore must
keep that state inert; it cannot manufacture a fresh lease or clear an unknown
receipt. Operations' public `restore_inactive` additionally requires the reviewed
authenticated manifest hash and a caller-held guard.

Canonical history, native administration, native message metadata, media stores,
other product owners, retained prior snapshots, signed runtime inputs and
external authorities are outside these seven images. They need separate explicit
coverage. The stopped archive later captures the complete application tree,
including these receipts and retained snapshots, under its original independent
supervisor/native writer boundaries. A successful staging callback is never
proof that the application stopped or that all product/code roots are covered.

Current causal tests are `test/owner-snapshots.integration.test.mjs` and
`test/full-owner-snapshot.integration.test.mjs`. They use installed owner packages
and isolated synthetic state. A controlled fixture shutdown does not establish
production platform stop, physical-device or account-provider acceptance.


The hook centrally joins every requested stage and requires each result to be
`status: "sealed"`. A fulfilled `unknown` original receipt is not permission to
continue native maintenance. It leaves the original Recovery job and intake
fence unknown. The scoped callback expires when the trusted hook returns; saved
callbacks cannot stage later, and forgotten awaits cannot detach capture.

For subsequent stopped capture, `app.storageInventory` accepts trusted
`nativeCapturePlans: [{engineId, rootIds}]`. This optional private inventory field
is retained under the inventory digest. The producer binds the configured native
administration engine and actual participant to **all** of its declared
`authoritative` / `native-artifact` roots, including reciprocal native-message
ownership where applicable. Duplicate IDs, missing roots, foreign engines and
unconfigured owners refuse. The plan removes only the missing-sealed-native-
artifact omission; it leaves those roots uncovered and `completeEligible: false`.
The coherent consumer requires exactly the planned adapter engine set and sealed
artifact root sets. Other engines, external roots, runtime provenance omissions
and credential review requirements remain unchanged. Older inventories without
plans retain the existing explicit-artifact/omission behavior.

Qualification includes a disposable signed child with the actual 21 configured
participants, genuine Recovery staging/release, then actual service-stop and
joined child/owner shutdown before native/supervisor capture. This is not a
production platform-stop proof. The fixture signs its own exact Node package
forest; independently supplied Python wheels and read-only dependency donors are
reported separately, not mislabeled as a self-contained 23-repository archive.


Existing facade intake and Terminal authority are validated read-only under
the lifetime exclusion lease before either authority opens writable. Missing
required tables or columns refuse startup without replacing authority or changing
its original main database and pre-existing nonempty WAL. SQLite may create empty
WAL/SHM files or change derived SHM read marks during validation; those sidecars
are coordination state, not canonical owner work. Terminal's existing singleton
account/origin binding must also be present and match.

This bounded startup guard preserves legitimately empty fence tables and new
store initialization only when the main database and all WAL, SHM and rollback
journal paths are absent. A surviving sidecar refuses before connecting.
It does not establish detection of whole-database
disappearance with all sidecars, general row deletion or arbitrary schema tampering; inactive
restore still requires the original recovery proofs described above.
