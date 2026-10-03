# Component boundaries — v1 (RATIFIED 2026-10-02)

Clause prefix: **CB**. Parent: [family vision](../docs/architecture/VISION.md).
Governs how independently owned repositories compose into Amplifier Unified.

## Who builds against this

Client, host, agent, capability and distribution maintainers; integration and release owners.

## What it is

Components have public boundaries, standalone development entrypoints and explicit
adoption evidence. Repository count does not prescribe deployment process count.

```text
component release -> public interface + fixture peer + compatibility record
distribution -> selected component releases + tested integration + adoption receipt
implementation lane -> owning repo/paths + clause + falsifier + independent check
```

[The repository map](../docs/architecture/repositories.md) records approved names and owners.
[CD](client-development.v1.md) owns evidence discipline and coordinated changes.

## The promises

1. **CB1 — Make development independent.** Each client, host and agent adapter can
   start against a public fixture peer without another component's checkout or live
   account. Optional capability absence has a documented ordinary behavior.
   Broken: editing the TUI requires starting the web app and its private runtime. Affected: parallel teams.

2. **CB2 — Enforce dependency direction.** Connected clients depend on protocols and
   client libraries; the generic host depends on agent protocols; native internals
   stay behind agent adapters. Optional domains never become host core dependencies.
   Broken: a generic host imports an Amplifier loop or Converge-specific model. Affected: substitute implementations.

3. **CB3 — Give each rule one home.** Reference upstream protocol semantics and one
   owner for each Amplifier extension, storage format or product promise. Generated
   types may travel across repos; copied normative definitions do not.
   Each repository has a concise local vision and discoverable contract entry point
   for its own boundary, tracing inherited direction to the family or ecosystem
   authority. Public promises link to falsifiers and executable acceptance; current
   gaps and qualified artifacts live separately. A generic library's vision remains
   useful outside Unified. Documentation structure may vary without duplicating rules.
   Broken: two packages disagree on the same permission or receipt. Affected: implementers and reviewers.

4. **CB4 — Qualify real substitutions.** Publish exact tested client/host/agent and
   capability combinations, separating fixtures, actual peers and deployed use.
   A green default stack alone never establishes an interchangeable component.
   Broken: an adapter README is reported as passing runtime compatibility. Affected: community adopters.

5. **CB5 — Keep releases composable.** Components version and release independently.
   The distribution tracks supported latest releases through qualification and safe
   activation, preserving prior receipts for rollback rather than standing pins.
   Broken: source currency is confused with a freshly loaded worker. Affected: operators.

6. **CB6 — Isolate implementation lanes.** Each lane names its writable repo/paths,
   public producer/consumer seam, dependencies and falsifiable acceptance before
   integration. Independent work uses owned checkouts, state roots and service ports.
   Broken: agents overwrite shared contracts or restart another lane's service. Affected: parallel contributors.

7. **CB7 — Compose actual maintenance coverage.** Every configured effect owner
   supplies its own intake/lifetime participant. The distribution derives the
   required coverage; callers cannot declare an omitted owner idle. Follow
   [MA](maintenance.v1.md) for held proofs, process replacement and recovery.
   Broken: an update restarts over a live publisher or native transfer process.
   Affected: operators and all accepted work.

8. **CB8 — Exercise failure at the boundary.** A component's standalone success
   and a distribution's successful composition are separate claims. Consumers
   retain the complete producer contract, including process working directory,
   configuration ownership, failure classification and recovery obligations.
   Failures discovered during composition update the responsible owner's contract
   and tests; a private integration workaround is not evidence of interchangeability.
   Broken: every package passes its own tests while startup loses a launch field
   or a failed component has no supported recovery path. Affected: independent owners.

## Not in v1

Mandatory microservices or a new central runtime framework; promote only when a
measured isolation or deployment requirement warrants the additional boundary.

## How the kit checks it

- CB1/CB2: boot each component with fixture peers; enforce dependency/import checks.
- CB3: resolve contract references and generated-schema provenance.
- CB3: start from each repository's README/agent entry point and locate its desired
  boundary, authoritative contract, consumer obligations, falsifiers and current gaps.
- CB4: execute both directions of external interoperability qualification.
- CB5: record package, installed process and fresh worker versions during release.
- CB6: review the lane brief and rerun its seam test outside the implementing agent.
- CB8: run installed composition and failed-start recovery through actual platform
  parsers and owner APIs; record standalone, assembled and live outcomes separately.

## Open questions

Which optional capabilities should be extracted in the first wave versus after the core seam?
Which existing packages already own a suitable implementation, avoiding duplicate services?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-03 | Add CB8 complete boundary consumption and failure qualification. | Installed launch working-directory omission and live failed-start recovery gap exposed assumptions absent from successful component tests. |
| 2026-10-03 | Clarify CB3 local vision/contract traceability for every repository. | Repository audit found complete packets in Unified/TUI and scattered README/schema obligations elsewhere; local corrections assigned to owners. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial CB1–6. | [Refreshed Converge references](../docs/architecture/evidence.md). |
