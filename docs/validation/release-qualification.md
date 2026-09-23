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
versions and release notes, run `npm ci --prefix frontend`,
`npm test --prefix frontend`, and `npm run build --prefix frontend`. Include all
resulting `amplifier_web/static` changes in the release commit. Rebuild once more
and require `git diff --exit-code -- amplifier_web/static` to pass before merging
the release candidate; a version-only edit still changes generated bundle hashes.
Keep the release workflow's committed-assets comparison enabled.

## Temporary rapid-development policy (September 23, 2026)

Browser scenarios and the complete Python suite are opt-in for the current
development period. Neither release publication nor PR integration runs the
complete Python suite or browser scenarios. Run the full suite locally before
release preparation with `uv sync --locked --group dev --group artifacts` and
`uv run --no-sync pytest -q --tb=short`, retaining the tested commit and result
in the release handoff. This local result is not an automatic publication gate.
Focused contract workflows, frontend unit tests/builds, committed-assets
comparison, package installation probes, and the short real-runtime suite remain
automatic. Release receipts explicitly name `package`, `frontend`, and `runtime`;
none of these claims that the full Python or browser suite ran.

**Python checks (on demand)** also runs the complete suite on a selected branch
or tag. It is a read-only manual workflow without a publish step or a release
dependency. No tests were deleted, and restoring automatic coverage is an
explicit workflow change.

Run **Browser checks (on demand)** from GitHub Actions, selecting the branch or
tag to test. It retains every former release browser command, plus the capacity,
coordination and connector browser checks from the PR workflows, in one isolated
runner. It has read-only permissions and cannot publish or block a release.
Tests remain available locally through the existing frontend scripts. There is
no scheduled browser/Python run or automatic expiration of this temporary policy.

For v0.20.13, merge-to-publication took 10m02s; its Python lane took 8m31s,
frontend 1m13s, and runtime 43s with a cache hit. Those lanes overlap, so removing
the full Python suite will expose another longest lane rather than subtracting
8m31s from every release. The next release must measure the resulting critical
path. Actual duration still depends on queues, dependency resolution and builds. Ordinary
feature merges still need a versioned release before published-release clients
can update.

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
run. No previous run or PR result is accepted as qualification evidence.

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
| Full pytest suite | Local or manual Python workflow; outside publication |
| Distribution verification, isolated wheel install and import/assets/login probe | Package |
| npm tests, frontend build and committed-assets comparison | Frontend |
| Browser scenarios and their conditional file guards | Manual browser workflow; outside publication |
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

An exact-candidate promotion system that reuses matching PR/merge qualification,
shared prebuilt wheels, and merge-policy changes remain separate proposals.
