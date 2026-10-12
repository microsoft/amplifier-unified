# Runtime preparation diagnostics

First-send preparation is separate from loading a saved conversation and from
waiting for a model response. A cold configuration may need dependency
preparation and compatibility checks before a worker can accept its first input.
The existing progress messages describe those stages; they are not delivery
acknowledgements.

Each newly prepared profile retains a private `profile-attempt.json` in its
generation receipt directory. It now records monotonic durations, in milliseconds,
for preflight, waiting for the configuration lock, the attempt overall, and these
phases:

- Source snapshot
- Runtime preparation
- Installer policy
- Dependency preparation
- Runtime freeze, including its individually recorded installation operations
- Compatibility probe
- Verification against the recorded installation

Each phase includes its start time and running, succeeded, failed or cancelled
status. Failed phases record the exception type. The receipt does not add command
arguments, environment values, output bodies or conversation text. Existing
private paths and source identifiers remain private diagnostic information.

Runtime freeze includes its nested operations: **do not sum all phase durations**.
The attempt duration is the enclosing interval. A process crash may leave a phase
marked running; that is a historical receipt, not proof that a process is alive.
A cached profile retains its original receipt, unchanged. These timings describe
qualification, not each later cache hit or first-token latency.

## Startup cleanup ownership

Before registration, the startup coroutine owns any spawned process and its
generation lease. Awaited preparation-heartbeat teardown belongs inside that
ownership boundary: cancellation there must reap the process before releasing
the lease, clear preparation/reservation bookkeeping, and propagate cancellation.
No startup or task command may be written on this cancelled path. After successful
registration, the existing worker stop/close path owns cleanup.

Repeated cancellation must not abandon an in-progress reaper. A second cancellation
can suppress the best-effort stopped notification; cleanup must not depend on that
notification being delivered. If process exit cannot be confirmed because OS
termination fails, retain the lease and surface the failure rather than claiming
successful cleanup. Generation-lease scanning protects source storage and removes
dead-holder references; it is not a process reaper.

The real-child regression test `test_startup_heartbeat_cleanup_keeps_process_ownership`
selects cancellation at heartbeat teardown and again during reaping. It checks
child exit, exact lease-removal ordering, empty preparation/worker registries,
zero attempted startup writes, ordinary startup/stop, and post-spawn lease-acquisition
failure. This controlled schedule does not establish production incidence, model
execution, or performance improvement.

## Qualification

The affected profile-input and source-declaration tests cover successful timing
records, failed preparation and compatibility checks, nested freeze failure,
cancellation, private configuration exclusion, and cache reuse without rewriting
the original receipt.

An owned Linux DTU experiment on main `b190c3b7` plus this instrumentation used
synthetic configuration and no model input. Two concurrent cold Work-profile
requests shared one qualified runtime and completed in 11.50 and 11.46 seconds;
two subsequent cache hits took 1.18 and 0.98 milliseconds. Dependency preparation
accounted for 6.50 seconds, runtime freeze for 2.91 seconds, and compatibility
checking for 1.69 seconds. Download and OS caches could be warm.

This is not evidence of a latency improvement or Mac/Windows performance. The
subsequent actual worker-startup check found shared-source bytecode contamination;
that independent repair is tracked in PR #507. A passed preparation probe alone
does not prove a usable first conversation.

## Current-main delivery (0.20.109)

The cancellation fix is integrated with main 0.20.108. In the owned Linux ARM64
DTU, 162 runtime/lifecycle/update/profile/continuation checks and two subtests
pass; 85 release checks pass. The production frontend build passes.

The earlier hosted dictation failure is reproducible without a runtime change:
the test started with no selected chat, entered a draft, then created its first
chat. If autosave completed first, the new chat correctly inherited that draft.
Waiting for the authoritative draft save deterministically reproduces the
incorrect empty-draft assertion. The test now creates its source chat before
editing, switches to a distinct chat, and verifies the original draft survives
while late recognition results cannot enter the other chat. The corrected
browser test, cross-chat draft browser test, and three dictation unit tests pass.
This is simulated recognition, not physical voice acceptance. The failed probe
is retained in workspace evidence under output/approved-followups.

This release fixes the demonstrated pre-registration cancellation boundary;
it makes no broader CPU improvement claim. Platform acceptance remains separate.
