# Optional native runtime maintenance capability

`@amplifier/unified-maintenance-capability` adapts launcher-authorized native ACP
administration to the public Unified capability interface. It has no native
Python or host-private imports and does not enumerate sessions or start providers
on construction, action discovery, or passive reads.

```ts
const maintenance = createMaintenanceCapabilities({
  nativeAdmin: (operation, args, context) => admin.perform(operation, args, context),
  bundleReferences: (await admin.maintenanceCapabilities())?.bundleReferences,
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

The exact bridge preflight proof `{reason:'native-generations-unavailable',
executed:false,replayed:false}` before any preparation result produces an
`accepted:false` action result with the original `commandId`, `operation`,
`state:'failed'`, `applied:false`, `executed:false`, `replayed:false`, the same
reason, and a plain-language `message`. The host can retain a failed receipt
instead of an unknown effect. Clients should settle only that exact refused
request and keep technical identifiers in optional details. This does not grant
maintenance or rewrite historical unknown receipts. Other errors remain errors;
even this proof after successful preparation cannot classify the whole install
as unexecuted. A failed passive receipt read never settles the original request.

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

An optional negotiated `bundleReferences` capability adds
`maintenance.bundleReferences.preview`, `.page`, `.apply` and `.receipt`.
These are explicit saved-chat compatibility actions. Preview scans native
metadata; apply requires its exact hash and a durable commandId, forwards to the
native metadata owner and retains the original receipt. The public command is
namespaced as `bundle-references:<commandId>` in native maintenance. Receipt
reads never repeat apply. The UI privately retains that identity before dispatch
and recovers it after reload or a lost response. The owner requires authenticated
maintenance authority and a UI caller because this operation spans saved chats.
No worker or provider is started; custom, changed and busy references remain
unchanged and reported. Without the negotiated capability these actions are not
advertised. Full-native maintenance and cooperative/stopped writer policy are
independent launcher grants; generation authority alone does not enable repair.

This package manages native runtime generations and this optional saved-reference
repair. Distribution
self-update, restart/adoption health, automatic preferences, feature installation,
full backups, destructive reset and retention are not advertised. The
native repository's `docs/maintenance-parity.md` is the detailed feature ledger.
The familiar `updates` and `maintenance` data fields are projected to existing UI;
that does not establish browser acceptance or full legacy maintenance parity.

Validation: `npm test` includes a real upstream ACP SDK consumer of the independent
native admin subprocess (passive inspection, explicit check, exact receipt), plus
known-only promotion and authorization tests. `NATIVE_ACP_PYTHON` may point to an
independently installed native wheel environment. No model/account calls occur.
`test/preflight.test.mjs` also accepts `MAINTENANCE_HOST_ENTRY` and
`MAINTENANCE_BRIDGE_ENTRY` as absolute installed public entrypoint paths. With
those and `NATIVE_ACP_PYTHON`, it exercises the real disabled-grant peer, public
AHP action and exact host receipt across restart, without starting a worker.
`MAINTENANCE_ENTRY` can select an installed maintenance archive for the same test.
