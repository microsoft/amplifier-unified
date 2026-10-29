# Boundary contracts and proof obligations

> **Candidate / WIP — discussion only.** This document does not govern current
> implementation, amend adopted contracts, or establish release requirements.
> See [package status](README.md) before proposing adoption.

Draft, October 28, 2026. These are proposed cross-component promises for the
[target architecture](target.md), not a second implementation of AHP or ACP.
Use the upstream schemas for protocol messages. Domain data below describes
semantics to settle in fixtures, not invented standard wire fields.

Unified's [host](../../../contracts/host-protocol.v1.md),
[agent](../../../contracts/agent-protocol.v1.md), and
[component](../../../contracts/component-boundaries.v1.md)
contracts remain the starting point. The additions here concern collaboration,
bounded coding assignments, evidence and the transition to alternate agents.
Move an implemented contract into its owning repository; the architecture index
then references that version instead of keeping competing normative copies.

## B1. Experience to durable host

**Owner:** host. **Consumers:** web, native/TUI, explicitly authorized agent clients.

- Commands carry a stable request identity and relevant expected revision. The
  host authorizes them, durably records admission or rejection, and makes the
  receipt queryable after a lost reply. A client retries transport using that
  identity; it does not create a new operation to discover an old outcome.
- Subscriptions expose authorized, scoped state with bounded replay or snapshot
  reconciliation. Reconnect does not replay commands or activate dormant agents.
- Private drafts and view selection belong to the client. Shared content enters
  the host through an explicit operation. Two viewers do not overwrite each
  other's input, attention state or scroll position.
- Authentication establishes principals. Collaboration owns project membership;
  the host enforces it for reads, streams, artifacts and commands. Reconnection
  must not restore access that has been revoked.
- Admission acknowledges persistence, not model execution or completion. State
  distinguishes queued, executing, uncertain, interrupted, finished and delivered
  where those distinctions affect what the person should do next.

**Proof:** real client against fixture host and real host against fixture client;
lost reply, duplicate identity, stale revision, two viewers, revoked access,
slow subscriber, bounded history and reconnect. Measure time to visible admission
separately from first provider activity and first user-visible model content.

## B2. Host to engine-bound agent

**Owner:** agent adapter for execution; host for routing/admission. **Boundary:**
ACP v1 with an exercised capability profile and separately advertised extensions.

- The host binds an execution session to one engine, native session ID, owning
  deployment and exclusive writer. An implementation update cannot silently
  change the engine for that session. A named collaboration agent may have many
  such sessions; its identity is not a model conversation identifier.
- Prompt, streamed updates, tool requests, permission requests and cancellation
  follow the pinned ACP version. Advertise only implemented optional methods.
  A missing capability returns an actionable unsupported state, not a silent
  fallback that changes execution semantics.
- Mid-turn steering names the intended active execution and preserves queued,
  applied, superseded or not-applied outcomes. For our v1 adapter it uses the
  existing advertised extension. A completed or cancelled run cannot be revived
  by a late permission reply or stale steering message.
- The adapter owns native history and locks. The host never interprets opaque
  engine state to claim cross-engine resume. Read-only inspection, portable facts,
  core run recovery and protocol session restoration are different capabilities.
- A durable dispatch record alone cannot prove remote acceptance. If a connection
  dies after a possible send, reconcile through the adapter's qualified lookup or
  return uncertainty. No automatic new prompt to resolve that ambiguity.

**Proof:** independent ACP client to our agent, host to a different ACP agent,
stream/steer/cancel, loss before/after send, engine mismatch, native duplicate-writer
rejection and honest unsupported resume. A mock pair is necessary but insufficient.

## B3. Collaboration to bounded work

**Owner:** collaboration for intent; runtime for attempts/actions/results.

The smallest assignment packet contains references to the authoritative project,
agent and work IDs; objective and acceptance revision; source repo/candidate;
necessary context and original records; permitted effects; resource/time budget;
and the required result destination. Credentials are not part of a copied packet.
Owners can use a typed in-process interface initially. Only remote boundaries need
serialization and durable transport.

- Parent intent is versioned. An assignment is a frozen view, not a writable copy
  of the parent task board. Changes to acceptance must return through its owner.
- A child gets a bounded fresh history, enough context to satisfy its obligations,
  and a share of the parent's budget and authority. Recursion does not reset a
  deadline, widen access, or remove parent checks.
- The runtime returns attempt identity, source/candidate revisions, artifacts,
  effect/check receipts, declared coverage, unresolved needs and stopping reason.
  Work status is derived from these records rather than manually synchronized
  across chat, assignment, queue and operations boards.
- Late evidence remains attached to the objective it actually answered. Its
  acceptance against newer intent is explicit reassessment, not silent rejection
  or reuse. Claimed completion is distinct from verified and delivered completion.
- Conflicting instructions from authorized people are recorded with provenance.
  The initial policy permits one accepted revision and explicit conflict; it does
  not pretend a language model can settle contradictory authority from chat order.

**Proof:** worker lacks irrelevant parent history but retains its own obligations;
late result after correction; conflicting directions; child success with failed
parent integration; exhausted shared budget; restart with inspectable attempt;
delivery after all viewers disconnect. Test actual user outcomes, not just records.

## B4. Runtime composition: context, models and strategy

**Owners:** existing `converge-context`, `converge-models`, `converge-strategy`.

- Context returns a proposed view with original-source references and omissions.
  Original requests, corrections and artifacts retain their owner and can be
  retrieved. Context compression cannot rewrite authorization or acceptance.
- Models advertise supported reasoning controls, streaming, token counting,
  continuation and optional provider compaction. Provider-specific behavior stays
  in that owner. The runtime requests capabilities rather than assuming every
  model supports identical settings or compaction semantics.
- Admission counts the actual complete request plus reserves against the selected
  model profile. Native capacity is at least 128K, preferably 256K, for the stated
  local target; selected request views may be smaller for quality and responsiveness.
- Strategy produces bounded next-action proposals from observed gaps and evidence.
  It cannot execute tools, replace checks or declare a parent satisfied by itself.
  Decision models are optional interchangeable policies, compared with simple
  baselines for quality and total latency before becoming the default.
- Streams retain interruption and tool-batch integrity. Incomplete arguments never
  become an effect merely because the UI already displayed partial output.

**Proof:** source continuity, truncation/compaction failures, exact request admission,
retrieval of an earlier obligation, unsupported thinking setting, overflow refusal,
steering during preparation and incomplete-tool cancellation. Freeze hard tasks
and acceptance before comparisons; retain unsolved cases and timeouts.

## B5. Execution and independent verification

**Owners:** execution for effects; verifier for check results; work owner for
acceptance against the requested revision.

- Every effect identifies location, source, authority, request and attempt. Its
  receipt distinguishes accepted, started, completed and unknown. Reusing an
  operation ID is not sufficient to claim an external effect occurred exactly once.
- Isolation is an explicit execution profile. Separate checkouts prevent ordinary
  file collisions; they are not automatically security boundaries. Containers or
  remote workers must qualify their stated filesystem, network and process limits.
- Checks bind to exact candidate/source/configuration revisions and preserve the
  trusted acceptance definition. A coding worker may propose a changed check,
  but cannot use that to silently weaken the active contract.
- Verification declares what it covered. A unit test or successful leaf artifact
  cannot stand in for assembled behavior or delivery. Uncheckable claims remain
  judgment. Failed and inconclusive evidence remains available.

**Proof:** two concurrent isolated candidates, mismatched source rejection,
cancel/inspect a long command, unchanged parent acceptance, mutation of a check,
unknown external effect, and rerunning a verified candidate in composition.

## B6. Resources and optional experiences

**Owners:** reusable capability repositories; experience composes them.

- Artifacts have stable, scoped references and explicit versions. Catalogs can be
  rebuilt without altering native history or artifact ownership. Listing history
  never creates a running execution session.
- Scheduling owns due-run claims and submits stable host intents. Voice/media owns
  media lifetime and transcript provenance; hanging up does not revoke work already
  admitted. Neither becomes a parallel work authority.
- The Storybook library contains presentation components with props/events and
  accessibility fixtures. Host calls and application policy live in the experience
  adapter. The current MCP App viewer needs that separation before extraction.
- MCP Apps retain host-enforced resource/tool scope. A2UI and A2A are optional
  capabilities to qualify when a real journey needs them. Ordinary coding does
  not depend on adopting every protocol.

**Proof:** fixture-only component development; real host/app resource exchange;
wrong project artifact rejected; history listing starts zero workers; scheduled
retry retains identity; voice hangup leaves the admitted assignment inspectable.

## B7. Component evolution and distribution

**Owner:** each component for its published behavior; distribution for composition.

- A component installs and tests without sibling private-source imports. Its
  consumer depends on a declared version/profile, not a directory layout.
- A consumer reports an unmet need to the owner. The owner may extend, compose,
  factor or version its interface and publish evidence. Routine authorized changes
  do not acquire a new mandatory human approval flow.
- UI and server that ship together move as a matched release, as Unified's current
  policy requires. A separate repo does not itself create an independently released
  public API. Existing independent clients keep their explicit version contracts.
- Development and stable installations have distinct manifests and writable state
  roots. An experimental component or learned policy is evaluated as a candidate;
  observation alone cannot promote it into the stable path.
- Cutover changes one authoritative writer at a time. Rollback preserves all newer
  writes and uncertain effect receipts. Temporary bridges have named consumers,
  removal evidence and an owner; they are not a permanent alternative architecture.

**Proof:** compose published artifacts in a clean environment; substitute fixture
and real peers; fail an incompatible profile before work admission; route rollback
after new writes; demonstrate unchanged user journey after a component replacement.

## Intentional amendments to the existing Unified packet

1. A persistent named agent is a collaboration identity above native execution
   sessions; existing native session semantics and locks remain intact.
2. Multi-human projects add membership, provenance and conflict policy. The current
   process-owner credential is not evidence those semantics already exist.
3. Converge is an alternative ACP engine for new work, with its own capability profile.
4. One collaboration work record owns desired outcomes; runtime evidence and
   read models do not become competing work authorities.
5. Stable experience reuse and gradual replacement now guide the transition.
   Legacy native internals need not constrain new engine internals indefinitely.

These amendments require contract/fixture changes with their implementation. This
packet records the design proposal; it does not silently ratify or edit upstream
contracts, perform a live cutover, or claim full protocol compatibility.
