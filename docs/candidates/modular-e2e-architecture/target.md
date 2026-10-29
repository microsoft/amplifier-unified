# Target architecture

> **Candidate / WIP — discussion only.** This document does not govern current
> implementation, amend adopted contracts, or establish release requirements.
> See [package status](README.md) before proposing adoption.

Proposal, October 28, 2026. Parent: [architecture packet](README.md),
[candidate vision](VISION.md), and [boundary draft](boundaries.md).

## Product outcome

People work with persistent named agents across discussions and devices. Agents
retain relevant commitments, coordinate bounded work, and deliver usable results
with evidence. The system spends human attention on consequential choices while
routine work proceeds within existing authority. Ordinary users get strong defaults;
developers can replace components and experiment in an independent environment.

The first proving journey is a shared project with two identified participants,
one named agent, two discussions and one real code change. The agent receives a
correction, uses a focused worker, keeps working after viewers disconnect, and
returns an independently checked artifact. Conflicting directions remain explicit.
A single-owner version proves the protocol path first; multi-human access is a
separate tested capability, not something inherited from multiple browser tabs.

## System map

![System and ownership boundaries](diagrams/target.svg)

Solid connections are proposed functional boundaries; dotted connections are
feedback or packaging relationships. The diagram describes the destination, not
the current deployment. Component separation does not imply network hops everywhere.

## Owners, repositories and state

| Owner / repository | Owns | Public boundary and independence |
| --- | --- | --- |
| Distribution: `amplifier-unified` | Selected component versions, install/update composition, supported feature matrix, activation receipts | Starts a matched system from packages; no private source copies |
| Experience: `amplifier-client-web` (proposed); existing native/TUI owners | Navigation, presentation, local drafts, reconnect UX, capability rendering | AHP client plus explicit host capabilities; operates against a fixture host |
| UI library: `amplifier-ui-components` (proposed) | Reusable components, accessibility, themes, Storybook examples | Typed props/events and component fixtures; no host calls or application store imports |
| Host: `amplifier-host` (proposed owner; compare existing daemon first) | Authentication/principals, scoped authorization enforcement, client subscriptions, command admission receipts, implementation registry, execution-session bindings and lifecycle | AHP server, ACP client, scoped capability dispatch; no coding-loop or Converge-domain imports |
| Collaboration: `amplifier-collaboration` (proposed) | Persistent agent identities/purposes, project membership, discussions, decisions, commitments, work objectives/assignments and desired revisions | Versioned domain operations/events used by people and agents through the same handlers; tests with fake host/executor |
| Native agent: `amplifier-agent-acp` (proposed extraction) | Existing Core/Foundation runtime composition, native history, bundles, live controls and native session ownership | ACP server and qualified extensions; native dependencies remain inside the adapter |
| Coding agent: `converge-acp` + `converge-bootstrap` (existing) | ACP presentation plus coding attempts, frozen assignment snapshots, action journal, candidate revisions and evidence-driven loop | Existing versioned bridge; parent intent is referenced, not independently rewritten |
| Strategy: `converge-strategy` (existing) | Gap selection, bounded next-work proposals, repair/reassessment policy | Pure observations-to-proposal interface; no direct effects or authority changes |
| Context: `converge-context` (existing) | Selection, retrieval planning and derived request views with source coverage | Versioned snapshot in; proposed context view out; original records remain with their owners |
| Models: `converge-models` (existing); optional decisions component | Provider transports, model recipes, native continuation, token admission, reasoning options and streams | Prepared-request/capability profile; CPU/GPU/cloud choices stay behind this boundary |
| Execution: `converge-execution` (proposed later extraction) | Checkout/process/container/runner lifecycle, admitted tool effects and effect receipts | Execute/inspect/cancel with scoped authority; host or remote workers use the same result contract |
| Verification: `converge-verification` (proposed later extraction) | Checks against exact candidates, evidence bundles, declared coverage and remaining needs | Uses execution without granting a worker permission to alter acceptance; separately runnable |
| Optional capabilities, existing owners where suitable | Artifacts/resources, MCP tools/apps, canvas, media/devices, schedules, publishing, transfers | Public host/resource/agent contracts; ordinary coding works with a capability absent |
| Interop: `amplifier-interop` (proposed), alongside `converge-evals` | Peer fixtures, user journeys, package-composition checks and measured model/workflow comparisons | Tests published interfaces without owning production work state |
| Context intelligence / learning: reuse an appropriate existing owner | Authorized traces, friction analysis, candidate improvement proposals and comparative results | Consumes observations; cannot rewrite commitments, acceptance or releases |

This is a target ownership map, not a request to create all repositories now.
Catalog/indexing starts as a separately testable host component; split it into the
already-proposed catalog repo when it can develop independently without duplicating
history ownership. Small provider recipes and attention policies need not each
become repositories. Existing capability/library owners should be reused after audit.

An agent workshop is a later client of these boundaries. It creates an agent
definition and package, tests it through ACP and exports it for real use. It does
not require a second host, execution engine or mandatory new programming language.

## One authority per fact

| Fact | Authoritative owner | Other representations |
| --- | --- | --- |
| Human/service principal and credential | Host/auth owner | Participant references, never copied credentials |
| Durable named agent and purpose | Collaboration | Host binding references agent ID; executable registry is distinct |
| Project membership, discussion and accepted decision | Collaboration | Authorized host views and context snapshots |
| Desired objective, acceptance revision and assignment | Collaboration work record | Runtime packet is a frozen input; strategy ledger is a derived proposal |
| ACP/native session identity and exclusive owner | Host binding + owning adapter's native lock | Project/discussion links do not create another native writer |
| Model history and native continuation | Selected runtime/adapter | UI transcript is a projection; portable facts are explicit exports |
| Actual action/effect/candidate/check result | Execution/runtime/verifier receipts, according to operation owner | Work record references receipts and derives its status |
| Artifact bytes/version | Resource or native artifact owner | Catalog and UI retain references/previews |
| Human-facing delivery and acknowledgement | Collaboration delivery record, backed by host dispatch receipt | Notification is a view, not proof the person read it |
| Private unsent input, selection and scroll | Client | Shared drafts are a separate explicit feature |

An assignment ID, attempt ID and action ID identify different things; they are
links in one work history, not three mutable task boards. Authoritative objective
revision changes through its owner. A late result keeps its original evidence
and is reassessed against new intent rather than discarded or silently accepted.

For the first cut, collaboration can use the existing database and storage adapter
behind its API. Extracting its repo does not require moving that database at once.
After cutover, no sibling may write its tables directly. A reliable outbox/inbox
can bridge owners; idempotent delivery is not proof an external effect happened once.

## End-to-end operation

```mermaid
sequenceDiagram
    actor Person
    participant UI as Unified experience
    participant Host as Durable host
    participant Work as Collaboration / work owner
    participant Agent as ACP coding agent
    participant Exec as Execution and verification
    Person->>UI: Objective or correction
    UI->>Host: Identified command, expected revision
    Host->>Work: Authorized intent
    Work-->>Host: Durable accepted revision / explicit conflict
    Host-->>UI: Admission receipt
    Work->>Host: Bounded assignment, context references, budget
    Host->>Agent: ACP prompt on bound execution session
    loop Observe, work, check, reassess
        Agent->>Agent: Select context; admit exact model request
        Agent-->>Host: Streamed progress and tool activity
        Host-->>UI: Authorized state updates
        Agent->>Exec: Scoped work or independent check
        Exec-->>Agent: Effect receipt / candidate-bound evidence
    end
    Agent-->>Host: Outcome, evidence references, unresolved needs
    Host->>Work: Record result against original assignment revision
    Work->>Work: Reassess current intent and assembled outcome
    Work-->>Host: Delivered result or consequential decision
    Host-->>UI: Inspectable artifact and evidence
```

The host admission receipt, ACP prompt return, completed tool call, verified
candidate and user delivery are distinct outcomes. The public UI uses ordinary
language for them; implementation IDs stay in receipts for diagnosis.

The coding runtime composes context, model, strategy, execution and verification.
The proposed first component assignment uses a fresh worker history and a shared parent
budget. Parent checks remain unchanged. Later recursive decomposition calls the
same bounded assignment contract; it does not grant more authority or reset time.
Parallel changes use isolated checkouts and integrate against identified revisions.

## Responsiveness, recovery and attention

The UI acknowledges local input immediately and shows durable admission separately
from model readiness. Streaming does not wait for tool completion. Mid-turn steering
records queued and applied states; newer direction is incorporated at a safe
boundary. An incomplete model tool batch has no effects. Cancellation requests
owned work to stop and reports confirmed cleanup separately from forced or remote
uncertainty. It does not undo completed effects; late approvals cannot restart it.

The host can outlive clients. Accepted work is retained when every viewer leaves.
After a process crash, use native receipts and inspection to distinguish not-sent,
accepted, running, completed and unknown. Resume only where the adapter qualifies
it; do not replay a prompt to discover whether it ran. Persisting a named agent or
an outbox alone does not implement durable execution recovery.

An attention policy proposes whether to proceed, defer presentation, ask or stop.
It consumes commitment/health/priority evidence and available user preferences;
it does not invent authority. A coordinator agent is one implementation of that
policy, not a second canonical work store. Ordinary authorized work needs no new
approval. Essential uncertainty stays visible; quiet periods must be supported by
worker health/progress evidence. Start with explicit rules and fixtures; add model
judgment where it beats that baseline.

## Protocol choices and limits

Official sources checked October 28. These are proposed placements, not claims of
conformance by current Unified or every optional capability in our ACP adapter.

| Boundary | Choice / maturity observed | Responsibility and limit |
| --- | --- | --- |
| Experience ↔ host | AHP target; official site links specification v1.0.0 | Synchronizes host-owned session state across clients. Our host still owns authentication, durable work and downstream uncertainty. [AHP](https://microsoft.github.io/agent-host-protocol/) |
| Host ↔ agent | ACP v1 initial baseline; v2 is currently documented as draft | Sessions, streamed updates, tool/permission/cancel interactions. Native session persistence and each optional capability must be qualified. [ACP v1](https://agentclientprotocol.com/protocol/v1/prompt-turn) |
| Mid-turn control | Advertised extension in current `converge-acp`; reconsider with qualified future ACP versions | Existing queued/applied steering semantics remain explicit. Do not treat v1 prompt completion as the early acceptance described by v2. [v2 lifecycle](https://agentclientprotocol.com/protocol/v2/prompt-lifecycle), [our live profile](harness-profile.md#live-controls) |
| Agent/service capabilities | MCP where suitable | Tool/resource boundary, with the same authorization and owner operations as UI actions. It is not our work scheduler. |
| Interactive tool experiences | MCP Apps, already used in Unified source | Tool-linked UI resources and host/app communication; renderer capability and access restrictions remain part of the host. [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) |
| External autonomous peers | A2A candidate | Discovery, messages, tasks, streaming and asynchronous collaboration across agents. It need not replace each local function or ACP interaction. [A2A](https://a2a-protocol.org/latest/specification/) |
| Agent-described UI | A2UI candidate; official site identifies 0.9.1 current, 1.0 candidate | Declarative component descriptions rendered from a catalog; complements, rather than replaces, the experience shell and component ownership. [A2UI](https://a2ui.org/) |
| Voice/video/device | Separate media transport plus host-visible control | Conversation/turn protocols are not a replacement for audio/video transport or targeted device effects. |

ACP is Agent **Client** Protocol here. A direct ACP editor can use the coding
agent without the host, but that does not grant shared-host collaboration.
An optional ACP-to-host bridge can serve such editors later; it must not own a
second history. Current `converge-acp` documents core recovery separately from
ACP session restoration; do not advertise restoration before it is implemented.
The standard itself makes resume optional. [ACP session setup](https://agentclientprotocol.com/protocol/v1/session-setup)

Use upstream schemas and reducers where available. Custom domain operations should
be scoped capabilities/extensions with one owner, not a fork of every protocol.
We can retain an efficient in-process interface behind an independently tested
package boundary. AHP/ACP do not require all implementation dependencies to be remote.

## Local and shared deployment

The proposed local development composition targets a qualified workstation: host/domain
services, artifacts, execution and verification locally; GPU coding model at a
qualified 128K minimum/256K preferred; optional CPU decisions independently
measured. Provider-specific thinking and compaction stay behind model/context
contracts. Long-context capacity is distinct from the smaller working view.

A shared host later supports multiple authenticated people and remote execution
workers with explicit workspace/location bindings. A remote command must say
where it runs, under whose authority, and where artifacts return. Do not make
shared collaboration depend on synchronizing arbitrary personal files or mounting
everyone's home directory. Device-specific builds use qualified runner capabilities.

The bleeding-edge and stable distributions have separate process/state roots and
component manifests. Optional frontier models are explicit developer choices;
local-only mode never silently uses them. A component can change without moving
active stable work to another runtime.

## Learning and promotion

Authorized observations identify lost constraints, unnecessary interruptions,
repeated investigation, stalls, verification gaps and expensive context handling.
An improvement becomes a bounded candidate assignment. Evaluate it against the
working version, including failure cases and user-level regressions; then adopt
it through the distribution's release policy. The observation system cannot edit
acceptance or promote itself simply because a model says the change is better.

Proposed evaluation priority: outcome quality, responsiveness and total time,
then cost. Also measure human effort as a product outcome. Attribute delay to
admission, context preparation, model first output,
execution and verification. Existing hard LoopsBench failures remain useful
evidence; successful collaboration fixtures do not erase those coding limits.
