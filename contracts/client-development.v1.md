# Shared client development — v1 (DRAFT)

Clause prefix: **CD**. Parent: [shared-client vision](../docs/clients/VISION.md).
This proposes coordination for shared behavior, without importing another project's governance.

## Who builds against this

Unified, web, TUI, Foundation and future client maintainers; product owners and reviewers.

## What it is

```text
Shared change -> clause + affected consumers + compatibility plan
  -> owned implementation slices -> shared acceptance journey
  -> per-client evidence -> release/adoption record
Internal change -> existing promises + local checks -> ordinary review
```

[The development plan](../docs/clients/development-plan.md) holds assignments and current work.
The contracts hold the promises; [the assessment](../docs/clients/assessment.md) holds observations.

## The promises

1. **CD1 — Give shared meaning one home.** This contract set owns cross-client promises;
   the wire document owns API spelling, Foundation owns shared mechanisms, and each
   client owns its internals. Consumers reference a reviewed revision instead of copying the rules. Draft publication is not ratification.
   Broken: two repos redefine Stop differently or a draft is reported as an agreed standard. Affected: client builders and product owners.

2. **CD2 — Declare behavioral impact before implementation diverges.** A shared semantic
   change identifies the affected clauses, consumers and observable acceptance before
   integration. Product approval settles changed promises; routine fixes within them use ordinary review and need no all-team approval.
   Broken: a server rollout silently changes another client's action or all internal work waits on a global meeting. Affected: parallel maintainers.

3. **CD3 — Make mixed versions safe.** Each client records the host/protocol versions
   and optional capabilities it supports. Additive fields remain tolerable; unsupported
   actions are unavailable explicitly. Breaking behavior needs a version/adoption plan and a documented minimum compatible release.
   Broken: an old client sends a destructive fallback or "latest" substitutes for tested compatibility. Affected: independently updated devices.

4. **CD4 — Assign work without stealing ownership.** Every shared work item names its
   source clause, affected files/repositories, integration owner and completion evidence.
   Existing active ownership is checked before overlap; dependencies can proceed independently and handoffs cite exact revisions.
   Broken: two tasks overwrite the same contract or one team's pending work is announced as another's delivery. Affected: parallel teams.

5. **CD5 — Keep evidence specific and falsifiable.** Assess each promise against named
   revisions and scenarios. Distinguish source inspection, fixture tests, actual client
   use and released-host checks; missing or skipped evidence stays visible. A reviewer reruns relevant checks or states what remains unverified.
   Broken: a transport fixture proves a TUI UX, or a draft gets a formal pass without adoption. Affected: reviewers and users relying on readiness.

6. **CD6 — Separate merge, release and adoption.** A shared change records its contract
   revision, compatible host/client builds, migrations and remaining limitations. Release
   owners coordinate dependencies and verify the actual deployed versions before claiming completion.
   Broken: a merged PR is announced as running on a user's device. Affected: operators and people testing the change.

## Not in v1

A required new repository, mandatory agent/session topology, a family-wide approval
for every PR, or enforcement of Converge Method's own operational rules in other repos.

## How the kit checks it

Review an example shared change using the [impact record](../docs/clients/development-plan.md).
Check references, consumer revisions, ownership and evidence at integration/release review.
These draft documents alone add no CI enforcement or formal verdict ledger.

## Open questions

When should the shared contract set move into an independent repository?
Which compatibility checks should become required CI as connected clients advance?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-09-20 | Initial draft, CD1–6. | Converge Method principles adapted to existing Amplifier ownership. |
