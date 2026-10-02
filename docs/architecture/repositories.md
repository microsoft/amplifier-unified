# Repository and ownership map

Proposal supporting [CB](../../contracts/component-boundaries.v1.md).
Names marked **new** are proposed, not repositories created by this packet.
Use existing ownership and public APIs where they fit. Independent repositories
may publish libraries, executables or optional capabilities; not all are services.

## Core boundaries

| Repository | Disposition and responsibility | Public seam / owns | Must not depend on |
| --- | --- | --- | --- |
| `amplifier-unified` | Existing; become family/distribution, installer, integration docs and release composition | Component manifest, installation and adoption receipts; family vision | Private copies of client/agent implementation |
| `amplifier-client-web` | **New**; extract browser from `frontend/` and relevant static/asset build | AHP client, browser persistence, UI, accessibility, optional renderers | Python service objects, native session files |
| `amplifier-app-tui` | Existing separate repo; evolve connected backend to AHP | Terminal interaction, local persistence and AHP; replace legacy CLI over time | Web rendering or native runtime in connected mode |
| `amplifier-host` | **New** target owner; extract generic admission, AHP, ACP orchestration, authorization and lifecycle | AHP public server, ACP client, scoped host records | Amplifier core/loop imports, web widgets, Converge domain types |
| `amplifier-agent-acp` | **New**; thin adapter around current native application runtime | ACP agent, native runtime composition, ownership and identity mapping | Web client, generic host private objects |
| `amplifier-core`, `amplifier-foundation`, module/bundle repos | Existing; retain kernel and reusable native mechanisms | Native composition, persistence and ownership mechanisms | AHP UI concepts or product-specific catalogs |
| `amplifier-agent` | Existing; separate candidate engine behind the ACP adapter after parity evaluation | Its own frozen embedding/engine contracts | A claim that its current API already preserves every modular runtime feature |
| `amplifier-session-catalog` | **New**; extract discovery/index/query, including legacy writer hints | Rebuildable summaries, workspace existence and parent indexes; filtered paged reads | Agent initialization, transcript mutation, browser selection |
| `amplifier-protocol-extensions` | **New**; small owner of Amplifier-only negotiated schemas and generated bindings | Extension registry, permissions, versions, fallback fixtures | Forked upstream core protocol or UI implementation |
| `amplifier-client-kit` | **New**, only shared client mechanisms | AHP SDK integration, local intent/cache interfaces, optional renderer registration | Host/global app store; duplicated upstream reducers |
| `amplifier-interop` | **New**; independent seam fixtures, load/recovery probes and real-peer qualification | Replayable fixtures, capability matrix and exact evidence manifests | Production runtime ownership or shared user state |

The host extraction must first compare the existing `amplifierd` daemon and current
Unified host boundaries. Reuse tested mechanisms that fit; do not establish two
competing generic hosts or silently declare existing REST/SSE to be AHP. The lane
records whether `amplifier-host` is a new home or an agreed evolution of an existing
owner before moving code. Naming does not block contract/fixture work.

`amplifier-client-kit` can initially publish language-specific packages in one small
repo; split TS and Rust ownership if it helps teams. It owns recovery helpers and
client persistence contracts, not a framework that dictates all UI state. Upstream
SDKs keep their own schemas and reducers. Do not translate every action twice merely
to put an Amplifier name around it.

## Additional useful extractions

These are concrete follow-on boundaries, not reasons to delay the first protocol
slice. Each becomes its own repo when its seam fixture is written; preserve an
existing suitable repo if the inventory finds one.

| Candidate owner | Current source / existing overlap | Boundary and independence test |
| --- | --- | --- |
| `amplifier-resources` (**new**) | `resource_files.py`, attachments, `amplifier_outputs/`, artifact storage | Stable resources, content/metadata access, version checks and auth; retrieve a large artifact without loading its agent |
| `amplifier-canvas` (**new**) | Canvas bridge/documents/views, frontend renderers and shell integration | Capability package and renderer SDK; ordinary chat works with it absent, agents invoke the same public operations |
| Voice/device capability owner | `voice*.py`, device dispatch; existing `amplifier-voice`, browser bridge | Reuse/evolve existing repos after comparison; media transport separate, exactly targeted permission and lifecycle |
| Configuration/composition owner | Shared settings, bundles and provider discovery; Foundation overlap | Native mechanisms stay Foundation; extract product admin/catalog UI capability, not a second settings authority |
| `amplifier-scheduling` (**new extraction or existing owner reuse**) | `amplifier_scheduling/`, schedules; compare `amplifier-drumbeat` | Durable schedule/trigger ownership and host admission interface; work runs with zero viewers |
| `amplifier-operations` (**new extraction**) | `amplifier_operations/`, durable questions/tasks | Durable operations API and capability; independent of a particular agent engine and frontend |
| `amplifier-publishing` (**new extraction**) | `amplifier_publishing/` | Publishing library/capability and resources; own tests without an app boot |
| `amplifier-worktrees` (**new extraction**) | `amplifier_worktrees/` | Workspace/worktree lifecycle library with explicit ownership; independent CLI/host consumers |
| `amplifier-portability` (**new extraction**) | `amplifier_portability/` | Explicit transfer/inspection and receipts; never implicit cross-engine conversion |
| Recall/history capability owner | `amplifier_recall/`, native history readers; Foundation and catalog overlaps | Search/read-only enrichment; index loss never changes native resume |
| ACP client bridge owner | Reuse an existing compatible bridge or propose a small separate repo | ACP server facing an editor, AHP client facing the host; no second history/authority, capability loss documented |
| Converge family repos | Existing Method, Agents, Queue, Service, Onramp, Direction, Operation, Experience SDK and bundles | Optional domain capabilities through generic host/client seams; no host imports of Converge-specific workflows |

A package is ready to extract when it can state its input/output contract, data
authority and independent fixture. Small size is not an objection. An extraction
that still imports a sibling's private source has not completed its boundary.

## Dependency direction

```text
distribution --> released components and compatibility manifest
web/TUI --> upstream AHP SDK + optional client helpers/renderers
host --> upstream AHP server behavior + ACP client + catalog/resource interfaces
Amplifier ACP adapter --> native runtime/Core/Foundation/modules/bundles
optional capability --> published host/resource/client-extension interfaces
legacy CLI --> native persistence + transitional discovery hint
interop --> public interfaces only
```

Keep native metadata naming/configuration authority in one place. The host's
display projection or catalog must not become another writer of a session's
canonical name. Make commands to change native facts go through their owning
adapter; then update index/projections from the confirmed result.

## How parallel work becomes practical

Each core repo ships a one-command local entrypoint, fixture peer, contract
revision, negotiated capability list and a minimal integration scenario. Local
development assigns an owned state directory and ports, with no shared checkout
edits. Core runtime tests use a fake model; actual peer/account tests are a
separate evidence tier. Contracts and extension schemas have one change owner.

The distribution consumes released artifacts rather than reaching into adjacent
checkouts. Cross-repo changes can carry candidate package references in isolated
integration receipts. Record actual versions at installation and fresh worker
start. Track latest qualified components and preserve previous locks as rollback
receipts; never confuse a source SHA with installed adoption.
