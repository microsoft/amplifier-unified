# Native bundle receipt composition

The root factory is `createDistribution`. It composes the public native bridge
with the configured native administration engine; Core/Foundation and native
receipt storage remain `amplifier-app-acp` responsibilities. This implements the
existing AP4/HP6/CS4 exact-outcome boundary, not another host result store.

With bridge `d0b5f2e` and native `4d2ea06`, root composition supplies the asynchronous
`nativeBundleCommands` resolver from actual engine initialization and awaits
`negotiateBundleCommands()` before `composeCapabilities` snapshots manifests.
The native `bundleCommands` v1 declaration, launcher-enabled administration and
passive receipt callback are prerequisites. Older peers leave `bundle.receipt`
unadvertised; configured names and static version assertions cannot prove support.

The `nativeBundleReceipt` callback receives only an admitted AHP conversation and
child identity. It uses public `host.inspectSession`, requires the exact configured
native administration engine and an existing native identity, and calls
`admin.readBundleReceipt` on that engine's separate passive administration
transport. `workingDirectory` is immutable canonical history authority;
`executionDirectory` may change through relocation and must never replace it.
Client-supplied native IDs, cwd/home selectors, active `nativeControl`, new-session
creation or worker startup cannot serve as recovery shortcuts.

The bridge derives distinct stable save/activation child IDs from the original
outer command, operation and authenticated scope. Public `bundle.receipt` takes
`{operation,commandId}` referring to that original outer command, and returns
native DTOs/null in `result.receipts.command` and, for selected saves,
`result.receipts.activation`. Unknown native effects remain unknown and never
reexecute. A succeeded activation may be an exact confirmed unapplied
`requiresModelChoice:true,applied:false` preview. Receipt reads return no topic
updates or invalidations. Host command status/detail does not contain those native
results and cannot replace them.

Selected-session save and activation lookup preserves the original canonical
workspace after root/native restart, relocation or a new current default. Host-
scoped administration has no selected-session workspace binding: the consumer
must retain and reauthorize its original admitted workspace. A null receipt under
a changed current default is honest absence in that scope, not proof of the
original effect. Recovery after such a default change without the original
binding remains unsupported; root never searches other scopes or repeats work.

[Actual factory acceptance](../test/bundle-receipts.integration.test.mjs) uses
installed bridge/native packages and a real authenticated gateway/WebSocket hop.
It drops the completed outer response before delivery to the client, reconnects,
relocates between owned Git worktrees, then restarts root and native transports
with worker creation prohibited. Exact save/activation results survive through
the original native/history identity, with zero active agents on cold recovery
and zero provider calls. A foreign configured engine refuses; an original host-
scoped default receipt becomes null after changing the default workspace; the
older native peer refuses recovery before a worker. The root baseline without
this wiring fails negotiation advertisement before bundle effects.

This is focused local factory/protocol acceptance with configured native/resources
owners and the sealed distribution component graph. Browser rendering, real
accounts/devices, Linux execution, all-owner runtime composition and deployed
adoption remain separate. Native transport reply-loss/model-choice and durable
store/backup cases retain their separate native/bridge qualification packets.
No merge, release or service activation is implied by this contract.
