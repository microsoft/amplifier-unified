# Parallel release qualification

The 0.20.8 release run [35803007224](https://github.com/microsoft/amplifier-unified/actions/runs/35803007224)
took 27m04s. Its serial stages included 7m34s of Python/frontend validation,
9m39s of runtime qualification, and 7m43s of browser checks. The runtime stage
spent 9m28s rebuilding the same Core Git commit as the preceding attempts;
its 24 actual tests took 4.57s. Application packaging and its install probe took
six seconds.

## Preparing a version change

The frontend embeds the package version from `pyproject.toml` in both its
JavaScript bundle and `static/build.json`. After updating the Python package
versions (including the local `amplifier-unified` entry in `uv.lock`) and release
notes, verify `uv sync --locked --dry-run --group dev --group artifacts` in the
owned environment, then run `npm ci --prefix frontend`,
`npm test --prefix frontend`, and `npm run build --prefix frontend`. Include all
resulting `amplifier_web/static` changes in the release commit. Rebuild once more
and require `git diff --exit-code -- amplifier_web/static` to pass before merging
the release candidate; a version-only edit still changes generated bundle hashes.
Keep the release workflow's committed-assets comparison enabled when a matching
trusted PR proof is unavailable; a reused proof includes that same comparison.

The installed-wheel probe also loads worker-owned app packages in a fresh
`-I -S` child outside the checkout. A successful host import is not worker
qualification: sibling app packages must be exposed by the explicit bootstrap
without adding host site-packages or distribution metadata to the runtime.

## Local-first, fast publication policy (updated October 14, 2026)

The development loop depends on published releases reaching local development
installations quickly. Target roughly two minutes for a warm publication run;
this is an operating target, not a guarantee about GitHub queues, downloads or
cold dependency builds. Exhaustive testing must not silently become a publication
or merge prerequisite again.

Before requesting merge, run the affected backend and frontend tests locally,
and exercise changed user flows in an owned DTU with synthetic state. Reuse owned
dependencies where appropriate. Aim for 1–3 minutes of focused local checks and
about five minutes for the normal foreground budget. The full local suite is not
a merge or release prerequisite, including for substantial integrations. Select
specific pre-release checks for material risks such as destructive migrations or
authorization changes. Fix failures attributable to the candidate's changed
behavior before publishing; record the exact source, commands, results, skips and
limits in the PR. Unrelated broad-suite findings remain visible follow-up work.
A green GitHub check is not a substitute for focused qualification. Follow
workspace DTU rules; never use a shared application installation as a test target.

Typical local commands, inside the owned test environment:

```sh
uv sync --locked --group dev --group artifacts
uv run --no-sync pytest -q --tb=short tests/test_affected_feature.py
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- amplifier_web/static
# Select relevant browser scripts from frontend/package.json.
# Optional broader local coverage runs separately in the background:
# uv run --no-sync pytest -v --tb=short
```

Replace the illustrative test filename with the actual affected suite. Real
runtime checks require their declared runtime dependencies; report host-only
skips accurately. Keep source, state, services and ports isolated and clean up
owned temporary resources after evidence is retained.

GitHub's required **Fast release checks** runs on every PR. It checks release
identity/reporting contracts, frontend unit tests, the build and committed assets.
Path-scoped contract workflows remain available for affected components. The
publication graph retains only prepare, package, frontend, native runtime and
publish. Matching candidate/dependency receipts, fresh installed-wheel probing,
immutable tags, checksums and the short real-runtime regression suite still gate
publication. The exhaustive readiness suite no longer runs on PRs or main pushes.

**Post-release verification** starts from successful completion of the publisher,
including publishers using `GITHUB_TOKEN`. It reads that run's retained candidate,
checks the actual published stable tag resolves to the candidate commit, and uses
that immutable revision for all broad tests. It does not substitute the current
main head or whichever release happens to be latest. The installed-package lane
downloads both published distributions, verifies their checksums, and installs
and probes the wheel outside the source checkout. The full Python, broad readiness
and extended browser suites run in independent jobs after publication.
The full Python lane has a 90-minute limit and prints each test's identity as it
runs, so a timeout does not conceal failed or unfinished test identities.
It retains the verbose log and, when pytest completes, JUnit results. A partial
log or absent JUnit file is not a successful or complete suite.

Extended browser checks use the explicit `frontend/tests/browser-checks.json`
plan, preserving distinct flags and modes. Scenarios run serially in separate
process groups, with a complete intended-coverage record written before execution.
A failed scenario remains failed while later independent scenarios run. The
runner retires only its owned process group and uniquely tagged descendants
(including detached Chromium processes) before proceeding; leaked processes
fail that scenario, and uncertain cleanup, cancellation or the bounded run budget
leaves the remainder explicitly unrun. Any failure or unrun scenario fails the
job. Per-scenario logs, durations, commands, declared environment, source/tree,
plan/runner hashes and build-metadata digest are retained with the run/attempt.
These are observations for that exact source, not reusable release receipts.
Fixtures must continue owning their synthetic state and cleaning resources;
runner process cleanup does not authorize access to real/shared installations.
Each scenario records existing PID/start-time pairs before launch. Unchanged
pre-existing processes are outside its inherited-token ownership; unreadable
new or reused identities still stop cleanup rather than being assumed unrelated.

Do not infer complete history from DOM Work-group counts. The reusable
`scripts/browser_dom_recorder.js` probe reports grouped headers and standalone
single-turn headers separately, excluding embedded turns inside expanded groups.
Its minimum-header and settled-DOM milestones are visibility observations only.
Canonical turn/event completeness needs an explicit source oracle; regrouping
can raise or lower header counts without adding or losing work. Historical
captures made with older counting semantics must retain that limitation.

The reporting job writes a run summary and opens or updates one tracked issue per
release when any expected lane fails, is cancelled, is skipped or is missing.
A successful rerun closes its existing failure issue; green releases create no
issue. Test jobs have read-only repository access; only the isolated reporting
job can write issues. A failure does not withdraw the release, change its bytes,
replay work or modify installed hosts. Investigate it and ship a corrective release
when needed. Published and exhaustively verified remain distinct states.

Publication completion is the foreground handoff. Delegate monitoring and triage
of broad local and post-release verification, and continue the next task without
waiting for those jobs. Notify on actionable findings, not unchanged polling.
When a next release is already in progress, incorporate relevant corrections
there; otherwise address findings directly while idle. Newly discovered urgent
regressions still receive prompt attention. Reusing earlier qualification requires
matching the exact tested inputs; affected checks run again when those inputs
change. The frontend lane implements the exact-input reuse described below;
other passing runs are not blanket waivers for a later candidate.

The broad suites also remain manually runnable. Changing this policy requires an
explicit decision and an update to this document, tests and repository rules.
The repository's dedicated required-check ruleset names **Fast release checks**;
other review, deletion and history protections remain separate and unchanged.

## Execution and identity

`prepare` selects the same immutable merged application commit as before,
resolves current host dependencies once, and records their exact requirements.
Package, frontend, and runtime jobs then run independently against that commit
and restored host graph. Each verifies the graph before and after its checks.
The publication job requires all three jobs to succeed and checks their
candidate identifiers plus the original wheel/source/checksum bytes before
calling the existing immutable-tag publication implementation.

Qualification tools come from the workflow revision, copied before selecting a
historical application commit. They are transferred as an artifact within this
run. Package/native qualification and candidate receipts remain fresh.

The frontend lane may reuse an immutable artifact from successful **Fast release
checks** for the same-repository PR whose merge commit is this publishing main
commit. Reuse binds the entire tracked Git tree (a conservative superset of
frontend code, tests, scripts, lockfiles, version and build configuration), the
actual committed-assets digest, Node/npm and runner image identities. GitHub's
run, attempt, PR association, tested merge commit/tree, job/step success and
artifact archive digest are independently checked; the qualifying workflow and
proof helper must match the release checkout. A green check name or a JSON claim
of success alone is insufficient. The proof is emitted only after tests, build
and committed-assets verification, then retained seven days.

Lookup has a 20-second network budget and small archive/JSON limits. Missing,
expired, changed, fork, tampered or unrecognized proofs run the ordinary frontend
installation, tests, build and assets comparison. Historical release selections
always use that fallback. Backend-only changes can also cause a conservative
whole-tree mismatch; avoiding duplicated work never takes priority over exact
evidence. The frontend lane still issues a fresh receipt for the current release
candidate and retains a separate artifact stating whether and which proof was
reused. Broad post-release coverage is unchanged.

The runtime job checks out the declared loop-live source and current recipe
source as before. It resolves a new runtime lock with refresh and upgrade before
any build-cache lookup. Test dependencies, including tool-delegate, are now part
of this lock instead of being installed by a second unconstrained resolution.
The cache key includes the lock and project digests, loop-live commit, OS/image,
architecture, Python version/ABI, uv, Rust/C toolchain, Maturin version, and hashes
of relevant build flags. The recorded Maturin version also constrains isolated
builds. There is no fallback cache key or standing main-branch pin. A later
candidate resolves moving sources again.

Only a matching uv build cache is restored. Successful builds are saved before
runtime tests so that a test failure does not force a repeat compilation. Cache
contents are pruned with uv's CI policy, retaining source-built wheels. Runtime
graph and source identities are checked again after tests. The private loop-live
package is explicitly removed from this job's cache before saving it; access to
the dedicated-key checkout does not grant other cache readers its package build.

## Coverage map

| Previous serial stage | Job |
|---|---|
| Full pytest suite | Local, manual and automatic post-release workflows |
| Distribution verification, isolated wheel install and import/assets/login probe | Package |
| npm tests, frontend build and committed-assets comparison | Frontend |
| Browser scenarios and their conditional file guards | Local, manual and automatic post-release workflows |
| Real Core/loop-live surface, child, component and cache tests | Runtime |
| Immutable tag/asset checks and publication | Release, after all jobs |

In the manual workflow, the active-client performance check runs once; independent live-client behavior
is checked separately. All four session-health invocations remain, including context-limit and context-limit with an active
worker. Browser fixtures retain their serial ordering within an isolated runner;
fixed-port fixtures and performance checks do not compete with other lanes.

Artifacts explicitly enumerate tools, dependency records, lock/build identities,
qualification receipts and distributions. No service home, environment directory,
Git config, SSH key, or general environment-variable dump is uploaded. Build
flags are represented by hashes, not their potentially private values. Only the
publication job has repository write permission; the existing dedicated read-only
loop-live key is confined to its checkout step.

## Validation and remaining measurements

Regression tests reject changed app commits, dependency records, mixed lane
receipts, altered wheel bytes and changed worker graphs. Cache tests require
invalidation when sources, ABI, architecture, toolchain or build flags change.
Workflow tests require independent lanes, fresh resolution before cache selection,
and all matching receipts before publication. Actionlint validates the workflow.

The new runtime preparation commands were exercised locally against current
sources: locking completed in 3.10s, a fresh environment reused the cached Core
source build, and all 24 real runtime tests passed in 5.59s. These are local
measurements, not proof of GitHub Actions cache transfer or the complete new
release duration. No publication workflow was dispatched for validation.

After this workflow is merged, end-to-end validation can
select an already published tag's full original commit SHA. All jobs execute, and
the existing publisher leaves that tag and its published assets unchanged. The
default newer main commit with an already released package version is rejected
at planning; this guard prevents accidental publication but does not exercise
the downstream jobs. A new release still requires its own version and all required
qualification lanes.

The narrow frontend proof reuse described above does not promote an entire
previous release candidate. Reusing package/native qualification or shared
prebuilt wheels remains a separate proposal.

## Published Core binaries

The app host, worker project and Work acceptance project require
`amplifier-core>=2.0.1` from PyPI. This is a minimum supported release, not a
standing exact-version pin. Core's Git source override is removed. The uv
`no-build-package` policy forbids compiling Core; other source dependencies can
still build. The worker uses the same explicit public index as the host so an
outdated local mirror cannot silently keep it on an older Core release.

An existing worker's old app-owned Core Git-main default migrates only in a new
qualified environment generation. Its previous base manifest must prove that
default, and the installed source must match it. Forks, fixed refs and edited or
local sources are preserved; they are never treated as that default. Existing
locks and rollback receipts are unchanged. Installed-source augmentation must
not reintroduce the retired app-owned Core Git override.

The release's fresh source resolution, exact cache key and frozen runtime graph
checks remain intact. Rust/Maturin identity and build constraints remain because
other moving-source packages may require native builds. A Core policy/lock
change changes the cache key; no old Git-Core cache can make the registry
selection sticky. Core wheel metadata and its native extension hash now join
the runtime receipt, and qualification fails if amplifier-app-cli is installed.
No CLI companion is needed by Unified's runtime qualification.

To independently qualify a dependency change on both Linux architectures, run
the existing **Python checks (on demand)** workflow on the reviewed branch with
`core_wheels=true`. Its default remains the full Python suite. The Core mode
runs focused migration/receipt tests and all 24 real runtime checks unchanged
on Linux x86_64 and aarch64, using fresh moving-source resolution and published
Core wheels. It records the tested app revision, full lock, resolved source
identities, installed graph, wheel tags and native binary hashes. It has read-only
repository permissions, no publisher, and no production app access. The private
loop-live key remains confined to explicitly dispatched qualification; automatic
PR jobs do not receive it. macOS and Windows require separate platform evidence.
