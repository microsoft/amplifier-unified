# Unified coordination capability

An optional product owner for `coordination.list`, `coordination.wait`,
`coordination.followup`, `coordination.interrupt`, and `coordination.command`.
It uses public AHP host callbacks, the native ACP worker index, and Foundation's
storage-neutral `amplifier_operations.coordination` cursor/notification library.
There is no AppService, global client state, transcript import, or execution loop
in this owner. Client selection, drafts, and consumed cursors stay client-side.

Install the Python wheel and Node package independently. Configure the Python
launcher with `--config /absolute/config.json`; the file contains only
`{"dataDir":"/owned/coordination"}`. One process owns its durable command journal.
The Node package exports `createCoordinationCapabilities(options)`:

```ts
const coordination = createCoordinationCapabilities({
  owner: {command: python, args: ['-m', 'amplifier_unified_coordination.server',
    '--config', ownerConfig], cwd: neutralDirectory},
  listCoordinationSessions: args => host.listCoordinationSessions(args),
  readCoordinationSession: (session, args) => host.readCoordinationSession(session, args),
  readCoordinationWorkers: (session, args) => host.readCoordinationWorkers(session, args),
  controlCoordinationWorker: (session, operation, args) =>
    host.nativeControlExisting(session, operation, args),
  controlCoordinationSession: async args => {
    if (args.operation === 'followup') return host.submitTurn(args.session, {
      commandId: args.commandId, text: args.text,
      clientId: args.clientId, origin: args.origin,
    });
    const current = await host.inspectSession(args.session);
    if (!current.activeTurnId) return {accepted: false, executed: false,
      reason: 'No active turn to interrupt'};
    return host.interruptSession(args.session, {
      commandId: args.commandId, turnId: current.activeTurnId,
      clientId: args.clientId, origin: args.origin,
    });
  },
  observeSession: (session, listener) => host.observeSession(session, listener),
  onInvalidate,
});
```

Forward native `workers.changed` notifications through
`coordination.changed(context.session)`. This refreshes only existing explicit
waits; an unwatched notification never launches this owner or an agent. Mount
`manifest`, `read`, `action`, lazy `actionSchemas`, and `close` on the public
capability interface. Topic `coordination` is host-scoped. Topic URIs accept the
standard scope query, but authoritative scope comes from the host envelope.

The optional `readCoordinationAttention(session,{clientId})` callback can supply
bounded genuine questions/task-owner data: `questionIds`, `task` (id/status/
revision/questionIds), `attentionComplete`, `attentionCoverage`, and `omissions`.
The adapter restricts this overlay to those fields and 32 KiB. Without it, missing
question authority is explicit (`questionIds:null`, `attentionUnknown:true`).
Host approval coverage and its truncation flag remain authoritative when this
overlay adds question coverage. Partial question or truncated approval state keeps
attentionUnknown true. Never substitute empty lists for unavailable owner state.

## Bounded reads and notifications

When a trusted `history: {inspect, read}` port is configured, the owner also
advertises `coordination.read {sessionId, cursor?, limit?, textLimit?}`. The
distribution supplies the Host's public passive native-history ports. No execution
worker or copied transcript is needed. Pages contain at most 50 messages and
4,000 Unicode characters per message, stable native message IDs, truncation and
coverage details, and explicit historical-reference attribution. This is a saved
history read; it is not a verified completion report or new human authorization.

Opaque cursors bind the authenticated reader, caller, target, native revision and
position. Multiple assistant messages within one native turn remain pageable.
Reconnect, restart or eviction requires a fresh read; a changed native revision
refuses continuation. Only cursor positions are retained, with four concurrent
reads and 4,096 cursor positions maximum. Native availability and access checks
remain with the Host. Without this port the action is absent.

The compiled Web/installed Native acceptance lives in
`amplifier-unified-client-web/tests/peer-history-native-browser.mjs`. It checks both
user and agent callers, original native bytes, stale revisions and preservation of
the reader's selection and private draft with an impossible execution launcher.

- `list {cursor?,limit<=100}` returns host-indexed conversation metadata and
  `workersNotLoaded:true`. It does not query native sessions or the historical
  project catalog. Its `coverage` describes that limitation.
- `list {sessionId,workerId?,cursor?,limit<=100}` pages one native parent's saved
  worker index. Items preserve `target:{sessionId,workerId?}`, kind, title,
  lifecycle and advisory controls. An absent index is explicit, not evidence an
  imported session never had children. Worker controls require the original
  resident runtime, never a process recreated just to answer a read.
- `wait {targets,waitMs<=60000,maxBytes<=65536}` takes 1–8 distinct explicit targets.
  Each target may contain `afterCursor`. It returns bounded stable result IDs,
  `nextCursor`, `cursorGap`, `hasMore`, attention coverage, and per-target errors.
  Native reads are concurrent at most 4, selected requests time out after 10s, and
  only 32 waits may be retained. Temporary observers release on completion.
  The byte budget bounds report text; envelopes and metadata have a 2 MB ceiling.
- Reports are retained by their authoritative owners. Native workers retain 32
  report receipts; a dropped history prefix becomes an explicit cursor gap.
  Conversation results use the host's settled-result index, never passive
  transcript reconstruction. Older unindexed results are explicitly unavailable.

## Mutation authority and uncertain outcomes

`followup {sessionId,workerId?,text}` and `interrupt {sessionId,workerId?}` require a
stable outer commandId. Agent-origin calls may target only their authenticated
calling conversation and its children. Only explicit user-origin calls can
message or interrupt another conversation. Arguments cannot grant authority.
Agent root follow-ups retain `origin:agent` and native `inputOrigin:coordination`;
imported history remains unverified and is never human consent.

The owner reserves its command before the public host/native call. A deterministic
`coordination:<sha256(outer commandId)>` child ID separates nested host/native
receipts from the outer capability receipt. Exact duplicate requests return the
saved result without resending. Changed content with the same ID is refused.
Initialization has a 15s deadline; replies have a 90s deadline (waits allow 60s).
Trusted launcher requestTimeoutMs/initializeTimeoutMs may lower those bounds.
Timeouts clear pending transport entries, never retry, and permit exact receipt
inspection on a still-live owner. A lost result is unknown, survives owner restart, and never causes automatic
replay. `command {commandId}` reads the exact receipt after rechecking access to
its original target. Accepted means admitted/requested, not completed or undone;
wait on the original target for results. Child follow-up/interruption also have
independent native receipts and validate the original resident worker run ID.

## Qualification and retained limits

Tests exercise the installed Python owner through the independent Node transport;
cursor reuse, retention gaps, event wakeups, scope authorization, unknown receipts
across restart, bounded metadata-only listing, and exact command recovery. The
optional installed-host test uses official AHP plus the native ACP package with a
real Core/Foundation persistent child and an offline provider. Native package
coverage separately proves cold passive inspection with an impossible worker
launcher and a 25k-row query-plan fixture. These fixtures do not prove a paid
provider delegated-agent run or browser acceptance.

Explicit integration/feature limits: global inventory covers host-indexed roots;
older native worker report indexes are not reconstructed from historical logs;
question/task attention requires the optional real owner callback; child steering
is not advertised until anchored delivery/withdrawal can be proved. Ordinary
root steering remains the standard host-owned AHP path. Arbitrary finite tool
jobs have lifecycle observations but no fabricated assistant report. No user
message, native transcript, approval, schedule, or execution is replayed by this
owner. No frontend draft or private selection is persisted here.

Build/test with `uv build --wheel --out-dir python/dist python`, install that wheel into
an isolated environment with Foundation's `amplifier-operations`, then set
`COORDINATION_PYTHON` for `npm test`. Python tests run against the installed wheel
from this component directory. The native integration additionally needs
`COORDINATION_HOST_PACKAGE` (a consumer package.json with the installed host),
`COORDINATION_NATIVE_EXECUTABLE`, `COORDINATION_NATIVE_CONFIG`, and
`COORDINATION_NATIVE_WORKSPACE`. The native fixture bundle must enable the native
package's offline provider `coordinationFixture` option and its fixture child.

`quiescenceParticipant(ownerId)` closes this owner's durable intake using the
optional Foundation operations fence. In-flight follow-up/interrupt calls refuse
acquisition; bounded reads, receipt inspection and passive explicit-target waits
remain available. An owner restart preserves the fence. Exact coordinator proof
releases it; unknown outcomes never reopen intake or resend a control. The optional
`onMayBeIdle` callback wakes a waiting coordinator once refused effects settle;
it is advisory, not an admission proof. Copy the exact `quiescenceAccess` mapping
through capability composition. Native execution has its own required participant.

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

Active work blocks acquisition. Retained uncertain owner commands protect their explicitly associated conversations; unscoped uncertainty cannot produce a false absence proof.

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

Pending and unknown followups protect their explicit target. Completed messages are copied text and canonical conversation references; this owner does not subsequently open execution-directory files.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

## Authority startup inspection

Under its existing owner lease, the Python owner checks existing authority schema read-only before writable authority connections. Missing required tables or columns, incompatible schema versions, and orphan WAL/SHM/journal files (including empty sidecars) refuse startup. A dangling or nonregular main/sidecar refuses too. Existing main and WAL/journal evidence is retained; SQLite may create its own SHM or empty WAL during read-only inspection. No historical row scan, startup hash, whole-database copy, mirror, or derived Catalog rebuild is added. Constructor refusal releases its lease.

The original f60de09 profile already required commands(id, signature, body). Complete unversioned profiles upgrade to marker 1 without changing original receipts. A missing command table is never a migration. Derived retention indexes remain reconstructible.

The reusable intake-fence database remains owned and checked by Foundation Operations. These checks certify fixed schema and bounded metadata, not every historical row or deleted whole-file authority. Native/model/account acceptance is separate from owned inert fixture checks.

## Pre-retirement distribution admission abort

The trusted Host participant forwards `abortAdmission(context_with_proof)` to the
private owner `quiescence.abortAdmission` route (slash form for workspace,
notifications and diagnostics). It negotiates `quiescence.admissionAbort.version=1`,
validates exact Host13 proof and typed owner receipt, and reports unsupported older
workers explicitly. The owner delegates to Foundation Operations' shared
`DurableIntakeFence` using its existing authority database, original acquisition or
refusal journal, and current active/background/pending accounting. No missing held
row, generic release proof or changed retry authorizes settlement. The private
`quiescence.admissionAbortReceipt` route observes an original completed receipt.

This is additive future-source support. Old deployed stageless/unrecorded attempts
remain unknown. Actual authenticated Updates proof, complete composition and live
adoption require independent qualification; this adapter does not reopen intake by
itself. Feedback's Python participant is only one subowner; its Node aggregate and
resources-upload participant require separate complete attempted-subowner receipts.

## Reviewed peer scopes

Optional `grants` ports advertise `coordination.grant`, `context`, `decide` and
`revoke`. Without these ports, neither the manifest nor action schemas expose
these controls. Composition supplies indexed ordinary-root identities, exact
host input provenance, and the normal AHP human approval surface.

Agent proposals require a current delivered human input and the actual root
actor. Applied steering qualifies; queued, held, historical, peer, question and
scheduled inputs do not. One input funds one exact proposal. Scope is limited
to eight roots in one workspace, explicit modes, idle-start and creation flags.
The existing command database retains proposals, decisions and revocations.
Approval never starts a model; after timeout/restart, a human can decide the
saved proposal without replaying a generation. Decisions recheck immutable input
content, root identity, interruption and participant locations. Active/pending
scopes protect their participants during retention and managed-file disposal.

An optional `delivery: {inspect, submit}` port enables `coordination.send` and
`coordination.result`. Queue delivery records the exact request before intake,
watches the recipient until idle, and rechecks scope, task, configuration, stop
state and native location. Composition must supply Host's guarded `submitPeer`
port and route the native `coordination.delivery.admit` callback back to this
owner. The saved original text is supplied by that callback, never by the native
caller. Forward Host's trusted `turnSettled` event to this owner.

Peer inputs remain agent-origin reference data in canonical history and public
projection. They cannot authorize another human scope. Duplicate identities
never send again; lost acknowledgements remain unknown and restart holds queued
work. Stopping, revoking or changing the recipient suppresses queued delivery.
The native runtime also rechecks its task budget and exact ownership at intake.

Acceptance, terminal completion and qualified results are different facts.
`coordination.result` reports the first two; it explicitly reports that independent
result qualification is unavailable. Peer task creation, notification-only
delivery, anchored steering and automatic result continuations remain separate
uninstalled ports. `coordination.context` reports the installed queue-only limit.
A recorded scope alone is never a delivery receipt.

`coordination.context` also returns at most 32 related request summaries with
2,048-character excerpts. Reading them never inspects or starts the recipient.
The shared web collaboration panel uses these receipts for Related work.
Human-only `coordination.resume` releases an exact held request that has never
been admitted, after rechecking its original scope and recipient state.
`coordination.cancel` cancels queued or held requests before admission. Neither
operation accepts replacement text, restarts an expired generation, or retries
unknown/admitted work. Stable control IDs reconcile lost replies without a second
effect. Restart/browser acceptance is in `coordination-peer-recovery-browser.mjs`;
that test uses a deterministic admission port, with actual native delivery checked
separately by the assembly's `peer-delivery-native.integration.test.mjs`.

## Passive peer messages

`coordination.send` also accepts `mode: "notify"` within an approved root scope.
The owner saves the original text and sender/recipient identity before notifying
watchers. It never calls prompt, steering, resume, or turn admission. The
session-scoped `peer-messages` topic exposes the most recent 32 notifications and
an explicit truncation flag; this makes the message visible while its chat is idle.

The matched Native adapter negotiates `features.peerNotifications`. At the next
natural root provider request it reads `coordination.notifications` through the
trusted host bridge, appends attributed agent-origin reference content, checkpoints
canonical history, then acknowledges exact inbox IDs. No delegated-worker hook is
installed. Retries reconcile those IDs without adding a second row. A revoked or
changed participant scope suppresses context delivery but preserves the inbox.

Canonical history retains an inline user-content row with `inputOrigin: peer`,
`recordedOnly: true`, and the saved `peerEnvelope`. It belongs to the existing
human turn and is not human authorization. The host preserves it alongside live
provider recordings; the web transcript deduplicates the inbox by request ID.
`queue` continues to use guarded admission when a response is explicitly requested.
This capability does not implement peer steering, result qualification, or
subscription-driven continuations.
