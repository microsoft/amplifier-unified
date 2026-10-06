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

The package also installs the independent `amplifier-ahp` command-line client:

```sh
amplifier-ahp run --server ws://127.0.0.1:PORT/ahp \
  --workspace /host/work/project --prompt-file request.txt --command first-input
amplifier-ahp inspect --server ws://127.0.0.1:PORT/ahp \
  --command first-input --output json
```

Use `--session ahp-session:/ID` with `run` to submit a new input to an existing
conversation. Host agent/model/bundle defaults remain in force. Remote connections
use the authentication owner's supported bearer token via `--token-file`; this
does not reuse browser cookies or the private supervisor control token. The
authentication owner must authorize that access. `--ca-file` supplies an explicitly
trusted CA without disabling certificate checks. Input journals stay on the client;
`inspect` and repeated command IDs never replay uncertain work. Approval requests
remain available in an interactive client. `--help` documents bounded waits,
machine-readable output, explicit private traces and exit statuses.

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

The native `app_control` state reader accepts `/session`, `/clients`, and a
single advertised capability topic such as `/canvas` (the equivalent bare names
are also accepted). Reads remain scoped to the invoking session and the owner's
authorization. Nested paths and unadvertised topics are rejected. `/session`
lists the available topics; `/clients` lists attached client tools. Invoke an
attached tool through the advertised `clients.invoke` action, with that client's
current revision. Creating a canvas artifact is separate from opening its panel;
the latter is confirmed by the selected client's action result.

Optional `diagnostics: {python: "/absolute/installed/python"}` composes the
independent diagnostic owner. Its initial policy is disabled. Explicit user
configuration enables bounded live records and selected destinations through the
Context Intelligence SDK. The owner persists policy, delivery status and exact
command receipts in its private directory. It never scans native history or
client drafts to populate records. Supplied native events and completed capability
action metadata are captured without extra session reads; diagnostic reads do not
generate more diagnostic events. Complete stream coverage, provider-request
capture, and external-account authentication require their own qualification.

The browser keeps unsaved destination and filter edits locally. A save includes
the observed shared policy revision; a lost reply is recovered by its original
command ID. Unknown deliveries are retained and are never retried automatically.
Storage controls and record pages do not start a model or a native session.

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

Native context clearing is advertised only after the configured native admin
negotiates the reviewed, history-preserving `native.contextClear` v1 contract.
The dedicated review, apply and receipt actions admit only that engine. Passive
receipt lookup resolves the selected session's original native identity and
canonical history directory through the public host port, including after
execution relocation or a changed default workspace. It never opens a worker or
repeats an uncertain clear. Older or unavailable peers leave unrelated sessions
and retained history usable.

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

Trusted local composition may set
`mcp: {python: "/installed/mcp-env/bin/python", installer: {executable: "/absolute/reviewed/uv"}}`.
The optional `installer` object accepts only `executable`: an absolute path of at
most 4096 characters without control characters. It is copied unchanged into the
private `mcp-launch.json`; browser actions and catalog entries cannot select or
override it. A configured path is authoritative: the broker reports unavailable
if it is missing or not executable, without falling back to `PATH`. Omitting the
option retains the broker's existing `PATH` discovery. Use a broker supporting
the managed-installer launcher contract before activating this option.

Do not combine `installer` with an external `broker`; configure that broker's own
trusted launcher instead. Local installation/provisioning owns binary provenance
and updates. The passive `smartTools.installerReadiness` action checks executable
availability only; it does not establish version, package compatibility, tool
installation or renderer readiness, and does not retry a previous installation.

`legacyClientState: {database: "/retained/legacy.sqlite", account: "local-user"}`
enables read-only migration. The account must exactly match the distribution.
Oversized, ambiguous or invalid legacy state is preserved, not silently truncated
or used to execute old device commands.
Selected legacy workspace IDs are mapped only when their exact saved path matches
an indexed workspace within the configured roots. Duplicate or unavailable
identities remain in the retained original. The Web client imports its original,
drafts, mapped selection, and completion marker in one private transaction and
preserves newer private edits. This does not import executable legacy shell
packages, shared application settings, or unresolved attachment bodies.

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

Optional `portability.resourcePayloads: true` connects the resources owner's
bounded metadata and body ports to signed transfer payloads. Export still requires
explicit `includeResourcePayloads: true` and a paired destination that negotiates
payload version 1. Reviews bind the exact source revision, descriptors, omissions
and payload plan; metadata paging never reads bodies. A destination verifies the
signed capsule, complete payload audit and exact bindings before activation and
again during recovery. Imported bytes remain inactive historical resources;
they do not become executable input. Missing or altered evidence refuses without
replaying native installation or activation. The owned exchange directory holds
the explicit detached payload directory; source filesystem paths are not signed
as destination authority.

`feedback: {python: "/installed/owners-env/bin/python"}` enables explicit GitHub
feedback through the independently installed `amplifier-unified-feedback` owner.
Drafts, correction editors and file choices stay on each client. Explicit Send
uploads bounded immutable files and admits one durable submission; a lost remote
reply remains unknown and is never reposted. Shared state contains only bounded
receipt summaries. Selected reports, diagnostics and reviewed excerpts load on
demand. Agent-origin remote writes and excerpt disclosure use a one-time review
in the active originating conversation through standard AHP tool confirmation.
The installed default shows the exact proposed operation and arguments; denial,
cancellation or turn completion grants no publication authority. A trusted
embedding can replace this policy with `authorizeFeedback`. A missing originating
conversation cannot publish. Read-only reconciliation does not imply permission to
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

Recovery also composes registered app-local reset ports from the native admin
and the actual configured notifications owner. Only advertised parts can be
reviewed: the app bundle default, notification settings, and separately authorized
private notification credentials. Each owner retains its own before-image; the
product stores redacted reviews and exact command receipts. Apply and separately
reviewed restore require the complete configured recovery fence and unchanged
owner revisions. Shared Amplifier/workspace settings, keys, native history and
client-local state remain preserved. This is not a complete product reset:
supervisor preferences and host policy remain separate.

`conversationPresentation: {}` adds reversible conversation visibility through
the host's local selected transaction and the recovery topic. It works with any
configured ACP engine and indexed catalog, without native administration or
global quiescence. Inject `authorizeRecovery` for the authenticated account.
The JSON launcher instead requires
`conversationPresentation: {authorization: "local-account"}`; this grants no
native maintenance or credential-export authority.
When full `recovery` is also configured, the same owner exposes presentation
actions without adding a second recovery topic; native recovery keeps its existing
maintenance requirements.

Preparation retains an explicit selection of at most 500 stable identities and
row revisions. Apply and restore use that exact account-owned review. Hidden
conversations remain authorized by exact ID for history, receipts, artifacts,
commands and deferred work. Visibility does not grant or revoke permission.
Client-private drafts, selection and preferences remain in each client.

The immutable marker-effect receipt and derived discovery readiness are separate.
An incomplete projection makes discovery unavailable, including workspace
session/workspace lists, while exact-ID access and unrelated work remain available.
Reconcile observes the original operation; it never repeats a reset or accepted
input. An explicit reviewed `recovery.presentation.rebuild` first seeds retained
authoritative mappings through the public catalog API, then performs a complete
metadata scan with zero issues, and finally lets the host project its retained
visibility markers. Root startup configuration must disable competing automatic
scans and writer hints during this sequence. Reconstruction reads metadata only
and starts no agent. A trusted embedding can supply
`conversationPresentation.reconstructMetadata` for its own catalog scheduling.

Full-product backup/restore and canonical history deletion are also separate
contracts. See the independent recovery component contract for native archives,
generated-bytecode clearing, omissions and uncertain-outcome rules. Queued
admission returns before background quiescence so the initiating request cannot
hold its own restart check busy. Partial or unknown reset results stay inspectable
and fenced; they do not trigger compensation or replay.

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

## Independently observed Git sources

The public `createGitSourceResolver({sources, git?, env?, timeoutMs?, deadlineMs?,
concurrency?})` adapter can supply the supervisor's `resolveSources` callback for
Git-backed components. `sources` is trusted launcher configuration containing
exact HTTPS `{repository,ref,protected?}` pairs. A plain ref selects a branch;
explicit `refs/tags/...` selects a tag and resolves its peeled commit. Explicit
protected entries refuse preparation. Unknown repos/refs, local Git URL rewrites,
missing refs and uncertain results refuse qualification. Publisher revision fields
are never used as observations.

Every invocation queries the remote anew, with four concurrent processes by
default, bounded output and deadlines. No clone, fetch, local source modification
or result cache is involved. Credentials remain in the operator's configured Git
helper/environment and never enter the observation or error record. Ambient URL
rewrites are preserved by refusing the observation, not silently overridden.
The current subprocess ownership implementation is qualified on POSIX; Windows
refuses explicitly. Registry packages require a separate source owner and are
not treated as Git observations. This does not qualify external Python runtimes
or supersede the native generation owner.

Use fresh observations at both forward preparation and final activation; retained
candidate hashes remain the offline verification and rollback authority. Git's
[remote-ref format](https://git-scm.com/docs/git-ls-remote) and
[ref validation](https://git-scm.com/docs/git-check-ref-format) define the adapter's
input/output boundary. No ordinary client state read invokes this resolver.

Selected-session permission editing is explicitly composed with
`nativeAdmin: {engine:'amplifier', permissions:true}` and a native launcher that
advertises `adminPermissions:true`. The host-scoped `permissions` topic uses an
explicit selected session URI resolved by the host; a client cannot select an
arbitrary native working directory. Agent actions remain bound to their own
session. Permission reads and receipt recovery remain available during a held
administration fence; writes share the native administrator's admission owner.
Effective policy changes apply on the next native input after a parked worker is
remounted; an active turn retains its original policy. Client editor drafts and
scope selection stay local. Custom community tool policy enforcement requires
its own qualification.

## Bounded history cleanup

`historyCleanup:true` composes the shared `history-cleanup` capability when an
explicit catalog and quiescence instance/scope are configured. `cleanup.preview`
reads one cold metadata page of at most50 roots and binds the authenticated
client/agent identity. Selection, cutoff, paging and review drafts remain private
client state. `cleanup.apply` requires the exact unexpired review hash and selected
subset; `cleanup.receipt` recovers the original outcome without retrying effects.

The host independently checks selected/subscribed sessions, live or uncertain
work, indexed descendants and native history activity. Before hide, the distribution
holds every configured product participant and asks its held lease for future
references to the exact family. A participant without `retentionHide:{version:1}`
or complete reference coverage refuses admission. Native family ownership is held
by the typed native operation; the cleanup facade covers only its own forwarding
lifetime. No canonical transcript, events, product record or workspace file is
removed. The feature remains unqualified for full product use until every deployed
owner's retention contract and the assembled flow pass.

An uncertain native result or release acknowledgement leaves intake protected.
`cleanup.reconcile` accepts the original child command identity and consults only
the host's exact settled native receipt before releasing held owners. It never
repeats hide or guesses a missing outcome. The private protection journal survives
restart. Service/update admission includes cleanup forwarding, and storage inventory
records this root-package owner's implementation content digest on explicit request.

Managed chats use `host.managedSessionRoot` plus the matching explicit native
launcher `managedSessionRoots` grant. Keep the managed root within this installation's
application state so that markers and generated files share stopped-product backup
coverage. External roots require explicit separate authoritative capture; native
history backup alone does not capture host-owned managed files. Creation allocates
one private directory without mounting Core or warming a worker. The catalog hides
allocation paths from the workspace picker and uses independent managed-session
grants for discovery. Managed ownership is verified from durable host/native receipts
before activation; a path or marker alone is not ownership proof.

Set `managedBundleCatalog: true` in the native launcher to offer the same global
registered root bundles for chats without a workspace. This catalog is read
without starting a worker; disabled registrations stay unavailable. Explicit
`forkBundles` allowlists, including an empty map, take precedence. Pass the same
`registryHome` to the launcher and worker preparation so draft provider/model
discovery resolves the installation's prepared sources. Bundle rows carry stable
IDs and revisions plus optional display labels; clients must submit the ID and
revision, not the label.

### Managed files protection and shared action facade

`createManagedFilesProtection` in `src/managed-files-protection.js` owns only
product-reference exclusion. Configure `{directory,instanceId,dataScope,
participants,readEffectReceipt,onMayBeIdle?}`. `participants` is the exact composed
owner census, using each factory's actual participant ID; exclude the initiating
managed-files facade and duplicate native/admin/transfer gates. The host/native
operation independently owns canonical-history and selected-family writer leases.
An absent participant or missing `managedFiles:{version:1,preservesCanonical:true}`
refuses before acquisition. A retention-hide marker grants no file-removal rights.

The host calls `acquire({commandId,session,descendants,operation:'dispose-owned-files',
reviewHash,allocation})`, where allocation has exactly
`{allocationId,executionDirectory,allocationHash,treeHash,entryCount,bytes}` from
its native-verified review. At most 100 descendants plus the root are accepted.
Under each genuine held lease the coordinator calls
`inspectManagedFilesReferences({sessions,limit:101,allocation})`. Only complete,
empty protected/omissions results permit native admission. File, publication and
artifact references remain relevant even when they would allow discovery hiding.
All product records and canonical histories remain owned by their original owner.

The returned `{ownerIds,release}` releases only after the trusted callback
`readEffectReceipt(session,commandId)` reads the host's exact durable effect:
matching command, session, review hash, operation, allocation, exact ordered
`descendants`, `familyCount` (descendants only, excluding the root), and
`preservesCanonical:true`, with `status:'completed'` or
`status:'refused',executed:false`. No caller-provided proof or hidden-session result
can substitute. The first terminal proof is persisted before any owner release;
restart and lost acknowledgments retain that exact proof. Unknown outcomes remain
held; `receipt(commandId)` is passive and `reconcile(commandId)` inspects original
proof and releases gates only, never repeats removal. Original acquisition
refusals unwind only confirmed, live, pre-effect leases. A separate SQLite OS lease
prevents competing processes from recovering an active coordinator's journal.

`createManagedFilesCapabilities` in `src/managed-files.js` accepts
`{host:()=>host,protection:()=>coordinator,authorize,directory,onMayBeIdle?,onInvalidate?}`.
The host-scoped `managed-files` topic is metadata-only and starts no agents or disk
inventory. Actions have strict public schemas:

- `managedFiles.preview {sessionId,protectSessionIds?}` returns the host review.
- `managedFiles.dispose {sessionId,reviewId,reviewHash,expectedHistoryRevision,
  protectSessionIds?}` uses the outer capability command ID, derives a deterministic
  separate child ID and returns `{commandId,ownerCommandId,receipt,replayed:false}`.
- `managedFiles.receipt {sessionId,commandId}` inspects that original child and
  returns the same identities plus `receipt`, `protection`, and `replayed:false`.
- `managedFiles.reconcile {sessionId,commandId}` passively inspects the native
  outcome through the host, then reconciles protection without dispatching disposal.

The nested host identity is `managed-files:` plus SHA256 of UTF-8
`JSON.stringify([sessionId,outerCommandId])`; this avoids collisions with the
host's outer capability command journal. Browser callers cannot supply native IDs,
paths, allocation evidence or actor identities. `authorize(context,{operation,
session})` must enforce the configured account and explicit action authority.
Agents are restricted to their trusted current session. The facade accepts the
root channel for authorized UI and the matching selected-session channel for a
native agent, preserving the shared `app_control` path.

Receipt and reconciliation actions are explicitly classified as passive under a
held intake fence. All their actual forwarding promises still count toward owner
lifetime and must settle before closing or acquiring another gate. The facade
advertises independent service-stop and retention-hide support; it does not hold
itself during its own disposal. Idle history-cleanup and application-updates
facades separately advertise managed-files support, binding their no-deferred-file
reference result to one exact held allocation/family. Their downstream authorities
must still appear independently in the census.

This module does not wire the distribution index or claim end-to-end native file
removal acceptance. Integration must supply the current host/native managed-files
implementation, the complete configured owner census, and source provenance for
these new private journals. Unknown work is preserved, never replayed.

### Launcher-owned infrastructure participants

A trusted launcher can supply `runtimeOwnerBindings: [{owner, storage}]` to
`createDistribution`. `owner` is the exact public quiescence participant; it joins
the required census without advertising a UI capability. It must independently
support each requested operation and held-reference contract. The launcher owns
its lifetime. Omitting configured quiescence or colliding participant IDs refuses
composition. The manual systemd wrapper uses this for its HTTP/WebSocket ingress.

The optional storage declaration has `packageName`, `packageVersion`, `revision`,
`configKey`, `rootRole`, and canonical `stateDirectory`. Cold `storageInventory()`
checks the package against the assembled public artifact manifest and the root
against `config[configKey].stateDirectory`. Missing or changed declarations remain
incomplete. Authority outside application state additionally needs explicit
`externalRoots` and `externalCoverage`; declaring no external data cannot suppress
an uncovered configured root. This private inventory never confers a writer lease
or replaces the archive's independent stopped-writer and capture checks.

## Canonical Native naming composition

When the configured Native admin peer advertises passive naming and the Host
provides title ports, composition binds `engine.sessionMetadata` and the native
capability NamingPorts to that existing admin connection. Manual rename keeps
the original command identity and disables automatic naming in Native. Auto
policy changes only metadata. Auto-name-now uses the existing Native generator
and commits through the Host title lock; concurrent manual edits defeat stale
suggestions. Older peers omit the optional naming capability.

The existing Native first-turn naming hook remains the automatic generator. Native
must emit standard ACP `session_info_update` after an accepted canonical name.
Host handles this update even after the prompt completes, rereads canonical
metadata through `engine.sessionMetadata`, and publishes the session title and
root summary. A delayed update cannot overwrite a newer manual title.

Root no longer projects custom `session.naming` events. Other extension owners
still receive those events. Naming diagnostics retain their existing shape with
zero pending, failure and dropped counts; Root owns no background title queue.
Manual naming ports, policy controls and explicit generation keep the same
canonical metadata authority and Host title lock.

This composition requires the paired Native standard-title behavior. Older
Native peers that emit only custom naming events are unsupported for automatic
title projection with this Root version; manual naming remains available. The
current component lock records exact artifacts but has no Native behavioral
minimum field. The qualified paired Native source and wheel must therefore be
bound in the existing assembly receipt before activation; this packaging gap
does not create a runtime fallback or a standing revision pin.

`test/naming.test.mjs` verifies retained ports and absence of duplicate custom
projection. The opt-in naming integration tests cover passive canonical metadata
and manual/Auto/generation races. The late-title integration test crosses actual
Native ACP, Host and AHP subscriptions with an inert local provider, including
post-prompt delivery and newer manual-title precedence. These controls do not
prove browser rendering, real-account acceptance or installed release adoption.

Workspace-first Native setup actions on the root channel authorize an explicit
workspace through the Host's existing `authorizeWorkspace` port, then carry its
canonical directory in trusted administration context. The original selector
is still checked by Native. Requests without a workspace keep the configured
default; session-scoped requests keep their session directory. A managed setup
request cannot also select a workspace. Authorization failures return the
existing confirmed no-effect refusal; errors after dispatch retain their
original uncertainty. This introduces no additional Bridge callback or owner.

The opt-in native-admin-workspace integration fixture exercises first provider
and model browsing for selected B while the default is A, using real gateway,
Host, Bridge and Native packages plus an offline fixture provider. It preserves
provider-local reasoning metadata and refuses foreign/session-misbound targets
without starting a worker or performing authentication/inference. It does not
identify operations missing from old receipts or establish real-account success.

### Existing-history rehearsal

`test/retained-user-history.integration.test.mjs` is opt-in. Set
`UNIFIED_DISTRIBUTION_ENTRY`, `RETAINED_HISTORY_PYTHON`, and
`RETAINED_HISTORY_COPIES` to an installed assembly, its independently installed
native/catalog Python, and an owned directory of already copied native session
folders. The test makes another private copy, preserves transcript and metadata
bytes, and exercises paged name sorting, identity search, passive history,
archive/reopen, and restart. It never imports credentials or submits a prompt;
the native audit refuses worker starts and writes outside the fixture. Optional
`RETAINED_HISTORY_RECEIPT` saves bounded results without message bodies.

A pass qualifies existing-workspace passive journeys only. Missing-workspace
refusals are retained in the receipt as gaps; they are not successful migration.
Temporary response-chunk IDs are excluded from restart comparison, while native
turn IDs, message locators, tool IDs and content must match. Full user migration,
resume/fork, events, artifacts, settings, account and device acceptance remain
separate gates. Retained fixtures contain private copied history and must not be
published with release evidence.
