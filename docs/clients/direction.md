# Shared-client direction and coordination

**Status: proposal for review.** This packet establishes an Amplifier-specific
destination, draft promises, a source assessment and an implementation sequence.
It does not ratify the contracts, implement a connected TUI, or change a release.

## Read the packet

| Document | Purpose |
| --- | --- |
| [Vision](VISION.md) | The experience we want across clients and devices. |
| [CX: client experience](../../contracts/client-experience.v1.md) | Observable journeys and common action meanings. |
| [CS: client state](../../contracts/client-state.v1.md) | Ownership, scoped synchronization, configuration and recovery boundaries. |
| [CD: client development](../../contracts/client-development.v1.md) | How parallel implementations change together without sharing internals. |
| [State scope map](state-scopes.md) | Concrete examples of shared, host-local and client-local state. |
| [Baseline assessment](assessment.md) | What the reviewed web and TUI sources support, and what remains unproven. |
| [Development plan](development-plan.md) | Ordered work, existing owners, acceptance journeys and change template. |

The [existing wire contract](live-sessions.md) remains the reference for current
endpoint names, payloads, retry identity and snapshot behavior. The
[TUI handoff](tui-handoff.md) remains the transport integration guide. These drafts
describe additional behavioral goals, not undocumented capabilities in wire v1.

## One home for each kind of decision

| Boundary | Home | Consumers |
| --- | --- | --- |
| Shared client experience/state | This contract set, initially in Unified | Web, connected TUI, future native clients, agent adapters |
| HTTP/SSE and public action schemas | Unified wire guide and service schemas | Every connected client |
| Storage/configuration/ownership mechanisms | Foundation's APIs and contracts | Unified, standalone CLI/TUI, other hosting apps |
| Execution admission, lifecycle, update generation | Unified host/runtime contracts | Connected clients and runtime integrations |
| Terminal rendering, scrollback, keybindings | TUI's own vision/contracts | TUI frontends and its host adapter |
| Web layout and widgets | Unified frontend | Browser/embedded-web experiences |
| Current implementation and release status | Assessment, work plan and validation records | Maintainers and release reviewers |

Shared contracts can move to a dedicated repository when there are independent
consumers maintaining adoption records. Creating that repository is not a
prerequisite for the first adapter. Until then, TUI references the reviewed
Unified contract commit and retains its own DRAFT direction documents.

## What we learned from the references

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

The [assessment](assessment.md) and [plan](development-plan.md) identify the
remaining decisions: stop semantics, rich-artifact fallback, scoped subscription
evolution, portable controls and measurable performance budgets. None requires
copying a whole product specification or stopping unrelated ongoing work.
