# Use Unified as a Constellation backend

The installed `amplifier-unified acp` command attaches standard ACP stdio to an
already-running Unified host. Several attachment processes can use that same
host. Closing one does not shut down the host or stop accepted work. This text
backend uses the public ACP SDK shipped with Unified, without the private ACP
application or a second writer for Unified's data directory.

This is an execution attachment in the
[five-boundary composition](VISION.md#constellation-boundaries).
When it serves another Agent Instance, that agent supplies its continuing identity
and accepted-work interface; connected experiences retain their source conversations.
The adapter's advertised capabilities below describe its scope, not complete
ecosystem conformance.

See the [supported ACP pilot composition](../validation/acp-supported-composition.md)
for the recorded November 16 combinations, lifecycle and context-read evidence,
and preserved harness failures. That acceptance is version-specific; the current
setup and approval-policy guidance below has its own implementation history.

## Setup

Install and start Unified normally, connect its model account, and choose its
default bundle. Use a dedicated host/data directory for an independent agent
installation, or attach to an existing host. The attachment command never starts,
restarts or updates a host automatically.

For a local host under the same operating-system account:

```sh
amplifier-unified --data-dir /path/to/unified-home --workspace /path/to/workspace acp
```

This command speaks ACP on standard input/output; diagnostics use standard error.
Configure Constellation Instance's existing custom backend fields:

```json
{
  "command": "/absolute/path/to/amplifier-unified",
  "args": ["--data-dir", "/path/to/unified-home", "--workspace", "/path/to/workspace", "acp"],
  "env": {},
  "secret_env": {},
  "working_directory": "/path/to/workspace",
  "allowed_working_roots": ["/path/to/workspace"]
}
```

Apply this backend object through Instance's existing settings/configuration
operation, preserving its other settings and revision checks. The workspace must
already exist on Unified's host. Optional `acp --bundle NAME` selects a bundle
for new conversations; otherwise they use Unified's default selection.

Remote attachment uses `acp --server https://unified.example --token-file
/private/unified-owner-token`, plus `--ca-file` for a private CA. Remote HTTP and
redirects are refused. Local attachment reads the selected installation's existing
owner credential and configured local listener, using the host's existing leaf
certificate pin for HTTPS; it never creates a credential. This credential
provides owner access: keep it private and give it only to an authorized launcher.
Never place its contents in arguments, URLs or Instance settings.

### Choose a profile with MCP support

Transient HTTP MCP support also requires `tool-mcp` in the selected native
profile. Work 0.4.0 does not include it. If an attachment supplies
MCP servers without that capability, preparation refuses the request before
model execution.

For a new conversation that needs connected tools such as Instance's context
retrieval, save this explicit profile as `/path/to/profiles/work-mcp.md` on the
Unified host. The recipe pins Work 0.4.0 and canonical MCP v0.3.1, which preserves
server-declared tool failures along with their returned content:

```markdown
---
bundle:
  name: work-mcp
  version: 1.0.0
  description: Explicit Work profile with canonical MCP support.
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-work@b5154aabf0aaed0ad9465f711abb3bfb18dee6f8#subdirectory=bundle.md
tools:
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@77bba04693963b0fb24ce6fe1313c39c35e2d3be
---

@work:context/system.md
```

Select it through the existing launcher option:

```sh
amplifier-unified --data-dir /path/to/unified-home --workspace /path/to/workspace acp --bundle /path/to/profiles/work-mcp.md
```

For Instance, append `"--bundle", "/path/to/profiles/work-mcp.md"` to the backend
`args` above. The profile path is on the Unified host. This selects the profile
for **new conversations**; exact-ID resume retains the conversation's saved root
bundle. Changing launcher arguments does not convert an existing Work-only chat.
Unified's default profile remains unchanged.

Preserve existing [scoped source choices](../scoped-bundle-sources.md): module or
bundle overrides can take precedence over these declarations. Confirm the actual
selected sources when qualifying a different configuration. Transient server
URLs, headers and credentials arrive through the ACP connection, not this profile.
The MCP module also reads the execution account's ordinary MCP configuration;
review those settings when preparing a dedicated agent.

## Ownership and recovery

Unified owns native execution and durable input receipts. Constellation and its
experiences retain their public conversations and participation mappings. The
returned ACP session ID identifies the corresponding Unified chat, which can be
inspected in Unified while the external experience uses it.

New sessions, exact-ID resume, text prompts, streamed text/tool activity and
explicit cancellation use the existing execution owner. Reconnect never creates
a replacement chat or resends an earlier prompt. A lost response after dispatch
leaves the outcome uncertain to the client; inspect the retained chat before
deciding on new input.

Attachment does not change access mode. Existing Full authority continues without
a human approval loop. The authenticated attachment presents tool and native
configuration waits through standard ACP `session/request_permission`. The host
commits the exact current choice; a client response is not itself a grant. Stop,
expiry, revocation and a competing decision invalidate stale replies. Connection
loss preserves the pending record and never grants authority or replays work.

`allow_once` decides the displayed request. For configuration requests the card
discloses the named effects/scopes and existing lifetime through Stop, revocation
or expiry; it does not mean one configuration write. This adapter offers only
the current-request allow/deny choices; it does not create a second durable
approval-policy store. For Instance-hosted agents, agent-wide matching rules
and no-prompt mode belong to the Instance's declared policy integration, not
to a separate per-chat cache in this adapter. Unsupported reusable-scope
descriptors must not be inferred from model-authored prompt text or tool titles.
Use Unified's existing **Access & approvals** or `access` command to explicitly
select or revoke broader standing Full access. No attachment or availability
recovery enables it automatically; native budgets and OS limits remain in force.
Full access also changes supported filesystem access ceilings. It is not an
alias for suppressing approval prompts. Revoking a remembered policy must not
claim to undo an effect or revoke an already accepted configuration grant unless
the serving owner explicitly performs that grant revocation.

### Optional Instance-owned matching policy

An Instance can negotiate `_meta["com.microsoft.amplifier.approval-policy"]`
at initialization with `{version: 1, owner: "instance", instanceId: "<UUID>"}`.
Unified returns the same version/owner, producer
`com.microsoft.amplifier.unified.configuration/v1`, and `reusableRequests: true`.
An absent or unsupported negotiation leaves ordinary ACP allow/deny unchanged.
Unified has no remembered-rule store and offers no independent automatic mode.

For a pending native configuration grant, Unified can then add a descriptor to
`toolCall._meta` under that namespace. Its `key` hashes the canonical effects,
global/project/local scopes, resolved settings destinations and Unified data
home. Its `description` states those effects, scopes and grant lifetime;
global scope explicitly identifies shared Amplifier settings. Descriptions
must fit the receiver's 512-byte UTF-8 bound and contain no control characters;
an unrepresentable scope falls back to once/deny without truncation.
`allowOptionId` names the current `allow_once` choice, and `authorityLifetime`
is `conversation`. Chat IDs, call IDs, reasons and timestamps are excluded from
the matching key. Matching spans chats only when the actual targets and effects
match. The canonical decision rechecks that key before accepting the reply.

The same producer can describe a supported exact native tool call with
`authorityLifetime: "request"`. Its key has a distinct `tool.invoke` operation
and binds the actual registered tool, qualified source selection and verified
package-content identity,
schema, exact validated JSON arguments, module configuration, working folder,
current access/mode policy and applicable security limits. File calls also bind
resolved targets. It matches the same request
and context across chats, not equivalent commands or paths. Current call, chat,
generation and Stop identities remain in an ephemeral execution witness.
The live worker alone supplies the descriptor; saved events cannot create one.
Approval and the existing Work/file execution boundaries recheck the witness,
including after file-lock waits. A hook callback error does not approve it.

This requires the optional streaming `tools.dispatch.current_call` capability
and Unified's existing enforcing wrappers. Guarded reference read/write/edit and
patch tools have source-specific scopes. Verifiable Python ownership alone does
not describe arbitrary plugin state; tools without an explicit scope remain
Once/Deny. Shell `run` requests bind the exact command and arguments, inherited
environment digest, working folder, effective safety validator and execution
limits. Unix Bash and an already resolved native Windows Bash are supported;
unresolved Windows shells, WSL environments and managed process operations remain
Once/Deny. The existing Work boundary rechecks these facts before invocation.
This is permission memory, not attestation of every executable or file a command
may subsequently read, or a claim that repeating it has the same effects.
No display-text hashing or argument
equivalence is used. Modes, existing approvals, observations, file checks, Work,
budgets and native execution ownership keep their normal order and authority.

Canonical MCP wrappers require a connection witness captured by the client that
opened the transport, plus the exact remote tool, current connected registry
schema and arguments. Stdio captures the process environment and working folder.
The negotiated protocol and selected client implementation are bound too.
HTTP clients must additionally advertise `httpSendAuthorization: 1`: the client
binds the effective request headers, cookies, endpoint and parameter-header
mapping, tracks the exact queued RPC and rechecks the complete constructed
request at the actual transport-send boundary. Changed authorization or a
withdrawn invocation cannot authorize that send. A redirect or resend requires
a new approval decision; a refusal does not undo an earlier send. Custom auth
flows, URL credentials and older HTTP clients remain Once/Deny. This does
not attest a remote account or promise a freshly queried remote catalog or
implementation. A new connection with the same facts can match a future rule,
but replacing a connection during a pending approval invalidates that invocation.
The client checks the exact tool/arguments/session before any reconnect and again
immediately before SDK invocation; its private invocation scope is single-use and
expires when the call returns. Authorization/environment values participate only
in opaque digests; descriptors do not expose credentials. Existing MCP
configuration provenance, execution checks and ordinary unbound calls retain
their behavior.

Always permits future new matching requests across the agent's chats. It never
authorizes replay, proves idempotence, or resumes an uncertain prior effect.
Remembering a request adds no prompt to calls already allowed by normal policy.
Instance rule revocation affects future decisions; it does not undo an accepted
effect. Automatic policy remains a separate explicit choice and is not enabled
by this support.

The worker captures only its already validated runtime selection before and
after preparation. Normal noneditable file-cache wheel installs remain valid
representations of that selection; a PEP 610 Git URL is not required. Bounded
checks bind the complete installed module package to its RECORD inventory,
and a Foundation source import must match the selected source and installed
package bytes. Missing, dirty, changed or ambiguous evidence stays once-only.
Registry/archive wheel imports can instead bind the already qualified installed
distribution, its exact recorded lock selection and captured package inventory.
They do not require a Git cache. This is not an independent verification of a
downloaded archive or publisher; those tools must execute from their installed
package, while a source import still needs the selected-source equivalence check.
Installed module entry points bind the actual Python implementation to the
host's selected module/configuration. Ephemeral install/cache locations are
checked by the private witness, not included in the cross-chat key when selected
source and package content are identical. Editable, absent, ambiguous, invalid
or unverifiable implementations retain once/deny. RECORD alone is not publisher
authority. No new source store, approval-time
resolver or whole-environment scan is introduced.

Session-only configuration targets and unstructured tool approvals have no
reusable descriptor. A model-authored title, reason or metadata cannot mint one.
These requests retain once/deny choices; the Instance must not manufacture an
Always rule. An Instance may separately offer its explicitly selected automatic
policy for supported current requests, without bypassing Unified's remaining
authority, Stop, budget or operating-system checks.

Instance owns agent-wide rule storage, the owner-authorized connector actors,
policy controls and revocation. Removing a rule stops future matching approvals.
An already accepted configuration grant remains until its existing Stop,
grant-revoke or expiry boundary. Upgrades select neither remembered rules nor
automatic approval, and this negotiation grants no authority by itself.

Disconnect releases observation and future use of the attachment's transient HTTP
MCP settings. Admitted work retains its mounted tools and can finish. Reattachment
can retire a released, settled worker through the existing acknowledged retirement
path before supplying fresh tools. Connected owners, active/protected work and
unconfirmed retirement are not replaced. Retry after that work settles.

Non-authentication owner failures, including busy attachments and unresolved
prompt outcomes, use Unified's implementation-specific error code `-31000` with
the owner's reason. ACP reserves `-32000` for authentication; an ownership
refusal does not request sign-in. A request failure does not establish whether
earlier work ran or authorize automatic retry. Inspect the retained outcome
before deciding on another input.

Host shutdown remains different from client disconnect and follows existing
recovery policy. This feature does not promise survival of process/machine loss.
The authenticated WebSocket hop is internal to the shipped launcher and host,
not a portable upstream ACP network protocol; external clients use ACP stdio.

### Optional conditional retirement

Initialization metadata `com.microsoft.amplifier.retirement` version `1`
advertises `_amplifier/retire` and advisory notification
`_amplifier/retirementRequested`. These are private optional extensions over the
existing ACP connection, not upstream ACP session-close semantics or a blanket
claim that a wrapper can be killed safely.

The request contains only `sessionId`, the exact execution attached to this
connection. The result repeats `sessionId` and has `disposition` equal to
`retired`, `busy` or `unknown`. Only `retired` authorizes closing this attachment.
The owner refuses cleanup when it has other attached sessions or unsettled work;
this does not limit how many sessions a connection can normally use. Native
retirement checks active and background work, approvals, tools and persistence
under the existing admission/retirement handshake. Connection-bound tools stay
mounted until the acknowledgement and a final matching-owner check. Refusal,
timeout, caller cancellation or changed ownership does not release those tools.
An exact successful result can be read again after a lost response, without
creating a session or replaying an input.

The advisory notification contains only `sessionId`. The existing worker-retention
sweep requests cleanup only when its warm-worker budget, idle timeout or pending
generation update calls for it. The defaults remain 32 warm workers and 12 idle
hours. Finishing a turn does not itself request attachment cleanup. Policy is
checked again under native admission before retirement: a delayed pressure hint
cannot evict a worker that is now within its warm budget. Older
`retirementAvailable` advertisements do not negotiate automatic cleanup.
For an attachment with no mounted tools, normal native retention can finish
without first involving that connection. Its acknowledged completion emits the
same request after the exact worker is removed, including after a late reply.
The attached client can then obtain the already-retired receipt. This does not
start another retirement or replay work, and failed notification delivery cannot
undo the completed native retirement.

The request is neither an idle guarantee nor an instruction to retry work.
Reading an already-retired receipt does not produce another notification. This reuses the existing
retention sweep and settings; no client timer or second retention policy is added.
Naming completion wakes that owner after its future and persistence settle.
Background resources, kernel or computer custody and uncertain operations can
still refuse cleanup. A demand arriving while the attachment is busy or already
checking retirement can be ignored; subsequent existing retention sweeps can ask
again. There is no guaranteed immediate retirement at every resource transition.
Confirmed cleanup retires the native warm worker as well as its attachment, so
the next real message resumes saved history with worker startup cost. It trades
idle resources for later startup, rather than promising a performance gain.

## Scope and qualification

### Optional Unified public Agent Instance profile (candidate)

This candidate maps the continuing Agent Instance role onto existing Unified
owners. It is not deployed conformance or a second agent service. An operator
can set `agentInstance` in the existing private server configuration:

```json
{
  "agentInstance": {
    "id": "urn:example:agent:researcher",
    "label": "Researcher",
    "workspace": "/srv/research",
    "bundle": "/srv/profiles/research.md"
  }
}
```

The explicit ID names the continuing public agent, not a process, label, native
child or ACP session. Preserve it across restarts and implementation/model
changes. The first slice exposes one profile and defaults to one native root per
exact source/conversation pair; this is not a universal one-agent-per-installation
or one-memory-per-room rule. Separate roots retain their own private context,
model/provider bindings and native writers. The configured workspace and bundle
must match the launcher; exact resume keeps saved root configuration and custom
source pins. Changing a profile does not migrate history or credentials.

Ordinary ACP needs none of this configuration. A client opts in at initialize
with `_meta["com.microsoft.amplifier.agent-instance"] = {"version": 1}`.
If configured, the response advertises `agentId`, `label`, `_amplifier/instance`,
supported interactions/controls and reconciliation limits. An absent profile
leaves plain ACP unchanged. A client must retain the advertised ID and refuse an
unexpected replacement or disappeared profile rather than silently create work.

For negotiated `session/new` and exact `session/resume`, that metadata contains
`{"agentId":"…","source":{"sourceId":"…","conversationId":"…"}}`.
Use the conversation owner's configured public origin for `sourceId` where
available and retain the exact pair. Local UUID-qualified room references are
correlation only; they cannot distinguish copies/imports on different hosts.
The response repeats the binding and current `expectedInterruptionRevision`.
The existing host creation receipt recovers the same root after a lost reply;
no private child is promoted into a public instance.

Prompt metadata adds `input` with immutable original `id`, integer `revision`,
`actor`, `audience` (string array) and `replyTo`, plus the observed
`expectedInterruptionRevision`. These are source-attributed facts, **not human
grants**. Deduplication uses original source/input/revision, not text or JSON-RPC
call ID. Different payload or attribution for the same revision conflicts;
another call ID does not authorize replay. A duplicate returns an explicit
retained-input error with `data.inputReceipt`, not a false completed turn.
Revision identifies a source snapshot, not a promise of automatic revision
ordering, retraction or reversal of earlier effects. The source adapter owns
freshness before dispatch. Stop fences stale new intent; inspecting an old input
remains possible after Stop and never resumes its work.

The optional `_amplifier/instance` request contains exact `sessionId`, `binding`
(the negotiated agent/source metadata) and `operation`:

- `inspectInput`: `binding` also contains the original input metadata. Reuses
  `conversation.delivery` for retained/native evidence and exact input-status
  annotations. Returns the lookup ID/revision, not invented actor attribution.
  Missing evidence stays unknown, never proof of non-delivery. `work` links only
  a current task whose native creation generation proves that original input.
  `work:null` does not prove no historical or later-amended work exists.
- `read`: returns the source execution's existing bounded `task.get` result,
  native status and `expectedExecution` (latest admitted input ID and Stop
  revision). Task detail/history references retain their existing owners.
- `update` / `pause`: additionally require stable `commandId`, exact `taskId`,
  `expectedRevision`, and `changes`. Update supports the existing objective,
  correction, constraints and turn-limit fields. Native task CAS and persistence
  retain used turns, effects and unknowns. Neither operation creates or resumes
  an objective.
- `stop`: the same fields, empty `changes` and the caller's observed
  `expectedExecution`. Pauses that exact mutable task before native Stop;
  `taskId:null` is supported only when saved task authority proves no task or a
  completed task. Newer input/interruption supersedes delayed Stop. Its receipt
  is admission, not proof of settled execution, descendant revocation or rollback.
- `workReceipt`: stable original `commandId` recovers existing native task and
  host Stop receipts without redispatch. Partial/unknown legs remain explicit.

All operations require this connection's server-held exact source binding.
Current profile/source restrictions and native source/location are rechecked
after awaits, before publication and at write/approval admission. Optional
`sources` in the profile restricts exact source pairs; it is not required per-room
setup. Label edits and unrelated source additions do not revoke other bindings.
Removing a source restriction entry, changing identity/pins or removing the
profile refuses future operations; accepted work is not retroactively undone.

**Trust and limits:** the authenticated launcher still possesses the existing
host-owner credential. These checks scope the profile context, not that owner's
administrative authority; they are not hostile multi-tenant isolation, actor
authentication, credential attenuation or an independent permission cache.
Ordinary ACP cancel remains current-turn cancellation, not accepted-work
revocation. Inspection recovers disposition, not an unbounded transcript or a
lost final-answer stream. Source/actor/audience strings have wire-validation
bounds, not work quotas. No browser participant-control UI is added here.

Maintained `tests/test_agent_instance.py` exercises controlled-runtime SDK and
real task-owner persistence/CAS boundaries. Native worker, full Work composition,
actual external Instance and rendered multi-browser qualification belong to the
separately owned combined campaign; these local checks do not claim those passed.

Advertised capabilities are text, resume and transient HTTP MCP. Legacy-history
loading, images/audio, client-side files/terminals and a new permission UI are not
advertised. Companion's voice layer can use this text backend. Separate chats can
run concurrently; one native writer and ordinary admission still govern each
execution.

Maintained qualification uses the real SDK, connection and service persistence
with a controlled runtime, plus a real worker retirement handshake. Live-provider,
Companion voice and user-desktop acceptance are separate evidence; candidate tests
do not imply adoption by a live host.

The earlier connected-context retrieval qualification used Unified 0.94.1 and
MCP v0.3.0. The [runnable Constellation reference](https://github.com/microsoft/amplifier-acp-lab/tree/b2a35e2ed600d6093832d3215514fb73c34a1bd3/examples/five_primitives)
exercises Unified 0.95.0 with the MCP error fix now released in v0.3.1. It checks
source-linked work, result inspection and recovery through authenticated APIs,
using a deterministic provider and a prepared worker with automatic runtime
preparation and qualification bypassed. Its recorded inputs and limits remain
separate from the current recipe pin and from default-profile or browser acceptance.
