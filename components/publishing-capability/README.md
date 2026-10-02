# Unified publishing capability

Product composition over the independent `amplifier-publishing` package. No
AppService, global state object, native engine or sibling source import is used.
The Python owner retains the existing target selection, exact RPC proof and
immutable capture contracts. The TypeScript provider speaks a private bounded
stdio protocol and exports the normal Unified capability interface.

```ts
const publishing = createPublishingCapabilities({
  owner: {command: '/owned/venv/bin/amplifier-unified-publishing',
          args: ['--config', '/owned/publishing.json']},
  inspectSession: (uri, context) => host.inspectSession(uri, context),
  withSessionWorkspace: (uri, expected, callback) =>
    host.withSessionWorkspace(uri, expected, callback),
  // Optional genuine human permission flow for agent-origin deploy/rollback:
  authorizePublication: async request => ({approved: true, approvalId: exactApproval}),
  onInvalidate: (topic, session) => invalidate(topic, session),
});
```

The approval example assumes `exactApproval` comes from the application's real
permission system for the supplied exact request. Omitting the callback refuses
agent deploy/rollback. Direct authenticated UI deploy/rollback is explicit
approval. A release review records the user's note; it never fabricates visual
inspection or publication approval. Approval evidence is durably bound to the
operation and exact arguments before effects; changed retries conflict.

Python configuration is `{"dataDir":"/owned/publishing"}`. One owner process
holds an exclusive lease; its SQLite admission database uses WAL/FULL commits.
No model, source builder, installation, public network route, authentication or
TLS service is created. Preview/deploy listeners retain the library's separate
origin and actual loopback/private access policy. SSH remains the account's
existing explicit configuration; the owner does not store SSH credentials.

## Public provider contract

Exports `createPublishingCapabilities`, `PublishingCapabilities`, `OwnerConnection`,
`Options`, `Launcher`, and `Context`. Methods are `manifest`, `actionSchemas()`,
`read`, `action`, `close`. Schema loading starts only the lightweight owner; it
does not instantiate Publisher, discover projects or connect to a remote service.
`read` is scoped to `amplifier-capability://publishing/publishing` and returns
`{topic:'publishing',scope:<AHP session URI>,revision:<number>,data:{publishing:{[uri]:snapshot}}}`.
The small first snapshot has ten rows per collection. Mutations return their
original exact result and invalidate the selected topic. They do not append a
second remote read that could turn a confirmed mutation into a misleading error.

Trusted `inspectSession` supplies current `executionDirectory`. For build, the
Node provider holds `withSessionWorkspace` while the owner captures existing
static output and optionally transfers its immutable bytes. The host mapping
cannot relocate or admit a turn during that callback; independent sessions keep
running. External filesystem writers are not frozen. Source paths remain relative,
inside the authoritative workspace and free of symlink traversal. The library
also verifies no-follow reads, size bounds, immutable hashes and manifest integrity.
Exact retries use their saved original source/target even after workspace changes.

All original names and argument contracts remain:

- `publishing.list`, `status`, `logs`, `build`, `preview`, `review`, `deploy`,
  `rollback`, `stop`, `remove`.
- `publishing.target.list`, `save`, `inspect`, `select`, `remove`.

Build returns a full immutable release. Other lifecycle mutations return the
original receipt shape with `state: running|unknown|succeeded|failed`.
Remote mutations require the inspected `targetRevision` and `serviceId`; selecting
or editing another target cannot retarget an admitted request. AHP URIs are mapped
durably to opaque plain IDs for the library and remote protocol and projected
back only in `sessionId` fields. Existing legacy session IDs require an explicit
migration binding; this owner never guesses or scans the former application DB.

Bounded read additions:

- `publishing.list {collection?:'sites'|'releases'|'receipts',limit?:1..100,cursor?,siteId?,targetId?}`.
  Without collection: original `{sites,releases,receipts,target,targets,...}` plus
  `pages:{sites:{nextCursor},releases:{nextCursor},receipts:{nextCursor}}`.
  With collection: `{items,nextCursor,collection,target}`.
- `publishing.logs {siteId,limit?,cursor?,targetId?}` retains `items` and adds cursor.
- `publishing.release {releaseId,targetId?}` returns the complete selected manifest.
- `publishing.receipt {requestId,targetId?}` returns exactly one receipt or null,
  retaining unknown admissions and reconciling remote proof without repeating work.

Pages use stable lexical identifier order, not chronological offsets. Cursors are
query scoped; old cursors cannot select another session or collection. Release
metadata pages use `fileCount`; `files` are hydrated only on selected release read.
Receipts omit embedded manifests in page projections. Target configuration lists
remain complete and bounded to 64 retained configurations per session. Capture
store handles are capped at four; old capture directories remain durable on disk.
Unknown remote outcomes reconcile only exact RPC payload digest plus service ID;
loss or malformed evidence never triggers mutation replay. Error data preserves
`code`, `receipt`, and HTTP-like status for the legacy UI's explicit retry controls.

## Qualification and remaining boundaries

`npm test` builds and runs Node → installed Python owner → actual filesystem and
loopback listener. `cd python && uv run pytest -q` exercises workspace rejection,
review/approval, deploy/update/rollback, exact retries after source removal,
unknown admission, pagination and real Unix-socket service transport with an
injected SSH transport boundary. The public publishing wheel is installed as a
consumer dependency, not imported from sibling source.

Real SSH account access, deployed site/browser acceptance, legacy publishing DB
migration and genuine host permission UI integration are separate gates. No live
site, Spark environment or external account was modified during qualification.
Old remote services without negotiated pagination version 1 explicitly refuse
bounded browsing; mutation and direct receipt contracts remain compatible.

Target configuration actions also journal their outer `commandId` before any
mutation or remote inspection. `publishing.command {commandId}` returns
`{commandId,sessionId,operation,state,result,error}` or null. States are
`running|unknown|succeeded|failed`. Exact retries use the saved result; changed
arguments or actor conflict. An unfinished command becomes unknown on restart
and is never repeated. The client must retain its exact outer command locally
and inspect this receipt after loss, rather than infer success from today's
registry. Lifecycle requests continue to use `publishing.receipt {requestId}`.
