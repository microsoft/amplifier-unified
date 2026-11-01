# Candidate alternate-agent profile

> **Candidate / WIP — discussion only.** This is context for the proposed alternate
> backend, not a claim that Unified has integrated it. See [package status](README.md).

The proposed Converge backend has separate bootstrap runtime, ACP adapter, model,
context and strategy owners. The [source manifest](source-baseline.json) identifies
the reviewed revisions. Their source and historical test artifacts are not included
in this package; review of this summary does not qualify a deployable integration.

## Inputs, execution and evidence

The runtime accepts an objective and executable acceptance checks for a repository,
works on a separate candidate, and records actions and independent check results.
Context and model adapters prepare bounded requests while retaining original-source
references. An optional strategy chooses bounded work from observed gaps; effects
and acceptance remain with their respective owners.

Historical hard-task evaluations include unsolved parent outcomes. A narrower
fresh-context artifact task produced a usable component, but that did not prove
the parent outcome. The proposed next quality step is bounded fresh-worker
delegation with unchanged parent acceptance, shared budgets and revision-bound
results. It is a planned capability, not an implemented guarantee in this profile.

## Live controls

The reviewed ACP v1 adapter requests model streaming by default, publishes ordinary
agent message chunks and tool updates, and keeps one active prompt per session.
Separate sessions can progress independently. Incomplete model tool arguments do
not authorize effects.

Mid-turn input uses the advertised `_session/steering` extension. Its receipt
distinguishes durable queued input from application at a safe model-context
boundary. Queued direction cannot silently disappear at completion. The extension
does not change the frozen acceptance definition or widen permissions.

Cancellation remains distinct from steering. Late permission replies cannot revive
cancelled work. Confirmed local cleanup, remote/model cancellation uncertainty and
already-completed effects must remain distinguishable. Transport loss is not
evidence that a submitted action never happened.

## Persistence and compatibility limits

Core run recovery is different from ACP session restoration. The reviewed adapter
does not establish general ACP session restoration or transparent cross-engine
continuation. Optional source-preserving session context is not equivalent to a
shared-project memory or multi-human authorization system.

Integration needs real host/client peers, capability qualification, native-session
coexistence and the candidate package's public user journeys. Historical unit,
mock and real-model checks do not by themselves prove that assembled experience.

References: [ACP v1 prompt lifecycle](https://agentclientprotocol.com/protocol/v1/prompt-turn),
[optional session setup capabilities](https://agentclientprotocol.com/protocol/v1/session-setup),
and [extension mechanism](https://agentclientprotocol.com/protocol/v1/extensibility).
