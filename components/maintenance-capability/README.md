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

The native owner preserves immutable qualified worker graphs, source revision and
local-change guards, native leases, lazy selected configuration qualification,
paged source summaries, sanitized diagnostics and exact receipts. Running worker
currency is reported as unavailable if the host does not supply its callback.

This package currently manages **native runtime generations only**. Distribution
self-update, restart/adoption health, automatic preferences, feature installation,
full backups, destructive reset, repair and retention are not advertised. The
native repository's `docs/maintenance-parity.md` is the detailed feature ledger.
The familiar `updates` and `maintenance` data fields are projected to existing UI;
that does not establish browser acceptance or full legacy maintenance parity.

Validation: `npm test` includes a real upstream ACP SDK consumer of the independent
native admin subprocess (passive inspection, explicit check, exact receipt), plus
known-only promotion and authorization tests. `NATIVE_ACP_PYTHON` may point to an
independently installed native wheel environment. No model/account calls occur.
