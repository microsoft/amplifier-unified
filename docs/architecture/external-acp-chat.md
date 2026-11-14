# External ACP chat pilot

Unified can own a new conversation whose agent executes in an operator-configured
external process. This is an opt-in text pilot in the existing host, not a new
host protocol or conversion of native Amplifier chats. Existing native chats keep
their runtime and history owners. Core is unchanged.

The host owns the chat ID, accepted messages, displayed replies, command receipts,
workspace association and saved access choice. The external agent owns its
execution session, tools, model and private context. A singleton's
`externalAgent.sessionId`, or each group member's
`externalAgent.participants[].sessionId`, is its remote execution identity;
it never becomes `runtimeSessionId`, `nativeProject`
or `nativeIdentity`. Launch arguments and enrollment secrets are not public chat
metadata. Host-owned messages use the existing view persistence mechanism under
`external-conversations/<host-id-digest>` rather than a fictitious native session.

## Operator setup

Use **Settings → External agents** to add or edit an entry in the existing private
server configuration. Saves take effect for new launches without restarting the
host, installing software or making a test connection. Existing entries carry a
revision so another editor's changes are not silently overwritten. The picker
lists names and IDs, not launch arguments or credential locations. Settings reads
one private launch configuration only when editing it; that read is not stored in
shared command receipts. Authenticated users and agents with existing global
`connections` authority (including standing Full access) use the same actions:
`externalAgents.list`, `.get`, `.save` and `.remove`. Adding an entry may omit its
ID; the host derives a stable `savedId` from the command identity. If the private
file was saved but its command receipt was not, retrying that same command
recovers the identical saved entry without another write; different contents
conflict. Editing and removing require the current
`expectedRevision` from the list or detail. Removing a connection preserves its
chats, history and already accepted work; it prevents a later new send through
that removed entry.

Manual setup can still add an `externalAgents` entry to `config/server.yaml`,
followed by a normal host restart. For a published Constellation Instance, use its maintained
stdio ACP command (the exact command is version-dependent; Instance 0.22 uses
`agent-instance acp`). Enroll a dedicated ACP client through the Instance owner's
normal mechanism. The token file belongs to the host service user; do not put its
contents in configuration, messages or URLs.

```yaml
externalAgents:
  - id: research-instance
    label: Research Instance
    command: /absolute/path/to/agent-instance
    args: [acp, --home, /owned/instance-home, --token-file, /private/acp-token]
    sessionCwd: /absolute/workspace/on/the/agent
```

Optional `cwd` is the local launch directory. `sessionCwd` is the execution
location sent to the agent, defaulting to the chat's workspace. Neither is a new
filesystem grant. Arguments are passed directly, without a shell. A fixed SSH
launcher can use the same stdio boundary; remote deployment is not qualified by
the local pilot.

Choose the external agent in New chat and an existing workspace, then submit the
first message. The draft stays private and creates no conversation or connection
until submission. The same `session.create` action accepts an explicit existing
`workspace` and `externalAgentId`, or a unique `externalAgentIds` list for a group;
it does not allocate a managed or new folder for an external chat. The two
selection forms cannot be combined. **Add agent** includes another configured
participant before creation. Selecting several agents creates a shared room;
messages are posted to the conversation without choosing a recipient. Selecting
one agent retains the existing direct chat experience. Earlier saved directed
groups retain their **Send to** control and captured recipients. The existing chat
interface displays external ownership and omits unsupported
native configuration, voice, attachments, edit and fork controls. Rename, pin,
archive, ordinary text, Stop and the existing Access control remain host actions.

## Shared conversations

New multi-agent conversations record `externalAgent.participation: "shared"`.
This marker selects shared participation; existing native, singleton external
and unmarked directed conversations keep their current admission, streaming,
Stop and continuation behavior. No saved history is silently converted.

A public message is accepted once by Unified independently of any model turn.
Each participant has its own exact source/execution binding and delivery record.
People may post while agents work; separate participants can execute concurrently,
while each native execution retains one writer and one active prompt. Public
participant metadata contains current activity and compact receipt facts; streamed
text belongs to the selected conversation body, not the catalog.

The room connector supplies original message identity/time and bounded attributed
public context to the agent's advertised Instance attention capability. The
instance decides whether to engage. Without that optional capability, the
connector uses the existing Slack-style quiet ordinary ACP speak/pass convention
on the same participant session. Decision output stays private. A decision is
neither a permission grant nor proof that work happened; advertised attention
errors do not silently fall back or repeat. See the canonical
[Instance attention contract](https://github.com/microsoft/amplifier-constellation-instance/blob/main/docs/attention.md).

Completed public agent contributions are committed once and offered to the other
participants as source activity, preserving their actual author. Own echoes,
private tool/reasoning updates and streaming chunks do not trigger new turns.
Names and optional addressing are context, not mandatory routing controls. A
participant can remain silent without posting a blank response or acknowledgment.

A busy participant can receive negotiated awareness tied to its actual active
prompt. Without confirmed incorporation, positively unsubmitted source events
remain pending consideration after its current turn; later public context can
show that a request is already resolved. An ambiguous observation remains
unknown and is not replayed. Neither receiving an event nor observing a cursor
proves model incorporation or completed effects.

Room Stop advances the existing durable interruption boundary, cancels current
participant turns and pending opportunities through that boundary, and prevents
late callbacks from restarting the exchange. A later explicit message may
continue normally. Restart resumes only known-unsubmitted opportunities with
their original identities; interrupted or uncertain dispatches retain unknown
receipts without automatic replay. Update-held inputs stay under the existing
update owner's admission boundary. An independent participant's failure does not
transfer or repeat its work through another agent.

The initial shared-room connector supplies bounded public context. On-demand
retrieval from a Unified-owned room requires the separate source MCP binding;
the already-qualified Conversations-source retrieval is not evidence that this
binding is installed. No new history store, Core change, or Instance management
credential is required for shared participation itself.

## Earlier directed conversations with multiple agents

A group has one host-owned public transcript and a separate saved execution
binding per participant. `conversation.send.recipientAgentId` explicitly selects
one group member. The browser captures that choice with the submitted input,
including retries and uncertain receipts; navigation or a later picker change
does not redirect it. Saved messages retain their recipient or author's ID and
label. Streaming and interrupted partial replies retain the actual author.
Existing singleton chats and their saved bindings remain usable.

One turn executes at a time in each conversation. Stop addresses that active
participant; it does not cancel another member's idle execution session. Each
member resumes its own exact remote identity. Unknown delivery remains attached
to the original input and recipient, never replayed through another agent.
This is explicit directed participation, not a broadcast or autonomous group
workflow. Adding/removing participants in an existing conversation is separate.

During an app update, each external chat can hold one known-unsubmitted input.
Its recipient is saved immediately, without marking a remote turn active.
Another send to that chat is refused before acceptance while the input waits;
other chats keep their own admission. After release and settlement, an explicit
next input can proceed. This does not keep unrelated chats waiting for an
external model turn behind the global update boundary.

Before a group turn, the addressed agent receives a bounded projection of prior
public messages: at most 32 rows and 24,000 text characters, with speaker/source
identities and an explicit omission marker. The current request remains a
separate prompt block. Historical requests are labeled as context, not new work.
The projection never reads another agent's private working context or unsent
client drafts and does not claim complete-history retrieval. On-demand retrieval
from an experience remains a separate capability.

## Delivery, recovery and permission

The maintained `agent-client-protocol` SDK owns JSON-RPC framing, stream dispatch
and local subprocess cleanup. Unified initializes without advertising client file
or terminal callbacks. Backend-owned tools remain the agent's responsibility.
The saved host Full choice can select an offered ACP allow option. Other access
modes return cancellation rather than inventing consent; this pilot does not add
a permission dialog. Changing access preserves the existing revision and authority
checks, without scheduling a native-runtime remount for this external chat.

The host saves the remote session identity before sending its first prompt. The
initial receipt means the host accepted the message and delivery is `sending`;
ACP has no early prompt-acceptance acknowledgement. A completed prompt response
confirms delivery. A lost response after dispatch remains `unknown`, preserving
partial visible text. Receipt retries never resend it. A new explicit message can
continue after cleanup; the earlier unknown message remains unknown.

Reconnect uses the same saved remote identity only when the agent advertises
`session/resume`; an unsupported or failed resume does not create a replacement.
This pilot does not import legacy history. Singleton continuation keeps its
existing prompt behavior; group turns include only the bounded public context
described above. Changing a configured launch identity requires a new chat rather than
silently rebinding existing work.

Stop records the ordinary durable interruption revision before sending
`session/cancel`. This revision also revokes pending Full permission decisions.
Cancellation has a bounded wait; losing confirmation leaves delivery unknown.
Closing an attach process is not a promise that remote effects were rolled back
or background work survived. Instance/backend lifecycle rules still apply.

## Validation scope

Maintained tests use a real SDK stdio peer without a model and the actual host
service/storage paths. They cover new/resume, stream, Stop, lost responses,
duplicate receipts, explicit later input, endpoint identity, saved history and
Full authorization. Real Instance/browser acceptance is separate evidence, not a
claim of parity for native tools, arbitrary ACP extensions or other platforms.
