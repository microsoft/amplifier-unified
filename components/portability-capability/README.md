# Unified portability capability

Independent Node capability adapter and Python durable owner over public
`amplifier-portability` and negotiated native ACP transfer authority. There are no
AppService imports, native Python imports, shared global state or direct native
history writes in this product owner.

`createPortabilityCapabilities` accepts an installed Python owner launcher,
`inspectSession`, public host `beginTransfer`/`commitTransfer`/`cancelTransfer` and
`adoptTransferredSession`, `nativeTransfer`, three evidence callbacks, optional
`authorizeTransfer` and `onInvalidate`. It publishes one host-scoped portability
topic. Root-channel actions may carry an explicit `args.sessionId` AHP URI; the
bridge resolves it through authenticated session inspection and checks any trusted
selected-session context. Selected-channel actions also work. Incoming actions
omit sessionId; a native UUID is never a channel. Selected inspection returns
`source.sourceRevision` and `source.expectedExecutionRevision` without requiring
a worktree capability.
Agent requests require an actual authorizer; explicit UI actions are the default
human approval boundary. No approval is derived from transcript prose.

The Python command is `amplifier-unified-portability --config CONFIG`. Its config
is `{dataDir,label,workspaceRoots,exchangeDir,stageDir}`. Launch with an owned,
neutral `cwd` to avoid legacy source packages shadowing installed libraries.
`dataDir` must equal native `transferAuthorityDirectory`; stageDir must belong to
both the owner's workspaceRoots and native transferWorkspaceRoots. Paired peer
public keys are operator-owned private `dataDir/peers.json`. Exchange files must
be within exchangeDir; source/repository paths must be within workspaceRoots.

`TransferConnection(launcher).perform` provides an independent official-ACP-SDK
passive peer. It accepts `{nativeSessionId,historyHome,operation,args}` from the
trusted owner callbacks and never opens a runtime session. Composition must select
only explicitly configured engines; a signed engine name is not executable
launcher authority. Custom worker graphs require matching native
transferProbeCommand. Missing owner/probe support is an error, never empty data or
invented account readiness.

Evidence aggregate callbacks:

- exportTransferEvidence({session,transferId,limitBytes}) returns {evidence,omissions}.
- stageTransferEvidence({transferId,nativeSessionId,sourceHost,evidence,acceptPartial})
  returns retained-only owner receipts.
- activateTransferEvidence({session,transferId,nativeSessionId,sourceHost,releaseHash,evidence})
  imports historical records through their original owners.

The owner verifies every byte-preserving public envelope and signed paired-host
capsule before these callbacks. Combined owner evidence is bounded to8MiB, native
files/windows remain with native ACP, complete capsules to64MiB. Composition may
use smaller per-owner limits. Historical unknown receipts never become requests,
and source jobs/schedules/questions/approvals never gain executable authority.

Retained actions include inspect/export/stage/release/activate/cancel/discard and
evidence. Added receipt/command reads recover exact outcomes without reissuing
mutations. `command {commandId,sessionId?}` accepts the original selected AHP
session even after navigation; omit sessionId only for an original host-scoped
command. inspect accepts cursor/limit<=100. Explicit `reconcile {id}` only
inspects a committed activated native proof and recovers the original host
adoption and evidence import; it never repeats provider probes or native writes.
If native activation itself is unknown, reconciliation refuses. Activation's
original command identity survives a lost host-adoption response.

## Acceptance and remaining boundaries

Installed Python tests exercise two signed owners, real Git source capture and
restoration, bounded metadata, unknown evidence preservation, failed adoption and
explicit reconciliation without repeated effects. The Node test runs the
installed Python peer and verifies lazy scoped reads and agent approval refusal.
Native Core/Foundation fence/probe contracts have separate actual-adapter tests;
these owner tests label substituted host/provider callbacks as fixtures.

Source canonical transcript/events stay byte-identical in place under a permanent
Foundation fence after release. Source archival relocation is not performed.
External attachment bodies are an explicit reviewed omission (references remain),
and destination imported artifacts/operational records are historical evidence,
not automatically mounted UI canvases or live work. Native provider/model
selection is preserved; other native settings remain independently owned by the
destination. Full real-account, remote host transport, browser transfer flow and
all-component latest-source acceptance remain distribution qualifications.

Nested host mutations must use a separate durable command domain from the outer
capability action. For example, composition maps an outer command to
`portability:<command>:begin`, `:commit`, `:cancel`, and `:adopt`. Preserve that
mapping during explicit recovery; never generate new child IDs for a retry.

Source export is reversible preparation and returns its actual signed `review`.
`portability.review {path|id,sessionId?}` verifies a paired incoming capsule or
an exact locally signed receipt without creating a checkout or calling a provider.
It returns the capsule hash, exact signed omissions, bounded per-owner evidence
summaries, native file hashes and workspace identity. The entire review is at most
256KiB; oversized review fails explicitly. `stage` and irreversible `release`
require `reviewedCapsuleHash` matching the displayed package. A blanket omission
flag is not accepted. Clients should discard approval when the path or hash
changes and never restore approval from draft storage.

## Held transfer maintenance

The factory accepts `nativeParticipants`, one actual
`TransferConnection.quiescenceParticipant` per configured engine launcher, plus
`onMayBeIdle`. Give each launcher a distinct `ownerId` and its own `onMayBeIdle`
callback. Register the factory's aggregate participant as `portability` and map
the portability topic to that owner in the host's required coverage. A missing
or unqualified native participant refuses acquisition; it is never treated as idle.
Register the one-per-native-home `AdminConnection.quiescenceParticipant` **after**
the portability aggregate. Transfer peers hold process-local intake; admin holds
the one cross-process exclusive native writer gate. No second exclusive gate is
created by the transfer participant.

The aggregate counts selected-scope authorization, queued Node calls, complete
reverse host/evidence/native promises, Python lock waits, Git capture/restore,
and all transfer transitions. Its Python owner uses Foundation's optional durable
operations intake fence, acquired after its process OS lease. The lease now precedes
TransferNode initialization/recovery as well. Cancelling Python requests joins
thread and callback effects before releasing accounting. Transport loss preserves
unknown receipts; no transfer effect is retried.

Private `quiescence/inspect`, `quiescence/acquire`, `quiescence/release` RPCs carry
exact `{fenceId,commandId,purpose,instanceId,dataScope}` and authenticated release
proof. Durable owner fences/releases survive restart. Native process replacement
can reserve a fresh passive process for reconciliation, but cannot certify an old
transfer: product receipts and the admin writer gate retain that authority.
`onMayBeIdle` is only an advisory recheck signal, never release permission.

Forward the explicit `quiescenceAccess` map: portability inspect/review/receipt/
command/evidence are reads. **Portability reconcile is a mutation** because it
may adopt a destination host session and activate historical evidence. It must not
bypass held maintenance. Other transfer actions are also refused before effects
while held. Active session runtimes are still independently retired by the host.

Installed qualification uses `PORTABILITY_MODULE` for the built Node archive,
`PORTABILITY_PYTHON` for the owner wheel, `AMPLIFIER_ACP_PYTHON` for the native wheel,
`PORTABILITY_HOST_MODULE` and `PORTABILITY_ADMIN_MODULE` for public installed
packages, and `PORTABILITY_NATIVE_FIXTURE` for the offline fixture provider source.
The paired test runs real Git, native canonical storage, Core/Foundation, two
private owners/hosts, signed review/release and maintenance gates. Evidence owner
callbacks explicitly report fixture omissions. It does not contact real accounts,
assert browser acceptance, or activate a live deployment.

The built-in native readiness probe independently holds its native-home leases in the child process. Arbitrary custom probe launchers cannot claim this proof, so their transfer lifecycle refuses maintenance coverage. Idle callbacks and the negotiated admin notification are advisory wakeups only; the host always acquires a fresh held proof.

## Service-stop participation

The aggregate and configured native-transfer peers advertise an additive
`quiescenceParticipant.serviceStop:{version:1}`. Actual acquisition additionally
requires service-stop negotiation from both the installed Python owner's public
Foundation intake library and the configured native ACP peer. An older library,
unmanaged probe, or unnegotiated peer refuses coverage. No idle sample substitutes
for either held intake or the separate native-home admin writer gate.

`purpose:'service-stop'` retains exact nested
`serviceIdentity:{installationId,dataScope,ownerId,instanceId,releaseDigest}` at
all layers. External release uses the authenticated host service-lifecycle proof:
exact expected identity, unchanged installation/scope/owner/release, and either
stop-refused with the same instance/refusal receipt or resumed with a new instance
and explicit resume/exit/readiness receipts. Native and Python owners keep their
own identity/evidence checks. The bridge never treats caller assertions as
platform authentication.

Only the exact just-acquired live lease may unwind a conclusive pre-stop admission
refusal. Replacement/reconciliation cannot use that shortcut. No transfer work is
replayed after unknown effects or service loss. Existing recovery/update behavior
and private transfer receipts remain intact. This component alone does not prove
that a complete configured product or platform service is safe to stop.


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

For retention only, the participant holds product intake without acquiring native transfer/admin participants. Native hide owns its separate native exclusion. Pending transfer receipts and unresolved bindings remain protected; missing/invalid derived indexes refuse proof instead of being repaired during inspection.

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

Pending or uncertain transfers protect selected conversations. All retained bindings to a selected conversation, and any binding with an overlapping workspace (including another conversation), protect its files. This purpose holds product-local intake only: the native disposal owner separately proves native exclusion. No transfer connection is started to inspect references.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.
