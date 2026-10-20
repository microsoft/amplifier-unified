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

## Source policy survives immutable installation

The qualified worker manifest pins exact installed commits for reproducibility.
Those commits are separate from the branch, tag, or explicit pin selected for
future updates. Before freezing a newly prepared graph, preserve an authored
requirement only when its repository, subdirectory, lock revision, and installed
revision agree. A full commit written by the resolver may then retain the
prepared declaration's original ref. An explicit installed tag, other branch,
fork, different directory, or changed revision cannot be relabeled this way.

Recovery needs explicit branch authority even when the resolver retains a named
`rev` rather than normalizing it into an exact SHA. Authored tags (including non-version names such as `stable`)
and ambiguous `rev`/PEP 508 names retain the exact installed commit. A matching
repository and commit do not prove that a name is a branch. Branch/tag conflicts
remain protected, even when their names are identical.

Only unconditional selected requirements and explicitly selected default groups
can authorize recovery. Marker alternatives, unused extras/groups, constraints,
and unknown source selection can block conflicting recovery but cannot authorize
it alone. The app's managed default group remains supported
through its actual checked stage receipt; an unchecked group URI is not branch
proof. Resolving ambiguous refs later requires selected-source reconciliation with
exact repository ref evidence, not a guess from names such as `main`.

`runtime-sources.json` records `policyOrigin` beside each newly captured policy:
its basis, original ref, exact source identity, and (for prepared declarations)
the manifest digest. Ambiguous names use `unresolved-reference` and `originalRef`;
explicit tags use `tag-reference`. Both remain fixed until separate policy proof. Checked policies carry that origin through subsequent
staging and freeze. Exact source drift still fails qualification. Installed
owned clean cache modules retain their declared policy; unrelated or newer
adjacent caches do not explain a Git distribution's old pin.

Older pins without recorded origin remain unchanged. Their update-source detail
reports that the origin is not recorded, so an intentional pin is not silently
converted into branch tracking. Resolving a legacy policy requires the actual
source declarations and configuration; conflicting bundle declarations require
an explicit compatibility decision. This change does not automatically migrate
legacy pins, alter registry component policy, or refresh qualified test adapters.

Focused tests cover declaration recovery before freeze, direct/transitive
requirements, policy origin across stage/freeze/next check, exact identity drift,
explicit pins/branches/tags/forks, ambiguous declarations, clean owned cache
boundaries, and retention of an unknown legacy pin. They establish source-policy
behavior, not deployment or fresh-worker adoption on a user's installation.
Real local Git/uv tests also exercise normalized installs through production freeze,
recorded verification, and next inventory: explicit selected branches and checked
managed groups remain eligible; tags, ambiguous names, inactive markers and unused
extras remain pinned.

Actual authored-manifest resolver tests separately cover a repository with only a
`stable` tag, same-name branch/tag, and a real branch. Both `rev="stable"` and
PEP 508 `@stable` remain protected without branch-kind proof. The original-ref
evidence survives unrelated stage/freeze/inventory cycles.

Scope boundary: Foundation cache/shared-binding metadata can also contain an
untyped non-version ref. Its existing ownership checks establish source identity,
not branch-versus-tag intent. That adjacent path is tracked separately for exact
ref proof; this correction does not claim to resolve all cache policy ambiguity.
