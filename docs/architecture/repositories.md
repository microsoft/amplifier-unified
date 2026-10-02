# Repository and ownership map

Ownership map supporting [CB](../../contracts/component-boundaries.v1.md).
Names marked **new** identify the extraction boundaries. Fourteen targets now
have landed source; access to `amplifier-unified-capability-mcp` is unresolved.
See [implementation status](implementation-status.md) for the current acceptance boundary.
Use existing ownership and public APIs where they fit. Independent repositories
may publish libraries, executables or optional capabilities; not all are services.

## Naming and reuse

The `amplifier-unified-*` prefix marks product ownership. The Unified web and TUI
experiences, host, product extensions and integration qualification use it even
when they interoperate with third-party peers. Protocol compatibility alone does
not make a component product-independent. The user approved bringing the TUI
under this naming scheme. The user chose a new repository with a fresh history;
leave existing `amplifier-app-tui` intact as the standalone predecessor.

Standalone Amplifier ACP execution, native-session indexing and generic AHP
client mechanisms remain `amplifier-app-acp`, `amplifier-session-catalog` and
`amplifier-ahp-client-kit`. They must build and run without Unified internals.
The ACP adapter owns Amplifier-native extension schemas; the Unified extension
repository owns only product-specific capability semantics. Reusable libraries
likewise remain unprefixed, with their Unified adapters owned separately. A
library/package boundary does not require its own Git repository: the Foundation
consolidation recommendation below supersedes four entries in the original
19-repository creation list. Future reuse is not sufficient: independent public
inputs, outputs, authority and a standalone test must demonstrate the boundary.

## Core boundaries

| Repository | Disposition and responsibility | Public seam / owns | Must not depend on |
| --- | --- | --- | --- |
| `amplifier-unified` | Existing; become family/distribution, installer, integration docs and release composition | Component manifest, installation and adoption receipts; family vision | Private copies of client/agent implementation |
| `amplifier-unified-client-web` | **New**; extract browser from `frontend/` and relevant static/asset build | AHP client, browser persistence, UI, accessibility, optional renderers | Python service objects, native session files |
| `amplifier-unified-client-tui` | **New** connected client repository with fresh Git history; retain useful extracted source/provenance and leave `amplifier-app-tui` intact | Terminal interaction, local persistence and AHP; replace legacy CLI over time | Web rendering or native runtime in connected mode |
| `amplifier-unified-host` | **New** target owner; extract generic admission, AHP, ACP orchestration, authorization and lifecycle | AHP public server, ACP client, scoped host records | Amplifier core/loop imports, web widgets, Converge domain types |
| `amplifier-app-acp` | **New**; thin adapter around current native application runtime | ACP agent, native runtime composition, native extension schemas, ownership and identity mapping | Web client, generic host private objects |
| `amplifier-core`, `amplifier-foundation`, module/bundle repos | Existing; retain kernel and reusable native mechanisms | Native composition, persistence and ownership mechanisms | AHP UI concepts or product-specific catalogs |
| `amplifier-agent` | Existing separate building block; excluded from this migration | Its own independent product contracts | Native Unified execution; it is not our current or intended runtime |
| `amplifier-session-catalog` | **New**; extract discovery/index/query, including legacy writer hints | Rebuildable summaries, workspace existence and parent indexes; filtered paged reads | Agent initialization, transcript mutation, browser selection |
| `amplifier-unified-protocol-extensions` | **New**; small owner of Unified-specific negotiated schemas and generated bindings | Extension registry, permissions, versions, fallback fixtures | Forked upstream core protocol or UI implementation |
| `amplifier-ahp-client-kit` | **New**, only shared client mechanisms | AHP SDK integration, local intent/cache interfaces, optional renderer registration | Host/global app store; duplicated upstream reducers |
| `amplifier-unified-interop` | **New**; independent seam fixtures, load/recovery probes and real-peer qualification | Replayable fixtures, capability matrix and exact evidence manifests | Production runtime ownership or shared user state |

The host extraction must first compare the existing `amplifierd` daemon and current
Unified host boundaries. Reuse tested mechanisms that fit; do not establish two
competing generic hosts or silently declare existing REST/SSE to be AHP. The lane
records whether `amplifier-unified-host` is a new home or an agreed evolution of an existing
owner before moving code. Naming does not block contract/fixture work.

`amplifier-ahp-client-kit` can initially publish language-specific packages in one small
repo; split TS and Rust ownership if it helps teams. It owns recovery helpers and
client persistence contracts, not a framework that dictates all UI state. Upstream
SDKs keep their own schemas and reducers. Do not translate every action twice merely
to put an Amplifier name around it.

## Optional Unified capability owners

These are product capabilities exposed through negotiated host interfaces, not
Amplifier kernel modules. The repository convention is
`amplifier-unified-capability-<domain>`. Existing package names and command names
may remain stable; repository taxonomy does not require changing every import.

| Landing repository | Ownership | Keep together |
| --- | --- | --- |
| `amplifier-unified-capability-native` | Amplifier configuration, provider/bundle administration and runtime-control presentation | One optional bridge to the native application's public ACP extensions; no second native settings authority |
| `amplifier-unified-capability-resources` | Shared artifacts, immutable versions, Canvas and CanvasApps | Resource authority, app-state validation and effect receipts; renderers stay client-owned |
| `amplifier-unified-capability-media` | Voice signalling, transcript delivery, explicit visual capture and media leases | Permission scope and media lifecycle; browser devices remain client-owned |
| `amplifier-unified-capability-mcp` | MCP connections, OAuth/account bindings, Smart Tool lifecycle and MCP Apps | Saved tool/resource/grant identity and its authorization owner |
| `amplifier-unified-capability-operations` | Unified durable tasks, schedules, questions and execution coordination | Product task/stop/admission rules over reusable scheduling and operation libraries |

These owners are separate because they have independent authority, lifecycle,
storage and tests. Their TypeScript bridges and Python brokers stay in the same
owner repository; language alone is not a repository boundary. They need not run
as five always-hot services. Load work and selected state only when required.

## Reusable domain libraries

The current source already has these application-independent library boundaries.
Extract public APIs, contract tests and provenance; keep Unified admission/UI
policy in a product capability or its distribution adapter.

| Landing repository | Existing library | Public boundary |
| --- | --- | --- |
| Foundation optional `amplifier-scheduling` library | `amplifier_scheduling/` | Recurrence calculation, schedule revisions and durable run claims; callers supply execution, permission and product policy |
| Foundation optional `amplifier-operations` library | `amplifier_operations/` | Operation journals, output cursors and coordination records; no native runtime or client state |
| `amplifier-publishing` | `amplifier_publishing/` | Immutable static releases, reviewed publication and transport receipts |
| Foundation optional `amplifier-worktrees` library | `amplifier_worktrees/` | Git checkout lifecycle and retained source/ownership evidence; caller supplies session handoff and cleanup authority |
| `amplifier-portability` | `amplifier_portability/` | Explicit paired-host transfer and reconciliation; depends on declared worktree APIs |
| Foundation optional `amplifier-recall` library | `amplifier_recall/` | Derived search and versioned memory storage; consent, consolidation policy, retention choices and model budgets remain with callers |

The four optional libraries landed in `amplifier-foundation` in
[PR418](https://github.com/microsoft/amplifier-foundation/pull/418), followed by
[PR419](https://github.com/microsoft/amplifier-foundation/pull/419) preserving
Recall native-history authorization evidence. Each has scoped contracts/tests and
an independently installable package. Existing package/import APIs remain stable. Do not add eager imports, mandatory runtime
dependencies, database creation, index scans or background services to the base
Foundation import. The landed Recall library is mechanism-only; the Unified Recall owner supplies
opt-in personalization and consolidation policy. Independent installed consumers
exercise the four libraries without importing the base Foundation package.

Parallel work uses separate worktrees and directory ownership. One integrator
owns shared manifests and release files; per-library tests and path-scoped CI
remain independent. Product-specific adapters still live in Unified. Publishing
and portability retain separate repositories because they own distinct deployment
and paired-host protocols, dependencies and compatibility surfaces. The catalog
retains its independently replaceable service/protocol boundary; common native
format/history mechanisms should be reused from Foundation rather than duplicated.

A CLI included with a library does not by itself make the repository an app.
These names do not claim kernel-module or behavior-bundle compatibility. A future
mountable tool/hook or composable behavior should use the appropriate module or
bundle type and depend on the suitable public boundary rather than rename its
underlying library. Do not create vendor-specific ACP adapters merely to wrap an
existing maintained compatible adapter; interop owns qualification first.

## Naming evidence and creation list

Reviewed upstream on 2026-10-02: Amplifier
[`docs/MODULES.md`](https://github.com/microsoft/amplifier/blob/33b75749920472257d1d85d20a5699ecf9d1ece7/docs/MODULES.md),
[`docs/REPOSITORY_RULES.md`](https://github.com/microsoft/amplifier/blob/33b75749920472257d1d85d20a5699ecf9d1ece7/docs/REPOSITORY_RULES.md),
and Foundation's
[ecosystem map](https://github.com/microsoft/amplifier-foundation/blob/0d4fb2792aa93454413a82451ad9a4cea9b2cbcf/context/amplifier-dev/ecosystem-map.md)
and [bundle guide](https://github.com/microsoft/amplifier-foundation/blob/0d4fb2792aa93454413a82451ad9a4cea9b2cbcf/docs/BUNDLE_GUIDE.md).
The catalog contains historical naming exceptions; the recommendation is a
consistent taxonomy for new work, not a claim that every existing repo follows it.

- `amplifier-app-*`: standalone applications composing the native runtime.
- `amplifier-module-<type>-*`: kernel-mounted extensions following module contracts.
- `amplifier-bundle-*`: composable agent configuration/behaviors, optionally with supporting code.
- `amplifier-<library>`: reusable application libraries and protocol helpers.
- `amplifier-unified-*`: the established product family namespace; retain it for Unified-specific owners.

**Approved boundary: 15 repositories under `microsoft`:** the eight new core repositories,
the five Unified capability repositories, and `amplifier-publishing` plus
`amplifier-portability`, including a new `amplifier-unified-client-tui`. Hold the
four Foundation library repository creations described above. Leave existing
`amplifier-app-tui` intact. **Keep** existing `amplifier-unified` as distribution and family
vision/contracts. Repository creation is not evidence of completed extraction,
feature parity, package publication or deployment.

Do not split Canvas from its resources authority, MCP Apps from MCP authorization,
each provider from native administration, or TypeScript/Python halves of the same
capability merely to increase repository count. Do keep clients, execution,
indexing, protocol envelopes and independent interop qualification separate.

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
