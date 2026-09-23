# Parallel release qualification

The 0.20.8 release run [35803007224](https://github.com/microsoft/amplifier-unified/actions/runs/35803007224)
took 27m04s. Its serial stages included 7m34s of Python/frontend validation,
9m39s of runtime qualification, and 7m43s of browser checks. The runtime stage
spent 9m28s rebuilding the same Core Git commit as the preceding attempts;
its 24 actual tests took 4.57s. Application packaging and its install probe took
six seconds.

## Execution and identity

`prepare` selects the same immutable merged application commit as before,
resolves current host dependencies once, and records their exact requirements.
Python, browser, and runtime jobs then run independently against that commit
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
graph and source identities are checked again after tests.

## Coverage map

| Previous serial stage | Job |
|---|---|
| Full pytest suite | Python |
| Distribution verification, isolated wheel install and import/assets/login probe | Python |
| npm tests, frontend build and committed-assets comparison | Browser |
| Every existing browser command and its conditional file guard | Browser |
| Real Core/loop-live surface, child, component and cache tests | Runtime |
| Immutable tag/asset checks and publication | Release, after all jobs |

Both active-client performance invocations remain. All four session-health
invocations remain, including context-limit and context-limit with an active
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

After this workflow is merged, an explicitly authorized end-to-end validation can
select an already published tag's full original commit SHA. All jobs execute, and
the existing publisher leaves that tag and its published assets unchanged. The
default newer main commit with an already released package version is rejected
at planning; this guard prevents accidental publication but does not exercise
the downstream jobs. A new release still requires its own version and complete
qualification.

An exact-candidate promotion system that reuses matching PR/merge qualification,
shared prebuilt wheels, and merge-policy changes remain separate proposals.
