# Amplifier Unified family — Vision (RATIFIED 2026-10-02)

This vision describes the intended destination of the Unified family.
It is not a description of the currently released implementation.
The architecture direction was requested on 2026-10-02.
The linked contracts were approved for implementation on 2026-10-02.
Evidence and adoption records determine what has been demonstrated.
Change this destination before deriving work that changes its meaning.

## What Amplifier Unified is

Amplifier Unified is a way to continue agent-assisted work across clients and
devices through a durable host. Its Amplifier defaults are useful on their own,
and its public protocol boundaries let people choose other agents, clients and
optional capabilities. Work and its history outlive the interface viewing them.

## Principles

### 1. Protocols make parts independent
Clients talk AHP to hosts; hosts talk ACP to agents. Upstream definitions own
their semantics. Independent implementations can join without copying Unified
internals. Optional extensions expose a capability without making it mandatory
for ordinary conversation, tools, approvals and cancellation.

### 2. Amplifier earns its place
Amplifier is the default through useful behavior, not a private transport
requirement. Its composable providers, bundles, tools, hooks, contexts and loops
remain available behind its agent boundary. People can adopt the host or a
client first, then choose Amplifier for new work when its value is demonstrated.

### 3. Attention determines the working set
Running work and viewed resources deserve memory. Historical existence alone
does not. Catalogs are filtered and paged before details are loaded. A host
with thousands of historical projects remains responsive when only a few
existing workspaces and conversations are relevant to the person using it.

Runtime reuse and retirement belong to the host, within explicit budgets and
protection for active work. The legacy Ready conversations settings are retired
by explicit user decision on 2026-10-04: no user-managed warm count, idle hours
or preparation on selection. Selecting or reading history remains a view operation;
accepted execution may prepare a cold runtime and incur startup latency. Reusing
a compatible prepared runtime does not alter its conversation identity or history.
This destination does not certify an adaptive cache policy or its deployment.
See [the retirement decision](ready-conversations-retirement.md).

### 4. Private interaction stays with the client
Unsent drafts and presentation state belong to their originating client.
Local persistence survives its restart without backend synchronization.
Shared drafting or agent access to an editor is explicit and bounded.
Accepted work belongs to the conversation and reaches authorized subscribers.

### 5. History is durable without being resident
Native conversation records and retained events remain on disk. Rebuildable
indexes make them discoverable without loading them into every process.
Opening a historical conversation loads a view, not an agent runtime.
Losing a derived cache does not lose accepted work or replay its effects.

### 6. One conversation has one execution authority
A conversation keeps its agent implementation and native identity.
Authorized clients can observe and act through the same host without starting
competing executions. Leaving a view does not cancel work. Switching agent
implementations is a new conversation, not an implicit conversion of history.
Catalog visibility and client presentation do not confer or revoke that authority.

### 7. Small updates perform small work
A change visits its resource, dependencies and subscribers. It does not copy,
serialize or hash unrelated historical state. Slow subscribers have bounded
queues and recover through the protocol. A local input responds immediately;
durable acknowledgement and model readiness remain distinct outcomes.

### 8. Separate ownership enables parallel work
Web, TUI, host, agent adapters, discovery and optional capabilities have explicit
repository boundaries. Each can run against a fixture peer. Cross-repo changes
name their producer, consumers, contract clauses and integration evidence.
Packaging many components together never restores private runtime coupling.

### 9. Recovery tells the truth
Clients distinguish local intent, confirmed shared state and unknown outcomes.
Reconnect restores state through upstream rules and never assumes tool work is
safe to repeat. Old sessions remain native-resumable when qualified, otherwise
inspectable with a clear limitation. Saved configuration is not mounted behavior.

## What this deliberately resists

- A global application snapshot as the default read, update or persistence unit.
- Loading dormant agents to fill a sidebar or make a session selectable.
- A private fork of a standard hidden behind a compatible-looking endpoint.
- Multiple stores claiming to be the same session's canonical history.
- A browser session becoming the lifetime or permission boundary of an agent.
- Requiring a new client to embed the Amplifier runtime to participate.
- Treating a repository extraction as proof of modularity or performance.

## How you can tell it is working

- A person types 100 characters in a private draft and causes 0 backend writes.
- A user with 4,000 historical projects sees only relevant existing workspaces.
- A person pages 25,000 stored sessions without initializing 1 dormant agent.
- Two web clients and two TUIs follow 1 conversation while keeping private drafts.
- A reviewer runs 1 external agent through our host and our agent in 1 external client.
- Three teams build against fixture peers in 3 repos before integrated acceptance.
- An operator clears 1 derived index and recovers accepted history without replay.
- A release reviewer sees 0 claims that source inspection proves deployed behavior.

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-03 | Clarify that visibility does not change execution authority. | Independent presentation-reset boundary review; [WS1](../../contracts/working-set.v1.md) specifies the intended separation, with implementation qualification still pending. |
| 2026-10-02 | Initial destination for protocol adoption and decomposition. | [Design packet and source assessment](README.md). |
