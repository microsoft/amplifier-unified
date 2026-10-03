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
