# Optional native runtime maintenance capability

`@amplifier/unified-maintenance-capability` adapts launcher-authorized native ACP
administration to the public Unified capability interface. It has no native
Python or host-private imports and does not enumerate sessions or start providers
on construction, action discovery, or passive reads.

```ts
const maintenance = createMaintenanceCapabilities({
  nativeAdmin: (operation, args, context) => admin.perform(operation, args, context),
  authorize: async context => authorizeHostMaintenance(context),
  inspectRuntimeCurrency: () => hostOwnedRuntimeCurrency(), // optional, bounded
  inspectResidentRuntime: (session, args) => host.nativeControlExisting(session, 'runtime.inspect', args),
  onInvalidate: (topic, scope) => invalidateCapability(topic, scope),
});
```

`admin` is a separately configured public `AdminConnection` from
`@amplifier/unified-native-capabilities`, or any compatible ACP client. Its
launcher configuration must explicitly enable `adminGenerations:true` and set
`adminWorkspaceRoots`; its trusted workspace resolver must use configured host
context. Configure its timeout for a complete qualification (for example 20
minutes). Execution agents independently opt into `managedGenerations:true`.
No request can supply a native home, workspace, interpreter, command or source
manifest authority. `authorize` is required and is invoked for every read/action.

The lazy `maintenance` topic uses scope `host`. Standard capability action envelopes
use `channel:'ahp-root://'`, `topic:'maintenance'`, version 1 and a durable commandId.
`actionSchemas()` returns `{[operation]:{description,schema}}` without native calls.

Actions are `updates.check`, `updates.install`, `updates.rollback`,
`updates.runtime.inspect` and `updates.runtime.receipt`. Install waits for an exact
successful native preparation before selecting its generation. A failure or
unknown preparation never causes promotion. The base command's `:prepare` and
`:promote` identities are durable; receipt inspection reads all three identities
without resubmitting them. Optional `checkId` and rollback `expectedCurrent` are
explicit CAS fields. Transport exceptions are never retried here.

`updates.runtime.current` reads bounded qualified-generation receipts and source
policy. `updates.runtime.worker` reads a selected resident worker's paged actual
interpreter/package/mount evidence through the supplied callback; it never starts
a worker. Absence or retirement is explicit, and agent callers cannot select a
different conversation. Neither receipt alone proves loaded-code equivalence.

`updates.runtime.repair.preview` binds the exact retained receipts to a source
hash. `updates.runtime.repair` requires that hash, generation and reviewed current
pointer and constructs a new qualified environment. It does not select it. A
separate `updates.runtime.select` action performs native generation promotion
with pointer CAS. Unknown repair receipts remain inspectable without replay.
Repair cannot restore missing source checkouts or change a running worker.

The native owner preserves immutable qualified worker graphs, source revision and
local-change guards, native leases, lazy selected configuration qualification,
paged source summaries, sanitized diagnostics and exact receipts. Running worker
currency is reported as unavailable if the host does not supply its callback.

This package currently manages **native runtime generations only**. Distribution
self-update, restart/adoption health, automatic preferences, feature installation,
full backups, destructive reset and retention are not advertised. The
native repository's `docs/maintenance-parity.md` is the detailed feature ledger.
The familiar `updates` and `maintenance` data fields are projected to existing UI;
that does not establish browser acceptance or full legacy maintenance parity.

Validation: `npm test` includes a real upstream ACP SDK consumer of the independent
native admin subprocess (passive inspection, explicit check, exact receipt), plus
known-only promotion and authorization tests. `NATIVE_ACP_PYTHON` may point to an
independently installed native wheel environment. No model/account calls occur.
