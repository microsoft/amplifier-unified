# Quiet observation v1

This is an opt-in contract. Ordinary prompt schedules are unchanged. Unsupported hosts must refuse the quiet watch, without falling back to a prompt schedule. Existing watches are never migrated or rearmed.

## Shared actions and authority

- `observation.qualify`: trusted operator/UI action only; unavailable through the agent bridge. It records an explicit source-review qualification for one already installed, connected MCP tool. `readOnlyHint` alone is insufficient. Qualification is not a sandbox or proof of remote implementation behavior.
- `observation.observers`: list qualified descriptors and current eligibility, without connecting or preparing a provider.
- `observation.preview`: review `{sessionId, observerId, target, scope, sourceId, args, intervalSeconds, durationSeconds, readTimeoutSeconds}` against persisted task authority. `sourceId` is the exact source URI derived by the extension from the target. The host treats it as an identity, never fetches it.
- `observation.create`: those same fields plus `{requestId, sourceMessageId, previewHash}`. Agent calls must cite the actual human request. Stable `requestId` is mandatory. The host checks an existing request and its exact original intent **before** preview validation, so a retry returns the original paused/expired/finished watch without rearming it. `durationSeconds` becomes an absolute expiry only on first creation.
- `observation.request`: `{sessionId,requestId}` returns the prior creation receipt without replay.
- `observation.list`, `observation.read`, `observation.report`: scoped watch/history reads; `report` is read-only, never a source-authored status submission.
- `observation.pause`, `observation.cancel`: `{sessionId,id,expectedRevision,requestId}`. They suppress checks and unadmitted handoffs, not watched product work.
- `observation.resume`: fresh preview and renewed human authority, with exact revision/request identity. Unknown admission or a terminal result cannot be resumed; no replay or terminal deduplication reset.

The bridge supplies caller origin; an `origin` value in tool arguments cannot authorize qualification. Source observations and scheduled/background messages cannot authorize watches. A source callback receives no action dispatcher, provider executor or presentation capability.

## Qualified descriptor

A qualification binds `installationId`, `connectionId`, `toolName`, exact input/output schema hashes, connection configuration fingerprint, account binding, `implementationRevision`, `requestArgument` (normally `observation`), target/scope schemas, and reviewed installed-file digests. The operator records `source-enforced-read` or `reviewed-trusted-read` with immutable review evidence, provider-free and concurrent-read-safe assertions. The source must enforce exact record ownership/scope and perform no product mutation. Stdio retains its OS process privileges. Remote read scope must be enforced by the remote service.

V1 supports only the already connected registration (`connectionPolicy: existing-only`). No installation, login, account acceptance, reconnect, source change or grant expansion is inferred. Missing/changed qualification requires review. Account identity is either verified connector identity or an explicitly qualified local process/configuration; unknown remote identity is not upgraded to verified identity.

`implementationRevision` is the qualified code/contract revision. It is **not** the product observation revision, which changes as recorded state changes.

## Wire envelope

The one approved MCP tool accepts a JSON object argument named by `requestArgument`:

```json
{"contract":"amplifier.observation.v1","watchId":"...","watchRevision":1,"occurrenceId":"...","target":{},"scope":{},"cursor":null}
```

Other arguments are fixed by the reviewed watch. The host supplies occurrence/cursor fields; caller arguments cannot replace them.

The tool returns `structuredContent` containing:

```json
{"contract":"amplifier.observation.v1","status":"pending","target":{},"source":{"id":"opaque exact record URI","revision":"semantic state digest"},"observedAt":1800000000,"semanticKey":"stable domain result key","summary":"bounded safe text","evidence":[{"uri":"immutable evidence identity","revision":"exact revision","digest":"sha256"}],"cursor":null}
```

`status` is `pending`, `actionable`, or `observation_failed`. Target must exactly echo the reviewed target and `source.id` must match the bound source. `source.revision` and `semanticKey` may be null **only** for `observation_failed`; no fresh-state claim is inferred from failure. Actionable results require exact evidence references. Revision/semantic keys must exclude observation time, heartbeat and transport IDs. Recorded pending/terminal state does not prove operating-system process liveness. Unknown state must be reported as failure, never pending/success.

## Outcomes and limits

Pending writes only bounded observation audit/cursor state. It does not load history, warm a worker/provider, send input, alter chat status/unread/name, notify, speak or select/open Canvas.

Actionable, genuine observation failure and expiry atomically stop further checks and create one durable handoff. At safe admission a finite, tool-denied background input asks for an explanation or necessary decision. It is machine evidence, not human permission. Expiry says watching ended; it does not declare product failure or cancel work. Without an explicit presentation grant, selection/drafts remain unchanged. No voice call is created. The optional exact browser presentation below is independent of model tools.

Durable result tombstones survive bounded audit pruning. Unknown worker admission is retained for review and never automatically retried. This guarantees one logical handoff, not exactly one provider execution after an ambiguous crash. Qualification/source changes, user corrections/stops, execution changes and account/scope changes invalidate authority; queued and returned reads are checked again.

Host synthetic acceptance and extension read-scope/customer acceptance are separate gates. A host mock cannot prove a remote tool is read-only.

## Action results (v1)

All shared dispatch calls use Unified's normal `{accepted:true,result:...}` envelope. The fields below are inside `result`:

| Action | Result |
| --- | --- |
| qualify | `{observer: QualifiedDescriptor}` |
| observers | `{observers: [{...QualifiedDescriptor,eligible:boolean,reason:string|null}]}` |
| preview | `{previewHash,binding,handoff,expiresInSeconds}`; binding includes exact task/native session/stop/execution revisions plus the original configuration |
| create/resume/pause/cancel | `{watch: Watch,duplicate:boolean}` |
| request | `{watch: Watch|null}` |
| list | `{watches: Watch[]}` |
| read/report | `{watch: Watch,runs: ReadOccurrence[],handoffs: Handoff[]}` |

A Watch contains `id`, `requestId` (original creation), `revision`, `sessionId`, original configuration, `authorization`, `taskId`, `taskRevision`, `nativeSessionId`, `interruptionRevision`, `executionRevision`, `status`, `createdAt`, `updatedAt`, `nextDue`, `expiresAt`, `sequence`, `cursor`, and optional `lastResult`, `terminal`, `reason`. Status is `active`, `paused`, `cancelled`, `needs_review`, or `ended`. Read occurrences include `id`, `watchId`, `sessionId`, `watchRevision`, `phase`, timestamps and optional validated `result`. Handoffs contain stable `id`/`inputId`, `watchId`, `phase`, exact `outcome`, and admission receipt where known. Phase is `pending`, `waiting_worker`, `submitting`, `accepted`, `suppressed`, or `unknown`; `accepted` is not proof of a completed provider response.

Qualification and first watch creation are distinct. Qualified descriptors do not grant authority to watch an arbitrary user's target. The source must validate exact scope and owner bindings on every read; target/source schemas and the human watch review further narrow host dispatch.

## Optional exact browser presentation

`presentationRequest` is optional on preview/create/resume; omitted or null means no presentation grant. A non-null request requires the actual **latest human** `sourceMessageId` in both preview and create. The two supported review choices are:

```json
{"kind":"browser","urlOrigins":["https://exact.example:8443"]}
```

```json
{"kind":"browser","urlPolicy":"loopback-with-explicit-port"}
```

Origins are exact, without wildcards, paths, credentials or query/fragment. The second policy permits only `localhost`, `127.0.0.1` or `[::1]` with an explicit port, so a newly built local result need not invent its port at arming. It does not authorize a private-LAN class or arbitrary hostnames. This policy appears in the reviewed binding. Exact retries preserve the original request and cannot add or widen the grant.

An actionable source result may carry this inert candidate (or `presentation:null`):

```json
{"presentation":{"kind":"browser","url":"http://localhost:8765/result","title":"Accepted result","target":{},"evidence":{"uri":"immutable selected record","revision":"selected revision","digest":"sha256"}}}
```

Candidate target must equal the watch target. Candidate evidence must exactly match an item in the accepted result's evidence. The source must identify its exact selected, accepted and still available result; emitting a URL is not authority to open it.

The host privately captures `presentationGrant` from trusted accepted input-to-client provenance, matching `sourceMessageId`. Agent-supplied client IDs cannot mint it. The grant binds the original session/client, actual connection generation, monotonic selection revision, human input identity/digest, exact policy and watch expiry. A client disconnect/reconnect or leaving and returning to the original selection does not revive it. A pause, cancellation, expiry, changed task/account/source or dirty Canvas prevents opening.

Immediately before handback, the host rereads **the same qualified observer**, with the same exact target/scope. Status, target, source semantic revision, semantic key and candidate must remain identical; selected candidate evidence must still be present. Other evidence entries may refresh without invalidating unchanged selected proof. The host then rechecks authority and client selection under the action lock and uses the existing Canvas URL/dirty-view policies. It does not fetch the URL or follow redirects. Browser embedding limitations still apply. No general tools, renderer resolver, app-control grant or model-authored instruction is added.

A durable presentation claim precedes the Canvas side effect. The handoff stores `presentationPhase` (`opened`, `skipped`, or `unknown`; transient `claimed`) and optional `canvasId`. A crash after claim is unknown and never replayed. The finite explanation receives a host-owned `presentationReceipt` containing status and exact reference (plus Canvas identity if opened); it cannot honestly infer successful opening from the candidate alone. Even a confirmed Canvas creation does not prove browser render completion.

## Recovery and qualification scope

A process restart repeats only an interrupted **qualified read**, preserving its occurrence ID. Already claimed/unknown handoffs or presentations are not replayed. If the host has no known worker after restart, it retains a visible `waiting_worker` result until the user explicitly resumes that chat; it does not warm a provider merely to wait. An available terminal handoff is finite and cannot borrow still-running delegated jobs; all temporary tool/goal/iteration restrictions are restored on idle, error, detach and worker shutdown, and are not saved as future user limits.

Host qualification uses synthetic registered observers through actual MCP stdio and OAuth HTTP transports, actual Core hooks and loop-live inbox admission, with fake model execution. It is separate from the extension's source-enforced read scope and cross-project customer acceptance. No production watch, live provider response or browser render is claimed by these tests.
