# Amplifier Unified workspace capability

Workspace placement and shared registration are owned independently of clients,
agent runtimes, native history, and the repairable session catalog. This optional
owner supports existing-directory discovery, bounded workspace/session pages,
create, attach, display-name changes, and registration removal through the same
advertised actions for users and agents. No action deletes, moves, or overwrites
an existing directory or chat history. No read or mutation starts an agent.

The client owns selection, drafts, picker state, and navigation. A workspace may
have no loaded sessions. Removing the final registration is permitted; creation
and attachment remain available from the empty view. These replace the legacy
shared selectedWorkspaceId/settings.workspace and keep-at-least-one constraint.

## Packages and configuration

- Node: `@amplifier/unified-workspace-capability`, `createWorkspaceCapabilities`.
- Python: `amplifier-unified-workspaces`, executable
  `amplifier-unified-workspaces --config /absolute/owner.json`.
- Catalog: public long-lived `amplifier-session-catalog serve` with the workspace
  projection API. The Node host's `StdioCatalog` exports the required methods.
- Python owner requires POSIX directory handles and an exclusive process lease.
  The only Foundation dependency is the optional, native-independent `amplifier-operations` intake fence. There are no Core, AppService, or transcript imports.

```json
{
  "stateDirectory": "/private/unified/workspaces",
  "allowedRoots": ["/home/user/projects"],
  "defaultRoot": "/home/user/projects"
}
```

Both owner roots and host allowedWorkspaceRoots must permit the intended paths.
Paths are canonical, absolute, and checked against configured roots. The default
root may be absent; prepared creation safely allocates missing parents. Caller
arguments cannot change configured authority or select another owner database.

```js
const workspaces = createWorkspaceCapabilities({
  owner: {command: '/venv/bin/amplifier-unified-workspaces', args: ['--config', config]},
  catalog, // the same public StdioCatalog used by the AHP host
  onInvalidate: (topic, scope) => host.invalidateCapability(topic, scope),
  onMayBeIdle: () => coordinator.mayBeIdle(), // optional event-driven retry signal
});
// Compose manifest/read/action/actionSchemas with other optional capabilities.
// Close workspaces before closing the shared catalog.
```

## Public contract

The negotiated `workspaces` host topic uses standard resourceRead/resourceWatch:
`amplifier-capability://workspaces/workspaces`. A read returns
`{topic:'workspaces',scope:'host',revision,data:{workspaces:{items,nextCursor?,coverage,defaultRoot,configRevision,creationSupported}}}`.
Topic revisions are safe integers. Each workspace is
`{id,name,path,available,availability,registered,hidden,revision,source,checkedAt}`;
IDs are `workspace:` plus SHA256 of the canonical path. Source is `registered` or
`discovered`; availability is `present`, `missing`, or `unknown`.

Actions use negotiated `x-amplifier/capabilityAction`, version 1, topic workspaces,
root channel (or the authenticated native caller's session), explicit commandId,
and the schemas returned by actionSchemas:

| Operation | Arguments | Result |
| --- | --- | --- |
| workspace.list | query?, cursor?, limit<=50, includeHidden?, includeUnavailable? | items, nextCursor?, coverage |
| workspace.inspect | id | current indexed workspace and explicit directory inspection |
| workspace.prepare | name, root? | planId, name, path, root, configRevision, disposition, workspaceId? |
| workspace.create | planId | created workspace and durable receipt |
| workspace.add | absolute path, name? | registered existing directory and receipt |
| workspace.rename | id, name, expectedRevision | display-name-only update and receipt |
| workspace.remove | id, expectedRevision | hidden registration and receipt |
| workspace.sessions | id, query?, cursor?, limit<=50, parentUri? | standard AHP SessionSummary page |
| workspace.receipt | commandId | exact saved receipt; unknown allocation includes present inspection |

Default list visibility is existing directories and non-hidden registrations;
filtering happens in SQL before keyset pagination. Both name and full path can be
searched. Cursors are connection/query-bound and expire. Concurrent label changes
can reorder later pages; restart paging for a newly sorted view. Sessions default
to root sessions; child pages require an explicit parent URI. Session summaries
omit native storage paths and IDs, and explicitly label runtime status unverified.
A missing directory is an availability fact, not deletion of its registration or
native data. Background catalog rechecks refresh availability; list queries do
not stat every directory or read native transcript/event bodies.

## Durable effects and recovery

Creation is a two-step prepare/create contract. Plans expire after 24 hours and
bind the configured root, normalized slug, collision result, and nearest existing
directory's device/inode. Create reserves its command and plan before mkdir,
walks pinned directory handles without following symlinks, and verifies the
created directory identity. Existing paths require explicit attachment; a second
plan for the same path never adopts another creator's directory implicitly.

The owner database is authoritative for registrations, plans, and receipts. A
process lease excludes concurrent owners of the same database. A command reused
with different arguments is rejected. Confirmed duplicate commands return their
saved result without repeating an effect. An interrupted process converts pending
admission to unknown, preserves partial directories, and never retries mkdir.
Receipt inspection is read-only and is not proof that an unknown directory was
created by this command. A user may inspect and explicitly attach that directory
with a new command. A new command on the same unresolved plan is also refused.

Mutation results disclose `filesDeleted:false` and `historyPreserved:true`.
Known pre-effect refusals return `accepted:false` with `executed:false` and a
rejected owner receipt. Unexpected failures after reservation and transport loss
remain unknown. Outer host receipt recovery and owner workspace.receipt are
separate: a completed owner command can repair a lost catalog projection without
repeating its filesystem effect.

The catalog stores only a derived label/visibility projection. Checkpointed
batches of at most 100 current registrations repair a lost catalog from the
owner. A catalog scanner or native restore cannot clear a user's hidden workspace
registration. Explicit attach restores discovery. This is visibility, not access
revocation: existing authorized session links and retained history remain usable.

## Held maintenance boundary

Register `workspaces.quiescenceParticipant` as required owner `workspaces`, and
map the `workspaces` capability topic to that owner in host coverage. Forward its
explicit `quiescenceAccess` map through composition: list, inspect, sessions and
receipt are passive reads; every other action remains ordinary mutation admission.
`onMayBeIdle` is a signal to the coordinator to recheck eligibility, not proof of
quiescence. It fires when Node requests/reverse callbacks and Python work settle.

The Node participant reserves intake before lazy process startup and counts the
entire request plus reverse catalog callback lifetime. The Python owner acquires
its existing OS process lease before opening the durable Foundation intake ledger.
It counts requests before lock waits, prepared mkdir intents, registration writes,
and the final catalog synchronization. A queued request or delayed catalog write
refuses acquisition. Only a confirmed private `quiescence/acquire` response creates
a held participant lease; there is no idle sampling or dormant agent startup.

Private RPCs `quiescence/inspect`, `quiescence/acquire` and `quiescence/release`
are coordinator-only. Context is `{fenceId,commandId,purpose,instanceId,dataScope}`.
Release requires the exact host-verified outcome and proof, or the narrowly scoped
admission-refused proof. Unknown release retains intake. The Foundation ledger
persists exact release receipts, so lost acknowledgements and process replacement
can reconcile without repeating mutations. The private methods are not advertised
as browser/agent actions. `inspectQuiescence()` exposes bounded coordinator status.

While held, ordinary list/inspect/session reads do **not** repair the catalog.
They return its current projection; `coverage.projection` (or selected inspection
`projection`) declares `held`, `complete`, and `repairDeferred`. Release permits
future normal reads to repair the projection. Exact command receipts remain
passive. An unknown mkdir result retained after a dead process is historical
uncertainty, not running work: it remains unknown and can never be replayed.
An active durable maintenance fence survives that restart and still blocks writes.

## Remaining compatibility boundaries

- The previous `locations.list` directory/file picker and standalone
  `locations.create` are not implemented here. Existing-folder attachment uses an
  explicit absolute path; name-based workspace creation is supported.
- The default root is trusted launcher configuration, with an authorized per-plan
  override. A durable user-editable default-root action is not implemented.
  Path display preferences belong to client-local storage.
- Workspace session pages do not yet expose separate active/all/archived filters.
  The catalog's current `includeArchive` also changes missing/hidden workspace
  inclusion, so forwarding it would conflate independent visibility choices.
  Host-owned archive actions and history-preserving workspace removal remain
  separate; this component does not declare archive-browsing parity.

## Validation

```sh
uv sync --project python --extra test
python/.venv/bin/python -m pytest python/tests
npm ci
npm test
```

For installed cross-package acceptance set WORKSPACE_OWNER_PYTHON to a fresh
Python environment containing the built owner wheel, CATALOG_EXECUTABLE to the
installed catalog executable, and HOST_MODULE to the installed host dist/index.js.
`npm test` then requires the public installed composition case instead of skipping
it. Tests cover crash-after-mkdir recovery, name races, stale revision/config,
symlink/outside-root refusal, lease exclusion, held catalog/mkdir lifetimes, passive held reads, exact release acknowledgement loss and restart reconciliation, display-only rename, removed history
preservation, bounded projection rebuild, root/child pages, and zero agent starts.
Browser interaction is qualified by the independently owned web-client lane;
these package tests do not claim browser, device, or production deployment.
