# Contributor operating guidance

For source discovery, use the [source and ownership map](docs/source-context.md).
`python scripts/source_context.py` summarizes tracked inputs without reading
their bodies; select relevant owners, contracts and tests before expanding context.

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

## Make performance changes reviewable

For changes to repeated reads, publication, persistence, runtime preparation or
provider requests/cache reporting,
follow [performance validation](docs/validation/performance.md). Identify the
canonical owner, triggering operation and inputs that grow before choosing an
optimization. Review work done before filtering or sending a response, including
history reads, copies, scans and encoding. Retain a focused regression check for
the demonstrated cost and the affected history, authority and recovery semantics.
Use measured workload budgets; this does not add a full-suite release gate.

## Keep the development release loop fast

Every release needs an authored What's new entry, including fixes-only and
maintenance releases. Before choosing a version or preparing a release PR,
follow [version choice and release notes](docs/validation/release-qualification.md#version-choice-and-release-notes).
The release owner checks the shipped changes and high-impact guidance; a version
bump and passing format validator alone are not a complete release summary.

User direction, October 14, 2026: keep the foreground development-to-release loop
to a few minutes. Qualify affected behavior locally in the owned DTU, retaining
exact-source evidence and explicit limits. Aim for 1–3 minutes of focused checks
and about five minutes for the normal local budget. Run relevant browser/runtime
smoke tests; do not make the full local suite a merge or release prerequisite.
Use specific pre-release checks for material risks such as destructive migrations
or changed authorization, instead of automatically running every unrelated test.

Keep publication on the short package/frontend/native-runtime path, targeting
roughly two minutes when warm. Broad readiness, full Python and extended browser
coverage runs automatically after publication and reports failures for follow-up.
Do not add exhaustive PR/main/publication gates without an explicit decision and
an update to [the release policy](docs/validation/release-qualification.md).
Published does not mean post-release checks passed. Investigate failures and ship
corrections; never move published tags or replace released bytes to hide a failure.

Full suites, broad browser/platform coverage and stress tests run in the background.
Delegate their monitoring and triage and continue foreground work as soon as the
release is published. Report actionable changes, not routine polling. Fold failures
into the next release when one is in progress, or address them directly when idle;
newly discovered urgent regressions still warrant prompt attention. Reuse verified
qualification only when the exact tested inputs match the candidate being shipped;
changed inputs require the affected checks again.

## Bundled client and server evolve together

User direction, October 14, 2026: the web client ships with the server. Change
bundled interfaces together and remove superseded bundled paths; do not add
old-bundle compatibility fallbacks or capability negotiation. Independently
released clients, custom components, and explicit public APIs have their own
contracts and version policies. Preserve or deliberately revise those contracts
with their owners instead of silently changing their promised behavior.

## Interactive archive boundary

Keep archive feedback/navigation in the bundled interactive controls, not the
public `session.archive` action. Qualify late-response navigation races, the
noncurrent-chat path, Undo, and the raw action's selection-preserving behavior.
Release the interactive mutation lock on its confirmed receipt, independently of
navigation. Qualify stalled navigation with usable Undo and superseding intent.

## Native Windows work

Native Windows backend support is not yet qualified. Follow the
[setup design](docs/design/native-windows-installation.md) and keep WSL evidence
separate. Never use Windows `os.kill(pid, 0)` for passive liveness; it can terminate
the process. `tests/test_generation_leases.py` includes an unmocked sentinel.
The focused platform workflow selects passive liveness, shared/exclusive repair
locks, bounded UTF-8 worker pipes, private files, owned-process containment and
offline installer fixtures, not full Windows installation. Windows preflight
requires ordinary x64 CPython 3.13 and both PowerShell 5.1 and 7. A separate
focused job checks private configuration, TLS, read-only TokenUser identity and
service policy, plus the real CLI-to-service root binding through a fake native
transport. It uses pytest, cryptography, PyYAML, filelock and binary-only aiohttp;
it explicitly excludes synthetic Task Scheduler registration. Inspect named native
cases and skip reasons in the candidate's CI logs; partial passes do not qualify the host.
Keep its
worker subprocess probes isolated with `-I -S`: installed application packages
must not hide import dependencies in this standard-library check. A cancelled
Windows stdin read stops delivery; its daemon thread/descriptor may remain until
EOF or worker-process exit. Do not join it or close its buffered stream from the
event loop.

Author login-page HTML and standalone scripts in `frontend/public/`, not only
`amplifier_web/static/`. Vite replaces the generated static directory during
builds. Verify canonical/generated byte parity and the served routes after
rebuilding; a source-only login check does not prove packaged browser entry.

Keep the Windows owner-browser credential gate and installer activation gate
closed until their native and combined-flow prerequisites pass. Launcher HTTP
health JSON is not listener authentication: require the private local leaf pin
and HTTPS before reading or sending a control credential. The standalone process
guard supports ordinary CPython 3.13 only and requires explicit
`sys._base_executable`; do not weaken PID checks to accept a venv redirector.
Task Scheduler expands percent-bearing executable/working-directory paths.
Reject those paths rather than recording a literal identity that launches a
different program. Task registration/state is not authenticated host health.
