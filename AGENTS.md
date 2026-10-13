# Contributor operating guidance

## Assess scope and design before taking on issues or PRs

User direction, October 13, 2026: an issue or PR is a proposal, not an automatic
commitment. Before implementing, sponsoring an upstream change, or calling it a
release prerequisite, verify that it affects Amplifier Unified's actual runtime
and dependencies. Shared naming is not evidence of dependency. In particular,
Amplifier Agent is separate from Unified's Core/Foundation runtime.

- Check the problem against `docs/architecture/VISION.md`, the applicable client
  vision, and the relevant contracts. Identify the canonical owner and evidence.
- Confirmed defects in promised behavior normally warrant straightforward fixes.
- New features and behavior or architecture changes require judgment about whether
  they are desirable, their general client needs, and the right design; do not
  accept the issue's proposed implementation by default.
- Any intentional change in direction must update the governing vision/contracts
  explicitly as part of the proposal before implementing the divergent behavior.
- Keep unrelated upstream improvements separate from Unified release gates.
  Close out-of-scope tracking and correct misleading reviews when discovered.

## Keep the development release loop fast

User direction, October 13, 2026: qualify affected behavior locally in the owned
DTU before requesting merge, retaining exact-source evidence and explicit limits.
Use the full local suite and real-runtime/browser checks for substantial
integrations. Do not defer routine defect discovery to release CI.

Keep publication on the short package/frontend/native-runtime path, targeting
roughly two minutes when warm. Broad readiness, full Python and extended browser
coverage runs automatically after publication and reports failures for follow-up.
Do not add exhaustive PR/main/publication gates without an explicit decision and
an update to [the release policy](docs/validation/release-qualification.md).
Published does not mean post-release checks passed. Investigate failures and ship
corrections; never move published tags or replace released bytes to hide a failure.
