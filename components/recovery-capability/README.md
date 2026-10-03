# Amplifier Unified recovery capability

`@amplifier/unified-recovery-capability` is an independently installable, optional product owner over the public Amplifier ACP native maintenance API and the generic host quiescence API. It owns only its private recovery-job database. It never imports native Python objects, reads native paths, starts an agent, or copies another owner's store.

**Coverage is explicitly selected native files, not a full-product or full-home backup.** The current implementation can archive selected native history/state/settings and perform an exact reversible reset of one session's noncanonical configuration. Other product stores, client drafts, workspaces, external artifacts, unselected/delegated histories, source/module caches, shared authorities and environment credential stores are explicit omissions. Credentials in `keys.env` require a separate reviewed selection and authorization; history/configuration can still contain private content without that file.

## Composition contract

```ts
import {createRecoveryCapabilities} from '@amplifier/unified-recovery-capability';

const recovery = createRecoveryCapabilities({
  directory: '/private/product-state/recovery', // this owner's database only
  nativeAuthority: 'configured-native-account',
  nativeAdmin,       // stable, explicitly configured private ACP admin connection
  authorize,        // (context, operation, args) => {accountId}; authenticate reads too
  resolveSession,   // (AHP URI, context) => {nativeSessionId, historyCwd, nativeAuthority}
  quiescence: {
    admitQuiescence: input => host.admitQuiescence(input),
    inspectQuiescence: () => host.inspectQuiescence(),
    quiescenceReceipt: commandId => host.quiescenceReceipt(commandId),
    releaseQuiescence: input => host.releaseQuiescence(input),
    withQuiescenceMaintenance: (input, work) => host.withQuiescenceMaintenance(input, work),
  },
  onInvalidate: (topic, scope) => host.invalidateCapability(topic, scope),
});
```

Register `recovery.quiescenceParticipant` as required owner `recovery`. Map the recovery capability topic to that owner in host coverage. The manifest is host-scoped; copy its explicit `quiescenceAccess` map through any capability compositor. Only `recovery.list/job/command/preview` are classified `read`, and `recovery.reconcile` is classified `reconcile`. All other actions use ordinary mutation admission. Advertise `actionSchemas()` lazily. Bind class methods if a compositor extracts methods from the instance.

`nativeAdmin(operation,args,context)` must call `_amplifier/admin` with a **configured coordinator workspace**, never a client-supplied path. Use one stable private connection while a lease is active; do not silently reconnect or replay after a transport failure. The callback must preserve structured `error.data` and honor the native request's completion semantics. This owner invokes it under `withQuiescenceMaintenance` for held maintenance effects, not under nested ordinary `withExternalMutation`, which correctly refuses while intake is closed. Other independent native admin writers still require their own real host quiescence participation; this owner cannot certify them.

The native launcher must explicitly enable `adminMaintenance`, authorize `adminWorkspaceRoots`, and configure a verified external-writer policy (`foundation-cooperative` or `stopped`). The policy is a trusted deployment assertion: old noncooperating CLI writers must be stopped or the selection must be refused. Native API details are in amplifier-app-acp `docs/native-maintenance.md` (qualified here against commit `198efe6`).

`resolveSession` must use authenticated host metadata and return the immutable **history** directory, not a relocated execution directory. All selected sessions must belong to the one configured native authority. Agent-origin actions can select or inspect only their own single session; an agent cannot list account-wide recovery jobs. `authorize` must independently approve sensitive actions and authenticate the account; a client-supplied `privateContentReviewed` or `credentialsReviewed` flag is not authorization. No secret enters the topic snapshot.

### Trusted release proof

The host's `verifyRelease` callback reads `recovery.readReleaseEvidence({fenceId,commandId})`. This method is internal composition API, not a public action. Require a returned proof, compare its exact fence/command, `instanceId`, `dataScope`, native authority and durable `receiptId` to the authenticated running host, and create the host's `verified:true` proof for outcome **unchanged**. Do not accept client evidence as that proof. An owner receipt is written before requesting release and records `nativeLeaseDisposition: 'released' | 'not-acquired'` plus conclusive terminal state. It never claims a new application instance is ready.

The participant atomically closes its queue and exempts only the exact persisted `quiescing` job whose internal fence command matches the coordinator request. Unrelated queued/running/uncertain work refuses acquisition. All host/native child identities are deterministic and namespaced separately from outer capability command identities. A participant release failure or ambiguous native outcome retains both fences; no force-retirement or input replay occurs.

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

## Uncertain outcomes and limits

- A restart marks unfinished jobs unknown. Neither startup, duplicate mutation submission nor reconciliation repeats effects. Exact duplicate owner submissions return their original job; conflicting arguments refuse.
- `recovery.reconcile {jobId}` reads original native and host receipts. A successful snapshot receipt can recover a lost result only when this owner already recorded conclusive native lease release. Lost host release acknowledgements recover from the original host receipt or an exact stored owner release proof.
- An unknown native acquisition/release, partial reset, or reset finalization marker cannot be inferred safe from a snapshot or a receipt alone. These remain fenced. The lower native API provides explicitly reviewed completed-marker reconciliation; this facade does **not** yet automate that operator workflow. No whole-archive restore, generation reclamation, retention deletion, full-home/product backup or destructive canonical reset is advertised.
- The owner captures a bounded selected native manifest; no atomic snapshot across arbitrary product stores is claimed. A distribution-level aggregate must stop or obtain real leases from each included owner and record its own complete inclusion manifest.
- Only one maintenance job may be unsettled per configured authority. Native TTL/process-death locking prevents an abandoned connection holding an eternal OS lease; it does not authorize reopening the durable host intake fence.

## Qualification

```sh
npm ci
RECOVERY_HOST_MODULE=/installed/@amplifier/unified-host/dist/index.js \
RECOVERY_NATIVE_PYTHON=/owned/native-wheel-environment/bin/python \
RECOVERY_NATIVE_PROVIDER=/owned/amplifier-app-acp/tests/fixtures/provider \
npm test
```

Tests consume an independently installed host archive (`a293a647`) and installed native wheel (`198efe6`) through public APIs. The actual Core/Foundation case creates a native session via the official AHP client, proves retirement precedes capture, streams and hashes the archive, and checks exact reset/undo plus unchanged canonical files. Deterministic transport fixtures cover unknown/lost results, exact self-job exemption, stale source refusal, account boundaries, indexed pages and restart non-replay. These tests make no account/model call and do not establish browser presentation, full-product aggregate backup or external legacy CLI cooperation.
