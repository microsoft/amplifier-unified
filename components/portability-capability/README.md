# Unified portability capability

Independent Node capability adapter and Python durable owner over public
`amplifier-portability` and negotiated native ACP transfer authority. There are no
AppService imports, native Python imports, shared global state or direct native
history writes in this product owner.

`createPortabilityCapabilities` accepts an installed Python owner launcher,
`inspectSession`, public host `beginTransfer`/`commitTransfer`/`cancelTransfer` and
`adoptTransferredSession`, `nativeTransfer`, three evidence callbacks, optional
`authorizeTransfer` and `onInvalidate`. It publishes one host-scoped portability
topic; selected-session actions remain scoped to their authenticated AHP URI.
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
mutations. inspect accepts cursor/limit<=100. Explicit `reconcile {id}` only
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
