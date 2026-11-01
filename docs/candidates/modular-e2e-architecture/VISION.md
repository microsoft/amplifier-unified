# Candidate vision

> **Candidate / WIP — discussion only.** This vision does not replace the governing
> Unified vision or assign implementation work. See [package status](README.md).

People should be able to give an agent a desired outcome and its promises, then
work with it across discussions and devices until it delivers a usable result
with evidence or identifies a specific decision or blocker. Routine authorized
work should proceed with strong defaults and little manual coordination.

The coding foundation follows an evidence-driven cycle: interpret vision and
contracts, inspect reality, identify unmet promises, derive bounded work, implement,
verify independently and reassess. Completion of a turn or a task list does not
establish that the requested outcome was fulfilled.

## Independently owned components

Each component has a purpose, contract, dependencies and checks that let an owner
change it without understanding the entire system. Higher-level projects compose
published interfaces and test the assembled experience. Repository boundaries
should reduce what an owner must understand; they do not require a service or
database for every component.

Consumers reuse existing capabilities and express unmet needs through the owning
component. Owners can extend interfaces, add variants, factor common primitives
or change implementations while preserving promised behavior. A Storybook library
and a separate experience shell illustrate this pattern for UI development.

Use public community interfaces where they support useful substitution. AHP and
ACP are the proposed host/agent boundaries; MCP Apps has an existing implementation
to preserve and qualify. A2A and A2UI remain candidates for specific journeys.
Protocol selection should remain revisable without exposing every consumer to
the implementation's private structure.

## Continuity and shared work

A named agent is a participant with a purpose and scoped continuity, not merely
one model history. Projects, discussions, decisions and versioned assignments
have explicit owners. Execution sessions remain bound to their engines. Private
client state stays private; shared-human access and conflicting directions need
explicit identity and authorization semantics.

One authoritative record owns work intent. Runtime attempts and evidence refer
to that intent instead of creating competing task boards. A late result is retained
and reassessed against current direction. Child success does not replace parent
acceptance. Leaving a client or ending a call does not revoke admitted work.

## Quality and responsiveness

Quality leads responsiveness and total time, which lead cost. Also measure human
effort, avoidable interruptions and repeated investigation. Use hard coding tasks,
retain failures, and evaluate strategies against unchanged acceptance and budgets.

The local profile targets open-weight models with at least 128K context capacity,
preferably 256K, and measured concurrent workloads. Streaming, mid-turn steering,
honest cancellation and source-preserving context management are essential.
Provider-specific thinking, continuation and compaction stay behind model/context
interfaces. Decision models are candidates for roles where measurements show
value beyond simpler policies.

## Progressive replacement and improvement

Keep the useful Unified experience while proving replacement boundaries on real
journeys. Existing sessions keep their native engine; new work can adopt another
qualified implementation. Extract an owner once its contract can be exercised
independently, then improve its internals in parallel with other owners.

Development and stable compositions use distinct versions and writable state.
Authorized observations can propose improvements, but candidates must demonstrate
better results before adoption. Recursive improvement uses the same bounded work,
authority and evidence rules as any other development assignment.
