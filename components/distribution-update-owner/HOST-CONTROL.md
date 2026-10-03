# Authenticated host control

Version 0.3 connects the external distribution supervisor to the public host
quiescence API. It does not implement another work counter, native owner, or
service manager. Production composition must register all real capability,
background, media, native-runtime, and recovery participants with the host.

## Host composition

```ts
import {
  serveHostControl, createHostReleaseVerifier, connectSupervisorFile,
} from '@amplifier/unified-distribution-update-owner';

const supervisor = await connectSupervisorFile(privateSupervisorDiscovery);
let control;
const host = await createDistribution({
  // Other configuration belongs to the distribution.
  quiescence: {
    instanceId, dataScope, requiredOwners, coverage, participants,
    verifyRelease: createHostReleaseVerifier({
      supervisor: supervisor.owner,
      inspectRunning: () => verifiedActualRunningIdentity,
    }),
    onMayBeIdle: () => control?.notifyMayBeIdle(),
  },
});
control = await serveHostControl({
  host, // Or the distribution's public host object with the four methods below.
  inspectRunning: () => verifiedActualRunningIdentity,
  token: privateHostControlToken,
  discovery: {
    file: absolutePrivateDiscoveryPath,
    tokenFile: absolutePrivateTokenPath,
    dataScope,
  },
});
```

The example shows the ports to compose; it is not a claim about the enclosing
application's configuration names. `verifiedActualRunningIdentity` must reflect
the actual qualified installation, new process instance and owned data scope.
A copy of the requested target without installation verification is insufficient.

### Actual runtime identity (version 0.4)

The distribution's **signed executable entrypoint** can use the public helper:

```ts
import {createRuntimeIdentity} from '@amplifier/unified-distribution-update-owner';

let initialized = false;
const runtime = await createRuntimeIdentity({
  entrypointUrl: import.meta.url,
  trustedKeys: publisherKeys, // Configured independently of the receipt/target.
  isReady: () => initialized,
});
// Initialize the actual host and its required owners with runtime.instanceId
// and runtime.dataScope, then set initialized = true. A held intake fence is
// not itself an initialization failure: readiness must allow proof/recovery.
// Use runtime.inspectRunning in BOTH serveHostControl and
// createHostReleaseVerifier; neither needs to assemble an identity itself.
```

`createRuntimeIdentity` reads the launcher-provided receipt path, instance ID and
data scope from `AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT`,
`AMPLIFIER_DISTRIBUTION_INSTANCE_ID` and `AMPLIFIER_DISTRIBUTION_DATA_SCOPE`.
It accepts no requested release identity. It verifies the publisher signature,
selected descriptor, release digest, platform/architecture, and complete installed
file/component inventory. The actual process entrypoint (`process.argv[1]`) and
the caller's `import.meta.url` must both resolve to the signed entrypoint under
the receipt's sibling `package` tree; pointing at another valid signed candidate
cannot prove the currently running code. Candidate receipts must be private
regular files. Retained signed installation evidence remains valid after channel
expiry; freshness is still required separately for forward update discovery.

The returned frozen binding has `identity`, `instanceId`, `dataScope`, and
`inspectRunning()`. The latter rechecks the exact original receipt and complete
tree before reading the explicit readiness callback, returning a fresh
`RunningIdentity`. Simultaneous readers share verification; later reads do not
reuse a stale integrity result. Changed files/receipt, invalid readiness or a
missing launch binding throw a bounded error code and never report ready.

This proves a local signed installation and its launch binding, not hardware or
remote process attestation. Trusted keys must not be learned from the receipt or
an untrusted client. The distribution must load its application graph from the
qualified artifact; arbitrary external imports are outside this proof. Protect
installed files from mutation and initialize this helper at entrypoint startup.
A malicious same-user process, altered executable memory or a concurrent writer
racing file loads is outside this local verifier's security boundary.

### Update facade participant

An `application-updates` participant in the replaceable child covers its actual
in-flight RPC forwarding and local intake gate. It must not hold the independent
supervisor's download/install lifetime: that would make an update wait for itself.
Forwarded mutations return the supervisor's accepted durable receipt promptly,
with the same command ID retained if the reply is lost. Do not wait for operation
completion or replay a new command. Preserve authenticated read-only receipt,
proof and diagnostic access while fenced so release/recovery can finish. Durable
facade fences and process ownership remain the host composition's responsibility.

`serveHostControl` accepts these public host methods (preserve their `this` binding
when constructing a facade):

- `admitQuiescence({commandId, purpose:'distribution-update', retryRefused:true})`
- `inspectQuiescence()`
- `quiescenceReceipt(commandId)`
- `releaseQuiescence({fenceId, commandId, outcome, evidence})`

It also accepts `inspectRunning`, a 64-character hexadecimal secret, optional
loopback port, optional `onMayBeIdle(notify): unsubscribe`, and optional private
file discovery. It returns `url`, `notifyMayBeIdle()` and `close()`.
`onMayBeIdle` or direct notifications must come from owner transitions, not an
interval polling the host. A notification only wakes admission; it proves no
idleness by itself.

The required host contract is qualified from source
`fc28680e55d63f911df51277506ecd3f03bf508c`. Its explicit `retryRefused` option may
re-evaluate only a prior definitive `admitted:false`, `executed:false`,
`intakeClosed:false` receipt with no retained fence. It must never retry held,
checking, releasing or unknown admission. Without this option, command deduplication
would keep replaying an old busy refusal after the host becomes idle.

## Supervisor adapter

```ts
import {connectHostControlFile} from
  '@amplifier/unified-distribution-update-owner';

const hostControl = connectHostControlFile(absolutePrivateDiscoveryPath, dataScope);
export function createSupervisorPorts() {
  return {
    inspect: hostControl.inspect,
    admitRestart: hostControl.admitRestart,
    reconcileAdmission: hostControl.reconcileAdmission,
    onIdle: hostControl.onIdle,
    resolveSources, // Existing source owner; unrelated to quiescence.
  };
}
```

`new HostControlClient({dataScope, connect})` accepts an asynchronous private
connection resolver instead. `connect` returns `{url, token, dataScope}`. Every
command and SSE reconnect resolves it again, so an admission lease's release
reaches the replacement host's current endpoint. Neither an old URL nor a PID
from an unauthenticated readiness endpoint grants ownership.

The client exposes passive `inspect`, `inspectQuiescence`, `quiescenceReceipt`,
and the lifecycle methods above. An absent endpoint throws; it does not mean
idle or authorize a cold start. Initial provisioning must be a separate explicit
owned-empty-installation action. The portable supervisor still cannot adopt a
service or kill a child it did not start.

## Durable proof and release order

Before external admission, the owner durably records `admission_requested`.
Only an exact definitive no-effect busy response becomes `waiting_idle`.
An exception, lost reply or unknown admission records uncertainty and blocks
subsequent installs; idle events never retry that operation.

After confirmed admission, the owner stores the exact fence, original running
identity and data scope. It records `restart_requested` before any replacement
effect. If it refuses before requesting restart, it durably records
`failed / pre_restart_refused` **before** calling `lease.release('unchanged')`.
Do not move this write behind `finally`: the host verifier must be able to read
an authoritative no-effect receipt while the release request is in flight.

`DistributionUpdateOwner.restartProof(commandId)` and
`SupervisorClient.owner.restartProof(commandId)` expose the bounded private proof.
The authenticated supervisor transport operation is `restart-proof` with
`args.commandId`. This includes opaque fence, instance and data identities; it is
separate from shareable diagnostics and is not a browser action.

`createHostReleaseVerifier` reads that authenticated supervisor method itself.
It ignores assertions in the release request's `evidence` argument. It requires:

- Exact command, purpose, fence, original instance and data scope.
- For `ready`: durable succeeded/ready, exact target release/digest, actual new
  running instance, exact previous instance, and actual data scope.
- For `unchanged`: durable failed/pre_restart_refused, no restart instance,
  and the same actual release, instance and scope observed under admission.

`unknown` never opens intake. The host's own verifier, retained fence and real
participant leases remain authoritative. A transport error cannot be converted
into a safe null refusal or fabricated zero-work proof.

Explicit owner `reconcile(commandId)` can retry a ready release or a recorded
pre-restart refusal release. It verifies the observed installation again; it
never restarts. `AdmissionReconciliation.outcome` therefore supports `ready` and
`unchanged`. Already released exact host receipts make this idempotent. Unknown
admission has no fabricated acquired-lease proof and remains closed for explicit
recovery through the host's authoritative recovery workflow.

## Authentication, bounds and notifications

The server binds only to `127.0.0.1`. Requests need both the bearer secret and
`X-Amplifier-Host-Control: 1`; requests with `Origin` are rejected. The private
file connector validates regular non-symlink 0600 files on POSIX, absolute paths,
fixed schema, loopback URL and exact data scope. Discovery is replaced atomically;
closing an old server does not remove a replacement's discovery file.

- POST `/v1/host-control`: `{operation, args, dataScope}`. Operations are `running`,
  `inspect`, `receipt`, `admit`, `release`; unknown operations/arguments are refused.
- GET `/v1/host-events`: bounded SSE wakeups. Reconnection may repeat a wakeup,
  which only requests authoritative admission. It never repeats mutations.
- 16 KiB request, 64 KiB response, 32 concurrent requests, 32 SSE subscribers,
  bounded buffers and disconnected slow readers. Raw host exception text, worker
  paths and arbitrary work details are excluded from transport results.
- A ten-second client network deadline records an unknown mutation result; it
  does not cancel durable host effects or bound model inference. No automatic
  mutation retry occurs after full or partial response loss.

## Qualification

Set `DISTRIBUTION_HOST_ARCHIVE` to the exact independently built host archive to
run the gated installed-host test. It checks the archive SHA, installs it and the
packed supervisor in an independent consumer, and runs the real host in signed
fixture application child processes. It verifies busy refusal, event-driven
retry, exact process/digest/scope replacement, durable held participant recovery,
authenticated supervisor proof, passive reconciliation and rollback. It does
not connect to a production host or provider account.

Set `DISTRIBUTION_HOST_CONTROL_ACCEPTANCE_DIR` to retain the exact supervisor
package and sanitized receipt. The participant is a real persisted **fixture**
gate, labeled as such; configured production owner coverage, systemd/launchd
ownership, deployment and production acceptance remain integration work. The
ordinary suite reports this one test skipped when no archive is supplied.
