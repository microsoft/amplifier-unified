# Latest Amplifier component qualification

A committed `uv.lock` records a prior resolution. A normal sync or a command
using `--locked` reproduces that resolution; the `@main` declarations alone do
not make an existing lock refresh. Keep old locks and acceptance receipts.

For development or a **new** qualification, make an isolated checkout at the
source revision under review, then explicitly resolve Amplifier packages:

```sh
python3 scripts/resolve_amplifier_latest.py --mode latest \
  --project . --evidence /absolute/private/new-unified-resolution
uv sync --locked --group dev
```

The same resolver accepts the Work bundle checkout or Unified's Work acceptance
project. Run the script from the Unified checkout and select the project to
qualify:

```sh
python3 scripts/resolve_amplifier_latest.py --mode latest \
  --project /absolute/isolated/amplifier-bundle-work \
  --evidence /absolute/private/new-work-resolution
python3 scripts/resolve_amplifier_latest.py --mode latest \
  --project scripts/work_profile \
  --evidence /absolute/private/new-work-acceptance-resolution
uv sync --project scripts/work_profile --locked --group dev --group browser
```

The resolver refreshes every Amplifier package found in declarations, optional
extras, development groups and the resolved dependency graph, including newly
introduced transitive packages. It retains local source overrides and explicit
user refs, refreshes branch sources, and records their exact resolved revisions.
It does not change source declarations, install packages, prepare bundles, run
models, or start acceptance. Third-party dependencies are left to the declared
compatibility requirements and normal dependency resolution. A newer dependency
may require additional third-party changes in the new lock.

Each new evidence directory holds the previous lock, resolved lock, manifest
digest, resolved Amplifier source inventory, and refresh passes. A failed
resolution restores the original lock and saves the unsuccessful candidate for
inspection. Review the resulting lock and run the relevant no-model tests before
using it as input to separately authorized live acceptance. Optional TUI/native
installers and dynamically prepared Foundation modules have their own runtime
qualification paths; this script qualifies the selected Python project graph.

For reproduction, select the original recorded lock in the isolated checkout
and use `--mode replay` with a new evidence directory. Replay requires the lock
to remain byte-for-byte unchanged. Existing active workers, pending generations,
saved conversations and prior acceptance directories are never inputs to this
workflow.

## Worker update generations

Updates also inventory installed Amplifier distributions that Foundation added
after the worker's original lock, including Git subdirectories and editable
Foundation caches. A new generation checks those branch revisions, prepares
modules in an isolated process with dependency refresh enabled, captures the
complete installed graph, and verifies a fresh ordinary preparation against its
frozen receipt. Refresh-enabled activators never enter active conversations.
This path requires Foundation's explicit `refresh_dependencies` and
`install_overrides` preparation API. Existing recorded generations keep their
ordinary preparation behavior.

`runtime.toml`, `runtime.lock` and `runtime-installed.json` are immutable
qualification evidence; exact versions and commits there reproduce that
generation. `runtime-base.toml` and `runtime-sources.json` retain the future
branch policy. The next update starts from the packaged manifest and recorded
branch identities, so a frozen receipt cannot turn `@main` into a future pin.
Local overrides are preserved; an unrecognized local/registry replacement of a
declared worker dependency blocks automatic replacement until its source
configuration is explicit. Recorded cache revisions and tracked content are
verified without resetting local work.
