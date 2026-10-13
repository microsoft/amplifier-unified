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
