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
