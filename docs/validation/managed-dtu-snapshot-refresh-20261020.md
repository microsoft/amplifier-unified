# Managed DTU snapshot refresh

New managed setup selects Digital Twin Universe 0.6.4 at
`5235833098d9a630791452a46558d92f58af0389`. The previously qualified
`8f644b3d8d8b89f0bb3ae9853bb531017dc98a04` remains accepted only with its
matching managed installation. Existing configuration receipts retain their
original installation, executable and commit; selecting the newer runner does
not rebind an earlier environment. Arbitrary revisions and mismatched
installation/commit pairs remain rejected.

Compared with the previous snapshot, upstream changed skill resource names and
removed three library convenience exports that Unified does not call:
`skill_directory`, `skill_resources` and `repository_url`. Lifecycle,
repository snapshot and schema implementations are unchanged. The upstream
lockfile also updates aiohttp from 3.14.3 to 3.14.4; Unified's managed installer
uses `uv pip install`, so that lockfile alone does not establish its resolved
dependency versions.

## Bounded qualification

- 282 Unified adapter, configuration, setup and lifecycle tests passed;
  one live Docker test was skipped.
- 80 upstream skill, manifest and repository snapshot tests passed using the
  new source and built package metadata with existing read-only dependencies.
- An offline-built 0.6.4 wheel passed CLI help and manifest checks.
- Unified's actual preparation adapter exported a changed Git working area
  through that wheel, accepted its archive identity and preserved the original
  source. Daemon identity and host-resource observations were fixed fixtures;
  this check did not launch an environment.
- Regressions cover setup selecting the newer snapshot while an older install
  remains, cold recovery of an old owner after selection changes, and rejection
  of unknown revisions or a changed installation/commit pair.

This qualifies the changed package interface and adapter paths. It does not
claim a fresh dependency resolution, a live Docker lifecycle run, or adoption
by any existing installation. Setup continues to verify its selected backend
before publishing a new configuration.
