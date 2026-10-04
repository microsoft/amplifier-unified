# Partial admission recovery

A failed admission may leave some participants fenced before the service was
retired. It is not a held admission and does not prove that work is idle. This
optional contract lets reconciliation unwind those partial holds without
restarting the service or repeating the update.

## Owner behavior

`owner.reconcile(originalCommandId)` considers this path only for an install,
activation, or rollback left `unknown` at `admission_requested`, with no recorded
admission, admitted running identity, replacement instance, or activation. The
original owned process must still be ready on the current release and data scope.
A separate durable `admissionAbort` intent is recorded before calling Host.

Lifecycle adapters optionally provide all three ports:

- `inspectAdmissionFence(commandId)`: the original command, fence, instance,
  data scope, and `distribution-update` purpose.
- `inspectAdmissionAbort(commandId)`: a completed Host abort receipt, or null.
- `abortAdmission(request)`: request the exact original abort and return its
  completed receipt. The request also binds the observed original running identity.

Portable and POSIX adapters require an actually owned live child. They cannot
adopt an unrelated process. Concurrent reconciliation of one command shares one
operation; shutdown waits for it. Missing ports, an in-flight transition, a
changed instance, or uncertain evidence leaves the original command unresolved.

Only an exact completed receipt plus a subsequent observation of the same
original process settles the update as `failed/admission_aborted`, with
`admissionSettlement: {state: "settled", outcome: "unchanged"}`. Current, previous,
and staged releases and other command receipts remain unchanged. No update is
retried; another attempt requires a new explicit command.

## Authenticated Host integration

Host exposes `abortQuiescenceAdmission(request)` and the passive
`quiescenceAdmissionAbortReceipt(commandId)`. The optional
`createHostAdmissionAbortVerifier({supervisor, inspectRunning})` reads the
supervisor's authenticated `restartProof(originalCommandId)` and checks the
persisted abort intent against the actual original process. Caller-supplied
assertions are not proof. The verifier belongs in Host's trusted composition as
`verifyAdmissionAbort`; existing release verification is unchanged.

Host must separately prove an explicit pre-retirement acquisition stage, reject
in-flight acquisition/release, serialize reverse-order unwind, and obtain durable
`released` or `not-acquired` evidence for every attempted participant. A legacy
fence without a recorded stage cannot be inferred safe from an apparently idle
service. The complete attempted-owner census is Host's responsibility.

The authenticated control transport adds `admission-abort` and the read-only
`admission-abort-receipt`. Both preserve exact instance and data-scope checks.
The completed result has this shape:

```ts
{
  schema: "host-admission-abort-v1",
  commandId, fenceId, instanceId, dataScope,
  status: "aborted", intakeClosed: false,
  owners: [{
    ownerId, commandId, fenceId, instanceId, dataScope,
    status: "released" | "not-acquired", receiptId
  }],
  receiptId
}
```

The owner validates exact bindings, closed receipt shapes, unique owner IDs and
a maximum of 128 owners. It cannot substitute an empty or incomplete census for
Host's proof. After a lost reply, reconciliation first reads the completed
receipt; it never reacquires admission to recover that reply.

## Compatibility and acceptance boundary

This is an additive integration contract. Existing lifecycle implementations
without these ports retain their unresolved receipts. A composition must wire a
Host and participants implementing the matching unwind contract before this
recovery is available. This package alone does not make old, stage-less fences
recoverable and does not authorize editing their databases or receipts.

Tests cover owner persistence, wrong bindings, missing/uncertain proof, repeated
and concurrent reconciliation, replacement-process refusal, authenticated control
transport, and POSIX ownership. Full installed-service acceptance additionally
requires the real Host and every attempted participant's durable unwind evidence.

## Synchronous Node forwarding owners

`createManualIngressGate` and the distribution's `FacadeFence` retain a separate
admission journal. Recording an acquisition and closing intake share one
transaction; a definite busy refusal is recorded explicitly. Missing state is
never evidence that acquisition did not happen. That refusal remains final for
the same fence after work finishes and after the owner reopens. A fresh attempt
must use a new fence; it cannot overwrite the earlier no-acquisition record.
An abort receipt and removal of
the matching hold commit together, so a lost reply can be answered from durable
evidence without acquiring again. An already completed pre-effect rollback can
also be acknowledged from its recorded disposition.

Only the distinct, exact `distribution-admission-abort` proof is accepted. A
different command, fence, instance, scope or proof cannot reopen intake. Passive
facade forwarding remains counted for shutdown and fresh acquisition, but is
excluded from the mutation count used for abort: otherwise the reconciliation
request would block itself. Manual network ingress still requires all forwarding
to finish; it does not classify unauthenticated HTTP requests as passive.

The ingress ledger moves from its complete v0/v1 schema to v2; the facade ledger
moves from its complete unmarked schema to v1. Both migrations are additive and
atomic under the owner lease. A missing table in an already marked new schema is
an error, not a repair opportunity. Existing held rows receive no fabricated
acquisition record and remain ineligible for this new recovery path.

The distribution composition accepts the verifier only from trusted launcher
options, never configuration JSON. Adoption requires the matching updater package
and Host admission-abort API plus coverage for **every attempted participant**.
An older or unsupported owner keeps the operation uncertain and intake closed.

`distribution/test/admission-abort-installed.test.mjs` exercises the actual packed
Host, supervisor transport, update facade and manual ingress gate together. It
covers an acquired hold, a recorded refusal, a lost completed reply and missing
participant support. Release/process identity and the acquisition reply fault
are explicit fixtures. This is not a full production owner census, signed process
replacement, deployment or recovery of legacy fences without admission stage.
