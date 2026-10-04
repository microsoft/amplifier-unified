# Amplifier Unified recovery capability

`@amplifier/unified-recovery-capability` is an independently installable, optional product owner over the public Amplifier ACP native maintenance API and the generic host quiescence API. It owns only its private recovery-job database. It never imports native Python objects, reads native paths, starts an agent, or copies another owner's store.

**Coverage is native-only, never a full-product backup.** Without additive native negotiation this owner archives selected native history/state/settings and performs a reversible reset of one session's noncanonical configuration. An explicitly configured full-native owner additionally supports cold, paged all-native authority capture and reviewed restoration into a new inactive destination. Other product stores, client drafts, general workspace contents, external artifacts and environment credential stores remain outside native coverage. Selected mode also omits unselected/delegated histories and shared authorities. Credentials in `keys.env` require a separate reviewed selection and authorization; history/configuration can still contain private content without that file.

## Composition contract

```ts
import {createRecoveryCapabilities} from '@amplifier/unified-recovery-capability';

const recovery = createRecoveryCapabilities({
  directory: '/private/product-state/recovery', // this owner's database only
  nativeAuthority: 'configured-native-account',
  nativeMaintenance: await admin.maintenanceCapabilities(), // initialize only; no worker
  restoreDestinationChoices: [{id: 'owned', label: 'Inactive recovery directory'}], // IDs only
  onMayBeIdle: () => hostControl.onMayBeIdle(), // advisory; never an idle proof
  nativeAdmin,       // stable, explicitly configured private ACP admin connection
  authorize,        // (context, operation, args) => {accountId}; authenticate reads too
  resolveSession,   // (AHP URI, context) => {nativeSessionId, historyCwd, nativeAuthority}
  quiescence: {
    admitQuiescence: input => host.admitQuiescence(input),
    inspectQuiescence: () => host.inspectQuiescence(),
    quiescenceReceipt: commandId => host.quiescenceReceipt(commandId),
    releaseQuiescence: input => host.releaseQuiescence(input),
    withQuiescenceMaintenance: (input, work) => host.withQuiescenceMaintenance(
      input, () => admin.withMaintenanceFence(input, work)),
  },
  onInvalidate: (topic, scope) => host.invalidateCapability(topic, scope),
});
```

Register `recovery.quiescenceParticipant` as required owner `recovery`. Map the recovery capability topic to that owner in host coverage. The manifest is host-scoped; copy its explicit `quiescenceAccess` map through any capability compositor. Only `recovery.list/job/command/preview` are classified `read`, and `recovery.reconcile` is classified `reconcile`. All other actions use ordinary mutation admission. Advertise `actionSchemas()` lazily. Bind class methods if a compositor extracts methods from the instance.

`nativeAdmin(operation,args,context)` must call `_amplifier/admin` with a **configured coordinator workspace**, never a client-supplied path. Use one stable private connection while a lease is active; do not silently reconnect or replay after a transport failure. The callback must preserve structured `error.data` and honor the native request's completion semantics. This owner invokes it under `withQuiescenceMaintenance` for held maintenance effects, not under nested ordinary `withExternalMutation`, which correctly refuses while intake is closed. Register the stable native bridge's `admin.quiescenceParticipant` once per native home as a separate required owner. Its native OS writer lease counts generation work and post-response provider sign-in; this recovery owner cannot certify those writers. Use that same `admin` connection for `nativeAdmin` and the scoped maintenance callback above. This requires the native admin lifecycle v1 boundary and a compatible public `AdminConnection`.

The native launcher must explicitly enable `adminMaintenance`, authorize `adminWorkspaceRoots`, and configure a verified external-writer policy (`foundation-cooperative` or `stopped`). The policy is a trusted deployment assertion: old noncooperating CLI writers must be stopped or the selection must be refused. Native API details are in amplifier-app-acp `docs/native-maintenance.md` (qualified here against commit `198efe6`).

`resolveSession` must use authenticated host metadata and return the immutable **history** directory, not a relocated execution directory. All selected sessions must belong to the one configured native authority. Agent-origin actions can select or inspect only their own single session; an agent cannot list account-wide recovery jobs. `authorize` must independently approve sensitive actions and authenticate the account; a client-supplied `privateContentReviewed` or `credentialsReviewed` flag is not authorization. No secret enters the topic snapshot.

### Trusted release proof

The host's `verifyRelease` callback reads `recovery.readReleaseEvidence({fenceId,commandId})`. This method is internal composition API, not a public action. Require a returned proof, compare its exact fence/command, `instanceId`, `dataScope`, native authority and durable `receiptId` to the authenticated running host, and create the host's `verified:true` proof for outcome **unchanged**. Do not accept client evidence as that proof. An owner receipt is written before requesting release and records `nativeLeaseDisposition: 'released' | 'not-acquired'` plus conclusive terminal state. It never claims a new application instance is ready.

The participant atomically closes its queue and exempts only the exact persisted `quiescing` job whose internal fence command matches the coordinator request. Unrelated queued/running/uncertain work refuses acquisition. All host/native child identities are deterministic and namespaced separately from outer capability command identities. An ambiguous native outcome retains both fences; no force-retirement or input replay occurs. Participant release records the exact authenticated context and proof in a durable transaction before removing its fence. If its acknowledgement is lost or a later participant fails, the host remains closed; retrying exactly that release after restart returns the saved receipt. Different proof or command identity refuses. `readReleaseEvidence` remains available after the owner released so the coordinator can authenticate that retry.

## Actions and review

Browser actions use `x-amplifier/capabilityAction` with `{version:1,channel:'ahp-root://',topic:'recovery',operation,args,commandId}`. Native agents include the optional `sessionId` routing selector and use that exact authenticated session channel; this retains trusted own-session context through the host bridge while the topic remains host-scoped. Agent account-wide listing is refused. Mutation calls return a durable **queued job**, before background acquisition, so their own host admission does not prevent quiescence. A completed outer host receipt means admission completed; inspect the owner job for actual backup/reset completion.

1. `recovery.prepare {sessions:[AHP_URI],parts:['session-history','session-state','native-settings'],privateContentReviewed:true,includeCredentials?,credentialsReviewed?}` pauses intake, gracefully retires idle native workers, reads an immutable native preview, then releases intake. It does not hold intake closed while a person reviews. Worker retirement can checkpoint history, so review is deliberately captured after retirement.
2. `recovery.preview {jobId,cursor?,limit?}` returns the exact `previewHash`, selected sessions/parts/bytes, bounded included/missing/excluded entries, omissions and credential coverage. Default page size25, maximum50. No absolute native paths or file contents are returned.
3. `recovery.snapshot {preparedJobId,previewHash}` separately authorizes the reviewed hash, reacquires quiescence, asks native ownership to validate unchanged files and exports a private TAR. An intervening native change is a known refusal that requires a fresh review; the owner never substitutes a newer hash.
4. `recovery.reset.prepare {sessionId,scope:'session-configuration',privateContentReviewed:true}` reviews one reversible configuration reset. `recovery.reset.apply {preparedJobId,previewHash}` removes only the native contract's specified noncanonical configuration keys/files, retaining originals. Native history/events/tasks/capacity receipts/ownership are preserved; there is no automatic resume.
5. Undo requires a **fresh** reset preview and `recovery.reset.restore {preparedJobId,previewHash,resetJobId,expectedPostResetHash}`. The native owner refuses changed post-reset state instead of overwriting newer configuration.

`recovery.job {jobId}` returns a selected descriptor; `recovery.command {commandId}` recovers exact original admission; `recovery.list {cursor?,limit?}` reads indexed metadata only, never historical payloads or native directories. Topic reads provide the first bounded page under `data.recovery`, with numeric revision. Job states: `queued`, `quiescing`, `running`, `releasing`, `prepared`, `succeeded`, `refused`, `unknown`. Pages are keyset based, including ties, and scoped to the authenticated account. A selected job carries its exact command, operation, revision, selected session URIs, result and coverage. Lists intentionally contain only ID, command, state, creation timestamp and revision.

### Private archive resource

A successful snapshot returns `{artifactId,sha256,bytes,contentType,resourceUri,...}`. Register scheme `amplifier-recovery` as a **read-only** resource provider:

```ts
{scheme: 'amplifier-recovery', read: (params, context) => recovery.resourceRead(params, context)}
```

The URI binds opaque job ID and full archive SHA, with `offset` and optional `maxBytes` (<=262144). Standard `{channel:'ahp-root://',uri,encoding:'base64'}` reads return `{data,encoding:'base64',contentType:'application/x-tar'}`. Advance by decoded chunk length until the descriptor byte count is reached; verify the completed archive SHA. Each chunk is also checked against native identity/offset/size/chunk SHA before it is returned. Account authorization is checked on every chunk. No arbitrary output path, native file path, browser-supplied artifact authority or full archive in topic state is supported.

## Owner lifetime

The owner acquires an exclusive SQLite OS lock before opening or migrating its records database. The lock uses a separate private `recovery-owner-lock.sqlite` file in DELETE journal mode and holds `BEGIN EXCLUSIVE` for the process lifetime; the jobs database has no lifetime transaction. A competing process fails before touching jobs or changing unfinished records to unknown. Closing the owner or actual process death releases the OS lock. There is no PID-file timeout, forced lock deletion or guessed takeover. The configured directory must reside on a filesystem that correctly implements SQLite locking. Do not delete or replace the lock file while an owner runs.

## Uncertain outcomes and limits

- A restart marks unfinished jobs unknown. Neither startup, duplicate mutation submission nor reconciliation repeats effects. Exact duplicate owner submissions return their original job; conflicting arguments refuse.
- `recovery.reconcile {jobId}` reads original native and host receipts. A successful snapshot receipt can recover a lost result only when this owner already recorded conclusive native lease release. Lost host release acknowledgements recover from the original host receipt or an exact stored owner release proof.
- An unknown native acquisition/release, partial reset, or reset finalization marker cannot be inferred safe from a snapshot or a receipt alone. These remain fenced. The lower native API provides explicitly reviewed completed-marker reconciliation; this facade does **not** yet automate that operator workflow. A pending full restore likewise remains fenced: passive evidence cannot finalize it. Generation reclamation, retention deletion, full-product backup and destructive canonical reset are not advertised.
- The owner captures a bounded selected native manifest; no atomic snapshot across arbitrary product stores is claimed. A distribution-level aggregate must stop or obtain real leases from each included owner and record its own complete inclusion manifest.
- Only one maintenance job may be unsettled per configured authority. Native TTL/process-death locking prevents an abandoned connection holding an eternal OS lease; it does not authorize reopening the durable host intake fence.

## Qualification

```sh
npm ci
RECOVERY_HOST_MODULE=/installed/@amplifier/unified-host/dist/index.js \
RECOVERY_NATIVE_PYTHON=/owned/native-wheel-environment/bin/python \
RECOVERY_NATIVE_PROVIDER=/owned/amplifier-app-acp/tests/fixtures/provider \
RECOVERY_ADMIN_MODULE=/installed/@amplifier/unified-native-capabilities/dist/index.js \
npm test
```

Tests consume an independently installed host archive (`a293a647`) and installed native wheel (`ce35648`) and native administration bridge (`8543ca4`) through public APIs. A second actual-native run registers the separate admin participant and executes recovery only under its exact held maintenance scope. The actual Core/Foundation case creates a native session via the official AHP client, proves retirement precedes capture, streams and hashes the archive, and checks exact reset/undo plus unchanged canonical files. Deterministic transport fixtures cover unknown/lost results, exact self-job exemption, stale source refusal, account boundaries, indexed pages, restart non-replay, an actual competing owner process, crash/reopen OS locking, and lost participant-release acknowledgement followed by exact restart reconciliation. These tests make no account/model call and do not establish browser presentation, full-product aggregate backup or external legacy CLI cooperation.

### Explicit native configuration archive plans

`recovery.archive.prepare` is additive; existing selected snapshot/reset requests
remain compatible. Arguments are `{sessions:[AHP URI],parts,privateContentReviewed:true,
workspaceConfigurationFor?:[AHP URI],includeCredentials?:boolean,credentialsReviewed?:boolean}`.
At most32 conversations may be selected. Workspace configuration selectors must
be a subset of those conversations. The trusted `resolveSession` callback supplies
canonical native identity/history cwd; callers never supply filesystem authority.
Agents remain restricted to their authenticated single conversation, and `authorize`
must approve private-content/credential scope for every request.

Parts: `session-history`, `session-state`, `shared-configuration`, and
`workspace-configuration`. Shared configuration includes native settings, routing,
bundles and adapter settings. Workspace configuration includes the selected
workspaces' `.amplifier` settings/routing/bundles only. Reviewed keys.env inclusion
requires shared configuration and both credential flags. Inline secrets can still
exist elsewhere even when keys.env is excluded.

The job is persisted and returned before host quiescence, then creates/appends/seals
a private native plan under the exact maintenance fence. These are separate stable
child commands; lost responses never repeat them. `recovery.reconcile` may recover
an exact sealed preview or conclusively abandon an incomplete private plan; it does
not continue that plan or rerun its effects. An uncertain native receipt stays fenced.

`recovery.preview` pages the immutable native manifest rather than storing it in
this owner's job record. Existing fields are retained: `items` with `kind/path/bytes/sha256`,
`nextCursor,totalEntries,bytes,parts,credentialCoverage,omissions`; `exclusions` is
additive. Cursors bind the exact preview hash. No native absolute source paths or
private content appear in public pages. Existing `recovery.snapshot` and private
artifact resources consume this review, with unchanged byte/hash verification.

Configuration trees require trusted native launcher setting
`maintenanceExternalWriters:'stopped'`; a browser claim does not prove that external
CLI/tools stopped. Native-home exclusive writer fencing remains mandatory. Without additional negotiation this
owner supports only explicitly selected native files/configuration. Independent
product stores, browser drafts and external credential stores remain outside its
authority. Full-native behavior is described below. The returned coverage and
omissions must stay visible. Hard deletion, credentials reset and safe generation
reclamation remain unavailable. Product-wide backup requires real
fenced export participants from every configured independent owner.

## Persistent service fences

The trusted quiescence participant advertises `serviceStop: {version: 1}` only
for the retained service-stop contract. Acquisition copies the complete
`serviceIdentity` (`installationId`, `dataScope`, `ownerId`, `instanceId`,
`releaseDigest`) into its existing durable intake fence. Restarting does not
open intake or replay admitted work. Python owners require the installed
Foundation `DurableIntakeFence.SERVICE_STOP_VERSION` marker.

Release requires the host-authenticated `kind: 'service-lifecycle'` proof bound
to the exact fence, command, original identity, and observed instance. A resumed
service needs distinct-instance exit/readiness receipts; a refused stop needs
the exact original-instance refusal receipt. The complete proof is retained.
Exact completed retries are passive, and changed proofs refuse even as the first
request after restart. Generic recovery/update proofs do not release this fence.

An `admission-refused` rollback is restricted to the newly acquired live lease.
It is unavailable through reconciliation or after an unknown outcome. Reads of
existing receipts stay available while intake is held. Platform authentication,
process ownership, stop/resume signaling, and aggregate coverage remain with the
host and supervisor; this owner does not infer them from a PID or missing socket.

The distribution's installed owner-service matrix covers fresh reopen, changed
proof, unknown rollback, and public bridge behavior on Node22/Python311+313.

## Negotiated full-native capture and inactive restore

Composition passes the exact cloned `native.admin.maintenance` returned by
`AdminConnection.maintenanceCapabilities()`. This is one lazy control-plane
initialization, without a Core worker or directory/history enumeration. Missing
markers preserve the original selected-only contract. `recovery.list` and topic
`data.recovery` expose `capabilities:{archiveParts,restoreAvailable,restoreDestinations}`.
Destination choices contain only trusted opaque IDs and display labels; absolute
paths remain in native launcher `maintenanceRestoreRoots`. No caller supplies a
native home, archive file path or output directory.

When advertised, `recovery.archive.prepare` also accepts `native-import-records`,
`native-preference-receipts`, `native-maintenance-records` and
`native-retained-archives`. Retained archives require maintenance records. These
store-only selections may use `sessions:[]`. Their owner-made SQLite images and
streamed artifacts preserve the explicit pre-existing cutoff and self-exclusion.

A distinct full selection is `{sessions:[],parts:['full-native-authority'],
privateContentReviewed:true,includeCredentials?,credentialsReviewed?}`. It cannot
be combined with selected parts/session aliases and requires UI account-level
authorization. Native inventory is performed only for this requested maintenance
job. It includes canonical histories/events, settings, declared source/generation
authority and native receipt stores. Missing/unknown authority is refused or
recorded as an authoritative omission; derived/transient exclusions are separately
classified. `recovery.preview` returns paged entries plus `ownerCoverage`, including
`completeNativeBackup`, `completeProductBackup:false` and omission counts. The
string scope `full-configured-native-authority` alone is **not** proof of complete
coverage. Credential omission can make a full selection incomplete.

A successful full snapshot includes `format:'amplifier-native-authority',version:1`.
Only that account's exact successful snapshot can be restored:

1. `recovery.restore.prepare {snapshotJobId,sha256,destination:{rootId,name},
   privateContentReviewed:true,credentialsReviewed?}` queues native digest, format,
   manifest and new-destination checks under coordinated quiescence.
2. `recovery.preview {jobId,cursor?,limit?}` returns the immutable restore review,
   bounded manifest and `restore:{destination,requiresRuntimeQualification:true,
   requiresConfigurationReview:true,overwritesExisting:false,startsWorker:false}`.
3. `recovery.restore.apply {preparedJobId,previewHash}` creates the exact reviewed
   new destination. It never overwrites a destination, activates a worker or
   replays saved input. Native project configuration remains staged for explicit
   rebinding; source dependencies and runtime require fresh qualification.

Jobs are persisted before asynchronous acquisition and retain the original child
command identity. Closing this owner joins its outstanding work. A lost apply
acknowledgement becomes `unknown`; `recovery.reconcile` calls only passive
`maintenance.restore.inspect`. It may recover a conclusive successful receipt
**only** when the native owner also proves the exact completion record exists and
its pending marker is gone. Otherwise both fences remain held. This facade never
calls the privileged native `maintenance.restore.reconcile` finalizer while an
unknown host fence is held. No extraction is retried.

Native full-operation work has its own joined, bounded deadline, distinct from
the short idle acquisition TTL. Default native full-operation timeout is600s;
launcher maximum is7200s. Set `nativeAdmin.timeoutMs` above the configured native
operation bound plus transport margin (for example7260000 for7200s). The
distribution defaults to1200000ms when maintenance or recovery is configured.
A transport timeout remains unknown and never implies the file thread finished.

Installed qualification covers actual host→ACP→Core/Foundation capture and
retirement, streamed archive checks, full-native review, new-directory restore,
lost final acknowledgement→passive recovery, original byte preservation and
zero worker reactivation. Independent peer fixtures cover capability absence,
account/agent boundaries, exact digest/destination restrictions, pending native
finalization refusal and shutdown joining. No account/model, browser, live-service
or whole-product aggregate claim is made.


## Held retention inspection

The actual quiescence participant advertises `retentionHide: {version: 1}`.
Acquire it with purpose `retention-hide` and the exact coordinator fence context.
The returned live lease exposes
`inspectRetentionReferences({sessions: ["ahp-session:/..."], limit: 101})`.
The family must contain 1–101 distinct explicit session URIs. The result is
`{coverage: "complete" | "partial", protected: [{session, reasons}], omissions}`.
Only the original held lease can inspect; release, uncertainty or replacement
invalidates that authority. Release reconciliation requires the same authenticated
coordinator proof and never retries effects. A pre-effect admission rollback is
allowed only on the original live lease. The purpose grants no mutation rights.

Inspection reads bounded indexed owner metadata while intake remains closed.
It never starts a native worker, scans canonical histories, deletes product records,
or interprets absence of a runtime as absence of deferred work. `complete` describes
this owner's reference coverage, not permission to hide or delete a conversation.
The coordinator must hold every configured owner, check all results, and use the
native history owner's separate preservation-first hide boundary.

Any queued, running, releasing or uncertain recovery job prevents the hold. The exact-own-job exemption applies only to recovery, never retention. An acquired retention lease therefore reports complete empty deferred references.

## Reviewed cache maintenance

When the configured native administration connection negotiates
`maintenance.cacheInventory` v1 (cold, paged, bytecode-only, recovery lease), this
owner advertises `recovery.cache.scan`, `.page`, `.preview` and `.clear`.
No direct cache-clearing action is exposed through a generic maintenance topic.

1. `recovery.cache.scan {}` admits a durable queued job before attempting host
   quiescence. A successful job retains the native immutable scan ID, revision,
   reference revision, expiry and truthful partial coverage. An optional
   `sessionId` is checked against authenticated context; no browser path is used.
2. `recovery.cache.page {jobId, revision, cursor?, limit?}` reads at most 50 rows
   from that account's successful scan. The selected scan's original native
   context is retained across client navigation. Inventory is cold and explicit;
   opening the topic does not scan sources or canonical histories.
3. `recovery.cache.preview {scanJobId, revision, ids, reviewed:true}` admits a
   review job for 1–50 exact clearable row IDs. `recovery.preview {jobId}` returns
   the immutable bytecode review and expiry. Protected source rows are refused.
4. `recovery.cache.clear {preparedJobId, previewHash}` uses the existing all-owner
   recovery hold and the native owner's joined exclusive writer operation. Only
   exactly reconstructible, unchanged, native-owned Python bytecode is eligible.
   Dirty/untracked/unpushed sources, uncertain use, external/shared source
   ownership and retained generation references never imply permission to clear.

The caller preserves the original command identity. `recovery.command`,
`recovery.job` and explicit `recovery.reconcile` inspect exact original native
receipts after a lost result; they never redispatch the clear. Unknown outcomes
retain intake. A known stale/refused review releases intake without changing
files. Preview generation writes only derived review metadata and cannot remove
files. Owner shutdown joins admitted work, including a long scan. Timestamp
fields are native Unix seconds.

Cache result coverage is `verified-native-owned-bytecode-only`; successful clear
proofs require zero source directories removed and zero canonical files changed.
This is not source-object garbage collection or a claim of complete ecosystem
cache authority. Ordinary native cache policy and external-writer requirements
remain enforced by the independently installed native adapter.

## Managed-file disposal protection

`quiescenceParticipant.managedFiles = {version: 1, preservesCanonical: true}` is a separate contract from `retentionHide`. It holds the real owner intake with purpose `managed-files-disposal`, preserving every owner record. The acquired lease exposes:

```ts
inspectManagedFilesReferences({
  sessions: [rootSession, ...descendants], // explicit AHP URIs, at most 101
  limit: 101,
  allocation: {allocationId, executionDirectory, allocationHash, treeHash, entryCount, bytes}
}) // {coverage: 'complete' | 'partial', protected: [{session, reasons}], omissions: [...] }
```

The allocation comes from the trusted host's native-reviewed managed allocation, never a browser path. It is bound with the exact selected family on the first inspection of the live lease; a different review is refused. This read does not grant file deletion. The coordinator must bind release to its exact durable managed-files effect receipt, keep unknown fences held, and refuse partial/protected coverage. Lease inspectors expire before release begins, including unknown release; admission-refused rollback is available only on the original live lease. Restart reconciliation requires the existing exact verified release proof.

No nonterminal/unknown job may coexist with this held gate; the exact own-job exception remains exclusive to purpose recovery. Completed artifacts/manifests live in native maintenance storage and their preview/read routes do not lazily reopen execution files.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

### Optional reversible app-local reset

`nativeMaintenance.appReset` v1 plus registered `appResetOwners` exposes three
explicit parts: `native.app-bundle-default`, `notifications.settings`, and
`notifications.credentials`. Unsupported/duplicate selections refuse. Nothing is
selected by default. Metadata negotiation reads no settings or secrets. This is a
partial app reset: supervisor preferences, host policy, conversation rows,
shared/workspace settings and keys.env, ChatGPT sign-in profiles, histories,
source caches, generation state and client drafts remain outside this operation.

The existing queued job path admits `recovery.appReset.prepare` with `parts`,
`privateContentReviewed:true`, optional `credentialsReviewed:true` (required for
notification credentials), and optional `restoreResetJobId`. Apply takes the
exact `preparedJobId` and `previewHash`. Undo needs a new prepare with
`restoreResetJobId`, then `recovery.appReset.restore` with that review, the
original `resetJobId` and `expectedPostResetRevision`. `recovery.preview` pages
redacted owner manifests; expiries are Unix seconds. `recovery.job`, `.command`
and `.reconcile` retain their account-bound, no-replay behavior. Private retained
images are unencrypted in each owner store, never in public topics/results.

This slice is explicit account-level user administration; normal agent-origin
requests refuse even with an empty session selection. `authorize` is invoked
again for apply/restore with canonical parts and review flags derived from the
account-owned prepared job. The launcher must require its private-credential
policy for `notifications.credentials`; caller flags alone never grant authority.
The notify settings part preserves credentials without copying them into its
before-image. Clearing credentials used by enabled delivery also requires the
separately selected settings part, which disables delivery.

Composition passes `appResetOwners:[notifications.appReset]`. Native ownership
is registered only from the exact initialized peer marker and calls existing
`nativeAdmin`. All ports run inside `host.withQuiescenceMaintenance` and
`admin.withMaintenanceFence`; notification ports independently match the exact
persisted recovery fence. Normal owner saves remain blocked. A port has
`{id,parts,perform(operation,args,fence,callerContext)}` with prepare/apply/inspect/
restore. Each owner stores private originals before mutation and returns exact
redacted receipts. Preflight checks all reviewed revisions before the first
mutation; every owner repeats CAS under its own lock. This is **not** an atomic
multi-owner transaction. Partial/unknown outcomes keep all intake held with
`ownerProgress`; exact passive receipts may prove completion, but absent/unknown
receipts never trigger replay or compensation. Undo refuses newer state.

Native preflight inspection can refuse with RPC `-32000` and the exact reason
`native-maintenance-busy` or `native-app-reset-settings-busy`. Only when that
native owner proves `executed:false` and `replayed:false`, before any owner command
or receipt has been reserved, does recovery preserve the reason and release
intake as a known refusal. Foreign-owner errors, missing proof, lost transport,
and errors after reserved effects remain unknown; the owner never retries them
automatically. A later settings change still requires a fresh review.

New owner-private reset tables are part of that owner's authoritative store and
must remain in its private backup declaration. Native reset images live in the
already-declared maintenance receipt database; notification images live in its
existing notification database. This does not expand native-only archive
coverage to notification or other product stores.

## Explicit conversation presentation reset

The optional `conversationPresentation` port binds the host's public prepare,
review, apply, receipt, reconcile, readiness and explicit rebuild methods.
Its preservation marker promises that visibility changes preserve canonical
history and exact-ID authority. No cross-owner reference census is required.
Native history reset, app settings reset, archives and deletion retain their
existing safeguards and are separate operations.

`recovery.presentation.prepare` accepts up to 500 unique explicit session IDs,
`operation: reset | restore`, and `reviewed: true`. Restore names the exact
successful original `resetJobId` and selection. The host captures each selected
row's identity/revision and retains private before-images; recovery retains only
opaque review metadata. `recovery.preview` pages at most 50 rows.
`recovery.presentation.apply` consumes the account-owned `preparedJobId` and
`previewHash`. Authorization receives the reviewed operation and selection;
agents remain restricted to their own conversation. The host's selected-row
compare-and-swap allows unrelated reset/undo activity without a global revision
veto. Caller-supplied fences, actors or completion evidence are not accepted.

These actions have a separate durable job lane. They never acquire a global
quiescence fence or call native maintenance. The host owns short selected-session
admission locks and the atomic marker/journal transaction. A confirmed marker
effect produces a successful job even if its separate `receipt.projection` is
pending or unknown. Discovery may remain unavailable until reconstruction;
exact-ID reads, receipts, artifacts and authorized work remain available.
Unknown visibility outcomes do not block unrelated native maintenance. Actual
in-flight calls still join shutdown, and previously retained global fences are
never silently discarded.

`recovery.reconcile` passively inspects the original receipt, including projection
readiness for a successful effect. It never repeats reset, restore or user work.
`recovery.presentation.rebuild` is a separate explicit account-reviewed command
with `expectedRevision` and `reviewed: true`; it repairs derived discovery only.
Its own receipt and passive reconciliation remain distinct from the original
visibility effect. Lost/malformed replies retain uncertainty; no automatic retry,
reconstruction, agent startup or execution-authority change is introduced.


### Generic ACP host composition

A host without Amplifier native administration uses the same package's
`createPresentationCapabilities(options: PresentationOptions)` factory:

```ts
createPresentationCapabilities({
  directory,                  // owner-private durable store
  conversationPresentation,  // complete negotiated public host port
  authorize,                 // authenticated account and operation policy
  onInvalidate,              // optional recovery-topic invalidation callback
  onMayBeIdle,               // optional completion notification
});
```

No native authority, session resolver, native administration connection or global
quiescence port is required. This mode exposes only presentation actions and the
common account-bound list, job, command, preview and reconcile reads. It does not
advertise native archives, settings reset, cache clearing or inactive native
restore. Supplying partial native configuration is rejected. Private artifact
reads also require the full native mode.

When native recovery is already configured, use `createRecoveryCapabilities`
with its existing required native options and the optional host presentation port.
Compose exactly one recovery topic owner and store; do not create a second owner
for visibility. The full factory does not infer a presentation-only mode from
missing native options. All native and app-reset admission rules stay unchanged.

Focused qualification covers source and independently installed package behavior
with an actual public host and deterministic catalog fixture: explicit selection,
per-row stale review and disjoint undo, account/agent scope, interrupted projection,
immutable effects with independent readiness, lost replies and passive restart
reconciliation, shutdown joining, and generic ACP composition without native ports.
The fixture does not establish real catalog rebuild, full distribution, browser or
live-service acceptance; those remain composition checks.
