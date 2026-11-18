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

[The repository map](../docs/architecture/repositories.md) proposes names and owners.
[CD](client-development.v1.md) owns evidence discipline and coordinated changes.
[Repository access](repository-access.v1.md) governs public downloads, scoped
anonymous retries and authenticated mutations across component boundaries.

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
   Broken: two packages disagree on the same permission or receipt. Affected: implementers and reviewers.

4. **CB4 — Qualify real substitutions.** Publish exact tested client/host/agent and
   capability combinations, separating fixtures, actual peers and deployed use.
   A green default stack alone never establishes an interchangeable component.
   Broken: an adapter README is reported as passing runtime compatibility. Affected: community adopters.

5. **CB5 — Keep releases composable.** Components version and release independently.
   Unified publishes an exact tested default set and absorbs installation/assembly
   prerequisites. Users can configure alternatives through public interfaces. Report
   actual selected versions, compatibility limits and how to return to the default;
   an alternative is not automatically part of its qualification. Updates follow
   qualification and safe activation, preserving prior receipts and newer user work.
   Broken: independent releases force manual source assembly, a private allowlist
   prevents substitution, or a source update is confused with a freshly loaded worker.
   Affected: operators and component developers.

6. **CB6 — Isolate implementation lanes.** Each lane names its writable repo/paths,
   public producer/consumer seam, dependencies and falsifiable acceptance before
   integration. Independent work uses owned checkouts, state roots and service ports.
   Broken: agents overwrite shared contracts or restart another lane's service. Affected: parallel contributors.

## Current scheduling delivery

Scheduling uses artifact assembly: its selected published wheel supplies a
generated runtime snapshot in Unified. The public
[amplifier-platform-scheduling](https://github.com/bkrabach/amplifier-platform-scheduling)
repository owns the policy/store source and standalone tests. Unified is the
sole installed distribution owner and carries the selected artifact provenance,
license and runtime requirements. Component source/test changes can proceed without
changing the app default; source handoff does not rewrite historical selected
receipts. This does not claim independent pip replacement; returning
to a prior app uses ordinary app rollback and preserves newer scheduling state.

## Current workspace metadata delivery

[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces)
owns the registration/query mechanism and direct tests. Unified selects its
published artifact and remains the sole installed owner. Filesystem effects,
workspace IDs, authority, persistence and conversation history retain their
existing host owners; this handoff does not extract the full workspace domain.
Publication and artifact selection remain separate, preserving original receipts.

## Current operation mechanism delivery

[amplifier-platform-operations](https://github.com/bkrabach/amplifier-platform-operations)
owns operation observations, delivery cursors and direct tests. Unified selects
its published artifact and remains the sole installed owner. The host retains
admission, permissions, Stop, cancellation and execution policy. Source handoff
and publication do not rewrite historical receipts or refresh the selected app
default; returning to prior app code preserves newer operation state.

## Current Worktrees mechanism delivery

[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees)
owns the Git mechanism and direct tests. Unified selects its published artifact
and remains the sole installed owner; host permissions, active work, handoff and
cleanup policy retain their existing owners. Portability's published dependency
receipt must match the selected Worktrees receipt exactly. This moves no user
files or ownership/transfer records and does not extract the whole resource
lifecycle domain. Source publication and app adoption remain separate.

## Current output mechanism delivery

[amplifier-platform-resources](https://github.com/bkrabach/amplifier-platform-resources)
owns output relationships, saved-body decoding, read-only Git evidence and direct
mechanism tests. The package keeps its `amplifier-outputs` distribution and
`amplifier_outputs` import. Unified selects the published artifact as the sole
installed owner and retains authorization, transactions, source access, content
storage, integrity and retention. This transfers no user data and does not extract
the broader resource/media lifecycle. Publication and app selection stay separate;
historical receipts and saved-body formats retain their existing meaning.

## Current Recall storage delivery

[amplifier-platform-recall](https://github.com/bkrabach/amplifier-platform-recall)
owns conversation search, versioned-memory storage and direct SQLite tests. The
package keeps its `amplifier-recall` distribution and `amplifier_recall` import.
Unified selects its published artifact as the sole installed owner and retains
source access, scope, authorization, opt-ins, context assembly, model execution
and Stop. Durable notes, versions, settings, command receipts and suppression
records retain their existing formats and recovery behavior. This handoff moves
no user data and does not replace native history or Foundation's separate optional
Recall API. Publication and app selection remain separate; historical receipts
and newer user state survive ordinary app rollback.

## Current Status and Kernels delivery

[amplifier-platform-status](https://github.com/bkrabach/amplifier-platform-status)
and [amplifier-platform-kernels](https://github.com/bkrabach/amplifier-platform-kernels)
own their source, direct mechanism tests and independent publication. Unified
selects their original public artifacts and retains host enablement, permissions,
process lifecycle and recovery. Status's public import/CLI remain separate from
Unified's private selected copy, preserving coinstallation and uninstall isolation.
Unified is the sole installed owner of its Kernels copy; ACP's separately selected
artifact is unchanged. No user records or active work move with these authoring
handoffs. Publication, app selection and live adoption remain distinct.

## Current shell SDK delivery

[amplifier-shell-sdk](https://github.com/bkrabach/amplifier-shell-sdk) owns the
public JavaScript API, types, direct package tests and controls example. Unified
selects the original public tarball and qualification receipt, preserving the
`packages/shell-sdk` and `examples/shell-controls` compatibility paths as generated
delivery inputs. Ordinary wheel, editable and Git-free source builds verify these
selected bytes offline; installing Unified does not require Node or npm.
Unified retains host/browser integration, authority and trusted-component policy.
Client source locks must select the same three SDK build inputs; no lock bypass
or second SDK author is introduced. Component publication and app adoption remain
separate. See [SDK delivery](../docs/shell/sdk-delivery.md).

## Current Publishing mechanism delivery

[microsoft/amplifier-publishing](https://github.com/microsoft/amplifier-publishing)
owns immutable static releases, private serving, lifecycle receipts and direct
mechanism tests. Unified selects its published artifact as the sole installed
owner and retains task scope, authorization, policy and remote-target integration.
The existing public Python API and Unified's durable store formats, optional
retention, pins, reader settlement, expired-body history and unknown-outcome
recovery remain supported. Selection moves no user data, enables no policy and
replays no effect. Historical publication receipts remain valid; source acceptance,
standalone publication, app selection and live adoption are distinct.

## Not in v1

Mandatory microservices or a new central runtime framework; promote only when a
measured isolation or deployment requirement warrants the additional boundary.

## How the kit checks it

- CB1/CB2: boot each component with fixture peers; enforce dependency/import checks.
- CB3: resolve contract references and generated-schema provenance.
- CB4: execute both directions of external interoperability qualification.
- CB5: install the packaged default without sibling checkouts, substitute a component
  through its public interface, record actual package/process/worker versions, and
  return to the default while preserving newer data and uncertain effects.
- CB6: review the lane brief and rerun its seam test outside the implementing agent.

## Open questions

Which optional capabilities should be extracted in the first wave versus after the core seam?
Which existing packages already own a suitable implementation, avoiding duplicate services?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-07 | Clarify independent component releases, tested default composition and configurable alternatives. | Product-owner direction; [repository map](../docs/architecture/repositories.md). Existing bundled compatibility remains until a boundary is deliberately extracted. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial CB1–6. | [Refreshed Converge references](../docs/architecture/evidence.md). |
