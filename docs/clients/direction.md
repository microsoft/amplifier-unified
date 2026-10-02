# Shared-client direction and coordination

**Forward direction updated 2026-10-02:** [the architecture packet](../architecture/README.md)
now defines AHP clients, a generic durable host and ACP agents, local private UI
state, bounded discovery/residency and separate repository ownership. The original
client promises remain draft and are amended to follow those protocol boundaries.
This is a design update, not an announcement that the released transport changed.

**Status: proposal for review.** This packet establishes an Amplifier-specific
destination, draft promises, a source assessment and an implementation sequence.
Publishing it does not ratify the contracts or change a release. A connected TUI
has since been implemented; see the [progress note](assessment.md#progress-since-the-assessment)
and current [TUI guide](tui-handoff.md). The original source assessment stays
explicitly historical instead of being presented as today's missing-work list.

## Read the packet

| Document | Purpose |
| --- | --- |
| [Vision](VISION.md) | The experience we want across clients and devices. |
| [CX: client experience](../../contracts/client-experience.v1.md) | Observable journeys and common action meanings. |
| [CS: client state](../../contracts/client-state.v1.md) | Ownership, scoped synchronization, configuration and recovery boundaries. |
| [CD: client development](../../contracts/client-development.v1.md) | How parallel implementations change together without sharing internals. |
| [State scope map](state-scopes.md) | Concrete examples of shared, host-local and client-local state. |
| [Baseline assessment](assessment.md) | Pinned historical observations and separately recorded implementation progress. |
| [Development plan](development-plan.md) | Ordered work, existing owners, acceptance journeys and change template. |
| [Architecture delivery plan](../architecture/delivery-plan.md) | Current proposed migration lanes and acceptance gates; supersedes D1–D7 as the forward sequence. |
| [HP / AP / WS / CB](../architecture/README.md#read-the-packet) | Host, agent, working-set and component contracts. |

The [existing wire contract](live-sessions.md) remains the reference for current
endpoint names, payloads, retry identity and snapshot behavior. The
[TUI handoff](tui-handoff.md) remains the transport integration guide. These drafts
describe additional behavioral goals, not undocumented capabilities in wire v1.

## One home for each kind of decision

| Boundary | Home | Consumers |
| --- | --- | --- |
| Shared client experience/state | This contract set, initially in Unified | Web, connected TUI, future native clients, agent adapters |
| Target AHP/ACP wire semantics | Upstream specifications; HP/AP delegate to them | Every target client, host and agent adapter |
| Legacy HTTP/SSE and action schemas | Unified wire guide and service schemas during migration | Existing clients |
| Storage/configuration/ownership mechanisms | Foundation's APIs and contracts | Unified, standalone CLI/TUI, other hosting apps |
| Execution admission, lifecycle, update generation | Unified host/runtime contracts | Connected clients and runtime integrations |
| Terminal rendering, scrollback, keybindings | TUI's own vision/contracts | TUI frontends and its host adapter |
| Web layout and widgets | Proposed separate web client repo | Browser/embedded-web experiences |
| Current implementation and release status | Assessment, work plan and validation records | Maintainers and release reviewers |

This is the bootstrap home of the product contracts. The [repository map](../architecture/repositories.md)
assigns the target owners; upstream law stays upstream and extension schemas get
one small owner. Until extraction, consumers reference the reviewed Unified commit
and retain their own direction documents. No duplicate normative copy is required.

## Historical references and refreshed method

The 2026-10-02 packet refreshed Converge and its related repos; exact revisions are
in [the source manifest](../architecture/evidence/upstream-revisions.json). It uses
small explicit promises, one rule per home, bounded lane briefs and independent
seam checks. These detailed drafts create no formal conformance ledger entries.
The earlier references below explain the September packet, not latest source.

Converge Method contributes the distinction between destination, observable
promises and evidence; explicit document state; stable clause references; and
one home for a rule. Its exact line budgets, session topology, stewardship roles,
and formal ledger machinery are not automatically adopted here.

Cortex contributes an example of how specific an experience contract can be.
Its product rules, surface categories and document organization are not the
template for Amplifier. The journeys in CX come from Amplifier's requirements.

The family guidance contributes ownership boundaries and explicit change impact
across independently developed parts. Compatibility/adoption records below are
our proposed application of those ideas, not a claim that its current format
already defines every cross-client versioning rule.

Reference revisions reviewed:

- [Converge Method](https://github.com/bkrabach/amplifier-converge-method/tree/d99c2a56f5ff40a4db23099a8e2959062e39aaf5),
  especially [documents](https://github.com/bkrabach/amplifier-converge-method/blob/d99c2a56f5ff40a4db23099a8e2959062e39aaf5/contracts/documents.v1.md).
- [Converge family](https://github.com/bkrabach/amplifier-converge/tree/763f5a75cfecdcb2a0464b00d1117f7199e121c2),
  especially the family and experience-interface contracts.
- [Cortex client experience](https://github.com/bkrabach/cortex-contracts/blob/6f0386989b13361cac2b9f169df27732825b5aa8/contracts/client-experience.v1.md).

## Adoption and decisions

The review should settle the actual promises and first-release capability floor,
then record the accepted contract revision in each consumer. Implementers may
prepare reversible adapter work while drafts are discussed, but may not infer
new approved product semantics from it.

The [current plan](../architecture/delivery-plan.md) records the remaining
capability, compatibility and measured-budget decisions. The original assessment
and D1–D7 plan remain historical evidence rather than today's missing-work list.
