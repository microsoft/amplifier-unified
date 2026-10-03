# Amplifier Unified distribution

This package composes independently packaged clients, host and capability owners.
The browser speaks AHP to the gateway; the host speaks ACP to configured agents.
The full native Amplifier runtime runs in `amplifier-app-acp`, using Core and
Foundation. This distribution never imports another repository's private sources.

This is an integration candidate. It is not yet a production replacement for the
complete existing application. The family vision and contracts are in the parent
repository; capability and acceptance receipts distinguish implemented behavior
from remaining migration and interoperability work.

## Run an owned local instance

Requires Node 22.16 or later and independently installed agent/catalog executables.
Install the static `@amplifier/unified-client-web` release and obtain its directory:

```js
import {webDirectory} from '@amplifier/unified-client-web';
import {createDistribution} from '@amplifier/unified';
const app = await createDistribution({...configuration, webDirectory});
console.log(app.url);
// On shutdown:
await app.close();
```

The CLI also accepts the same configuration as JSON:

```sh
amplifier-unified --config /absolute/owned/distribution.json
```

Adapt `examples/distribution.json` to owned existing workspace directories and
installed executable paths. The native adapter configuration is separate; its
`home` owns native history and its `appHome` owns native application settings.
Use a separate state directory for each development instance. Historical
`transcript.jsonl`, `metadata.json` and `events.jsonl` remain with their native
owner. Rebuildable catalog/host/resource records do not replace native history.

## Signed supervisor launch

The JSON launcher can bind the real application to the independent supervisor:

```json
{
  "supervision": {
    "trustedKeys": {"publisher-key-id": "-----BEGIN PUBLIC KEY-----\n..."},
    "discoveryFile": "/owned/control/supervisor.json",
    "hostControl": {
      "discoveryFile": "/owned/control/host.json",
      "tokenFile": "/owned/control/host-token"
    }
  }
}
```

This is an addition to the ordinary workspace, client, agent and owner
configuration. Publisher keys are configured independently of the launch receipt.
The supervisor supplies its signed receipt, fresh process identity and data scope.
The CLI verifies the actual entrypoint and complete installed Node inventory,
initializes the configured owners, then exposes private authenticated host control.
The supervisor connection is lazy, so its discovery file may appear after initial
application readiness. Missing endpoints are unavailable; mutation requests are
never queued or replayed. Update checks/install preparation dispatch immediately;
replacement waits for actual owner admission and receives idle/progress events.

The launcher derives intake coverage from the configured owners. Portability closes
its native transfer processes before the one shared native administration gate.
With quiescence enabled, portability must select exactly the administration engine;
multiple independent native homes need separate admin coverage before they can be
qualified. A listening publication, unresolved external work or an unqualified owner
refuses restart. Replacement releases retained fences only through authenticated
supervisor proof and actual verified running identity. Readiness remains available
while intake is held so that this reconciliation is possible.

The JSON launcher serves a single local account on loopback. Explicit
`recovery: {authorization: "local-account"}` enables the reviewed recovery owner
for that account; agents remain limited to their own session by the recovery owner.
Credential export additionally requires `credentials: true` in that trusted
launcher configuration and the existing request review. Remote authentication and
custom permission policies use the public composition API.

This proves a directly owned child-process lifecycle. It does not provide
service-manager adoption or attest external Python installations and local operator
configuration. Those have separate qualification receipts.

## Composition and authorization

- The gateway serves one installed static client release and proxies `/ahp` to a
  private loopback host with a server-only credential. No credential is placed in
  the browser URL. A stable non-secret `account` scopes client persistence.
- Loopback is the default. A non-loopback gateway requires an injected
  `authorize(request)` returning the configured account, and the deployment must
  supply its TLS/authentication owner. The JSON CLI does not create an account
  service or infer authentication from request headers.
- The host owns session admission, identities, ACP connections and subscriptions.
  Provider execution continues independently of viewers. Unknown effects are
  retained for reconciliation, never replayed on reconnect.
- Resources, native administration, media and MCP are separate capability owners.
  Optional owners are enabled by explicit configuration. MCP connections and
  media devices are acquired only when requested; creating the distribution does
  not connect every configured peer or request device access.
- Browser drafts, UI selection and editing buffers belong to the client. Shared
  Canvas state is a scoped resource with conditional writes. Legacy client-state
  import reads one explicitly identified record, preserves its source, and maps
  only unambiguous native identities through the catalog.

`createDistribution(config, {authorize, capabilityOwners,
createCapabilityOwners})` accepts independent capability factories. Factories
receive selected-session public host callbacks and resource registration; they
receive no host store, native runtime object or global application snapshot.
The aggregate rejects overlapping capability names. Native agents discover the
same admitted shared actions through their negotiated host bridge. Private client
actions require an explicitly attached client and that client's advertised tool.

## Optional owners

`nativeAdmin: {engine: "amplifier"}` uses that engine's separate ACP administration
connection. The native launcher must authorize `adminWorkspaceRoots`; this does
not create a model session. No credentials appear in generic shared action schemas.

`workspaces: {python: "/installed/owners-env/bin/python", defaultRoot: "/owned/projects"}`
adds durable registration and name-based folder placement. It requires the shared
`catalogProcess`; both owners use public catalog requests. The default creation
root is the configured default workspace. Selection and editing remain private
to each client. Listing filters indexed existing directories before paging;
children require an explicit parent. Removal hides registration and ordinary
session discovery while preserving the directory and native history. A lost mkdir
reply is resolved by its exact receipt, never by repeating creation. The current
directory-handle and exclusive-owner implementation requires POSIX.

`media: {python: "/installed/media-env/bin/python"}` supplies voice/media lifecycle.
With native administration, private voice credential access additionally requires
`adminVoiceCredentials: true` in the native launcher. Without native administration,
the server uses `credentialEnvironment` (default `OPENAI_API_KEY`) and optional
`settings`. Device permission and capture remain in the originating client.
Native foreground capture requires separate explicit `enableNative: true`.

`mcp: {python: "/installed/mcp-env/bin/python"}` runs the independently installed
`amplifier_unified_mcp.server`. Its owned launch configuration receives the exact
gateway origin after binding. An explicit `broker` configuration instead delegates
launch configuration to that broker's owner. OAuth responses return through the
authenticated same-origin callback; saved MCP Apps retain their original resource
and grant authority without rerunning the launch tool.

`legacyClientState: {database: "/retained/legacy.sqlite", account: "local-user"}`
enables read-only migration. The account must exactly match the distribution.
Oversized, ambiguous or invalid legacy state is preserved, not silently truncated
or used to execute old device commands.

## Qualification

`npm test` covers separately packaged components and independent fixture peers.
Set `AMPLIFIER_ACP_PYTHON` to an installed native-adapter Python executable to run
the actual Core/Foundation/loop-live graph with an offline provider. Set
`MCP_BROKER_PYTHON` to an installed broker Python executable for its subprocess/MCP
Apps case. Missing executables skip those named cases; a run with skips is not
equivalent to the complete installed-component qualification.

The candidate vendor archives allow independent installation before package
publication. Their checksums and resolved dependency tree are qualification and
rollback receipts. Deployment composition must track latest qualified components;
these candidate archives are not a policy of permanent source pins.

Browser/terminal fixture checks, physical-device checks, vendor-account execution,
legacy state migration and actual Spark worker adoption are distinct acceptance
boundaries. Complete parity requires the remaining capability adapters and their
integration cases; packaging the existing UI alone does not establish parity.

`operations: {python: "/installed/owners-env/bin/python"}` enables the separately
installed scheduling, operation and question owner. If MCP is also configured,
quiet watches are advertised using that broker's qualified read seam. Watches
read persisted task state without preparing a model. A result is delivered only
through typed admission to an existing worker, with exact authority rechecked;
uncertain outcomes are retained without replay.
`schedule.request` reads the original scoped command result after a lost reply,
even if the schedule has since changed. A missing receipt is not permission to
submit the command again.

`recall: {python: "/installed/owners-env/bin/python"}` enables passive scoped
history indexing and opt-in memory. The distribution supplies bounded native
history reads and a turn-settled callback; contribution runs only for confirmed
human-origin turns. The owner never imports native execution. `worktrees` and
`publishing` take the same installed Python form and own separate private stores.
Publication from an agent requires the injected `authorizePublication` policy.

`maintenance: {}` adds native generation management when `nativeAdmin` is enabled.
The native launcher must explicitly enable `adminGenerations`. This host-scoped
topic applies equally to an authorized UI and its agent, independent of the
selected chat. An optional `authorizeMaintenance(context)` further restricts
that authenticated-account authority. Preparation has a 20-minute admin timeout
by default; `nativeAdmin.timeoutMs` overrides it. No application self-update,
automatic update preferences, backup, reset or running-worker currency is inferred
from successful native generation selection. Runtime currency is explicitly
unavailable until a qualified running-worker inventory is connected.

`updates.runtime.current` reads the bounded recorded installation and configured
source policy. `updates.runtime.worker` uses the public host's resident-only ACP
control to inspect one worker's actual interpreter and paged installed/mounted
sources. It never starts or resumes a worker to satisfy an inspection.
`updates.runtime.repair.preview` reviews retained native generation receipts;
`updates.runtime.repair` reconstructs a new qualified environment from their exact
hash. Activation remains a separate `updates.runtime.select` with pointer CAS.
Unknown repair outcomes are inspected by receipt without re-running work.

`applicationUpdates: {}` separately advertises distribution updates only when
`createDistribution` receives an `applicationUpdateSupervisor` that outlives the
application. Its bounded host-scoped facade exposes inspection, preferences,
check/install/rollback and exact receipt reconciliation. Closing this application
unsubscribes its facade; it does not close the external supervisor. Admission
means that a command receipt exists, not that a replacement is ready. The public
supervisor package requires trusted release qualification and continuously held
idle/intake admission; authenticated production transport and service-manager
integration have their own acceptance gates.

Legacy document migration is an explicit offline operator action through
`@amplifier/unified-resources-capability/migration`. It reads a bounded page from
one reviewed legacy session mapping, preserves original bytes, verifies document
versions and commits an entire artifact with a durable idempotence journal.
Conflicts and unsupported interactive apps remain classified and retained; this
does not run on host startup or import private editors into shared state.

Set `UNIFIED_OWNERS_PYTHON` for independently installed Foundation/product owners.
The complete graph additionally requires both native and MCP installed Pythons.
Its watch case uses an actual SDK stdio source and full Core/Foundation runtime,
with an offline model fixture; it does not call an external model account.

Attachment drafts remain in client storage. On explicit Send, the resources owner
accepts resumable bounded uploads and shares immutable URI references. Engines
default to `attachmentMode: "inline"` (up to 4 MiB per file, host aggregate limits
still apply). An explicitly co-located engine may select `"local-files"` to receive
standard ACP file links up to 32 MiB. That option requires the same filesystem;
it does not imply remote agent access or provider-specific image/PDF ingestion.

`portability: {python, engines: ["amplifier"], exchangeDir, stageDir}` enables the
independently installed paired-host transfer owner. Both directories are explicitly
owned; staging must be inside an authorized workspace root. Each allowed native
engine must enable transfer and point `transferAuthorityDirectory` at the same
`stateDirectory/capabilities/portability` journal, with compatible workspace roots.
The composition derives the host's transfer identity from that owner's public
identity before serving clients. No model session starts during this discovery.

Signed owner evidence is verified before private resource/operation staging.
Imports remain historical observations. Unsupported owner records and attachment
bodies produce explicit omissions requiring preview acceptance. Configured engine
IDs must match the signed session binding; no alternative engine is substituted.
Agent-origin transfer actions require an injected `authorizeTransfer` policy;
ordinary explicit UI actions retain the same command and receipt recovery path.

`feedback: {python: "/installed/owners-env/bin/python"}` enables explicit GitHub
feedback through the independently installed `amplifier-unified-feedback` owner.
Drafts, correction editors and file choices stay on each client. Explicit Send
uploads bounded immutable files and admits one durable submission; a lost remote
reply remains unknown and is never reposted. Shared state contains only bounded
receipt summaries. Selected reports, diagnostics and reviewed excerpts load on
demand. Agent-origin remote writes and excerpt disclosure require the injected
`authorizeFeedback` policy. Read-only reconciliation does not imply permission to
resend. This configuration does not submit any feedback by itself.

`coordination: {python: "/installed/owners-env/bin/python"}` adds bounded indexed
conversation pages, explicitly selected native worker pages, eight-target waits,
and durable follow-up/interruption receipts. Its independent Python package uses
Foundation's coordination library. Ordinary listing never hydrates a native
worker tree. Agent-origin mutations are restricted to the calling conversation
and its children; follow-ups retain agent provenance and cannot become human
Recall consent. Native worker changes and actual attention/lifecycle events wake
selected waits; streamed text tokens do not trigger task-state rereads.

When operations are configured, pending question IDs come directly from a covering
disk index. Passive native task inspection supplies task attention. Missing owners,
unsupported agents and truncated metadata remain explicitly incomplete; no empty
attention state is inferred. Neither inspection starts a native model worker.


## Composed restart and selected recovery

`quiescence: {instanceId, dataScope, timeoutMs?}` enables complete configured-owner
coverage derived from actual capability/resource owners. Callers do not supply
coverage assertions. Missing owner participation remains an explicit refusal.
One native admin participant is shared by native controls and runtime maintenance.
Each independent owner closes durable intake and accounts for queued callbacks,
background work and subprocesses until their real completion. History rows alone
are not active work. Local publishing listeners remain live responsibilities and
must be explicitly stopped; unqualified external MCP lifetimes refuse automatic
restart. A worker lost during an effect retains uncertainty rather than replaying.

The optional `onMayBeIdle` composition callback forwards advisory completion
signals to external host control. It requests a new admission check and never
proves idleness itself. `verifyQuiescenceRelease` must read authenticated external
supervisor evidence, including actual replacement identity. Caller assertions
cannot reopen intake. The stateless application-update facade owns only its local
forwarding lifetime; its supervisor survives application replacement.

`recovery: {nativeAuthority?, leaseSeconds?}` requires native administration,
quiescence, and injected `authorizeRecovery(context, operation, args)` returning
`{accountId}` for the configured account. This is explicit authorization for
sensitive content, separate from client review flags. The selected session's
immutable history directory is resolved through the host; an execution worktree
cannot redirect recovery. Both the host and native admin maintenance fence wrap
native snapshot/reset effects. The release verifier reads the owner's exact durable
native lease disposition. Bounded archive resources are authenticated per chunk.

Recovery covers selected native history/state/settings and reversible session
configuration reset. It does not yet replace full-product backup/archive restore,
cache reclamation, retention or canonical history deletion. See the independent
recovery component contract for included files, omissions and uncertain-outcome
rules. Its queued admission returns before background quiescence so the initiating
request cannot hold its own restart check busy.

## Optional shared push notifications

`notifications: {python?, command?, owner?, defaultServer?, legacySettingsPath?}`
composes the independently installed notifications owner. Ordinary completed turns
flow through its opt-in policy; schedules reach it only after Operations has made
an explicit `notificationDecision.notify === true` decision. It is a required
quiescence participant whenever configured. Disabled settings read no selected
session/result or credentials; preview defaults off. Network delivery is bounded,
exact events are deduplicated, and uncertain delivery is never retried.

The shared `notifications` topic provides redacted settings, revisioned save and
exact receipts. Native/browser agents and UI use the same advertised actions.
Desktop permission/preferences and unsent edits stay client-local. A client must
never persist topic/token bytes in its effect journal: keep only command identity
and fingerprint, then read the exact receipt after a lost save acknowledgement.
Optional legacy import reads only the explicitly configured old notifications file
on first creation, preserves it, and never triggers delivery. HTTPS success proves
server acceptance, not a physical device display.
