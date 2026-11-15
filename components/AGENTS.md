# Component ownership

`components/workspaces/` is the temporary authoring home for the independently
buildable `amplifier_workspace_metadata` package. Its registration and query
mechanism tests belong here; Unified retains initialization, filesystem effects,
history, authority and persistence. Unified consumes only the published artifact
selected into `workspace-metadata-delivery/` by `workspace-metadata.source.json`,
as the sole installed owner; do not coinstall the standalone distribution. Never
edit that generated copy by hand. Author edits and publication do not refresh the
app selection. This metadata boundary is not the whole workspace domain.

`components/outputs/` is temporarily authored here as an independently buildable
Python project. Preserve `amplifier_outputs` imports and its saved-body formats.
Its Git/SQLite mechanism tests belong with the package; Unified retains
transactions, authorization, source access, content integrity and retention.
Unified consumes only the published artifact selected into `outputs-delivery/`
by `outputs.source.json`, as the sole installed owner. Never edit that generated
copy by hand or coinstall the standalone distribution. Author edits and publication
do not refresh the app selection. This package is not the whole resources domain.

`components/portability/` is temporarily authored here as an independently
buildable Python project. Preserve `amplifier_portability` imports and its existing
worktree dependency. Its signed-journal and Git-capsule tests stay with the package;
Unified retains task admission, data projection, native fencing and account probes.
Unified consumes only `portability-delivery/` selected by `portability.source.json`,
which binds the exact published dependency to `worktrees.source.json`. Authoring
and publication do not change those selected defaults. Standalone delivery selects
an exact public worktrees artifact through `portability/worktrees-source.json`;
install both selected wheels explicitly, never alongside their Unified-owned copies.
Never edit either generated delivery copy by hand.

`components/worktrees/` is temporarily authored here as an independently buildable
Python project. Preserve `amplifier_worktrees` and `amplifier_worktrees.git` imports;
Unified selects published bytes into `worktrees-delivery/`, recorded by
`worktrees.source.json`, as the sole installed owner. Its real-Git mechanism
tests belong here; host permission, active-task, handoff and cleanup policy remain
in Unified. The author project is independent of the generated delivery copy;
never edit that copy by hand or coinstall the standalone distribution.

`components/publishing/` is temporarily authored here as its independently
buildable Python project. Preserve its `amplifier_publishing` imports and existing
module entrypoint. Unified consumes only the published artifact selected into
`publishing-delivery/` by `publishing.source.json`, as the sole installed owner.
Never edit that generated copy by hand or coinstall the standalone distribution.
Author edits and publication do not refresh the app selection. Its POSIX mechanism
tests stay with the package; host authorization, policy and integration stay in Unified.

`components/scheduling/` is temporarily authored here as its independently
buildable Python project. Preserve its normal `amplifier_scheduling` import.
Unified consumes only the generated `scheduling-delivery/` snapshot selected by
`scheduling.source.json`; author edits and publication do not change this default.
Never edit the delivery copy by hand or coinstall the standalone distribution with
Unified, which remains the sole installed owner. Follow the author project's README.

`operations/` is the temporary authoring project for `amplifier_operations`.
Its SQLite operation observations and bounded delivery/cursor tests belong here;
Unified retains admission, authority, Stop and execution. The top-level
`amplifier_operations/` is the generated runtime snapshot selected by
`operations.source.json`, preserving the installed and isolated-worker import path.
Never edit that snapshot by hand or coinstall the standalone distribution with
Unified. Independent author edits/publication do not refresh the app selection.

`kernels/` is the temporary authoring project for `amplifier_kernels`, including
its Python and Node runners and real-process mechanism tests. Unified consumes
the generated top-level `amplifier_kernels/` snapshot selected by
`kernels.source.json`, preserving editable and isolated-worker import origins.
Never edit that snapshot by hand or coinstall the standalone distribution with
Unified. Author edits and publication do not refresh the app selection; the host
retains process approval, execution, observation and recovery authority.

## Web client delivery

`web-client/` is a verbatim snapshot of the separate client repository selected
by `web-client.source.json`. Maintain source, client tests and build tools in that
owning repository, then refresh the whole snapshot and record together. Do not
make independent fixes inside this copy. Its nested guides describe upstream
development; this wrapper governs maintenance of the delivery copy.

Read [README.md](README.md) and the small source record before opting into copied
contents. Unified assembles the selected client through
`scripts/web_client_build.py`; ignored `.build/web-client` is disposable prepared
source. Host/browser/producer checks and their own dependencies live in
`tests/web`. Neither delivery nor preparation creates another client owner.
