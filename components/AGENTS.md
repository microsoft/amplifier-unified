# Component ownership

`components/workspaces/` is the temporary authoring home for the independently
buildable `amplifier_workspace_metadata` package. Its registration and query
mechanism tests belong here; Unified retains initialization, filesystem effects,
history, authority and persistence. Unified includes the same runtime sources as
the sole installed owner; do not coinstall the standalone distribution. This
metadata boundary is not the whole workspace domain or a generated copy.

`components/outputs/` is temporarily authored here as an independently buildable
Python project. Preserve `amplifier_outputs` imports and its saved-body formats.
Its Git/SQLite mechanism tests belong with the package; Unified retains
transactions, authorization, source access, content integrity and retention.
Normal Unified builds these same runtime sources directly; do not coinstall the
standalone distribution. This is not a generated copy or the whole resources domain.

`components/portability/` is temporarily authored here as an independently
buildable Python project. Preserve `amplifier_portability` imports and its existing
worktree dependency. Its signed-journal and Git-capsule tests stay with the package;
Unified retains task admission, data projection, native fencing and account probes.
Normal Unified builds both runtime packages directly; the standalone preparation
uses an exact local worktrees artifact, not an assumed public dependency.

`components/worktrees/` is temporarily authored here as an independently buildable
Python project. Preserve `amplifier_worktrees` and `amplifier_worktrees.git` imports;
Unified builds directly from its nested runtime sources. Its real-Git mechanism
tests belong here; host permission, active-task, handoff and cleanup policy remain
in Unified. It is not a generated delivery copy.

`components/publishing/` is temporarily authored here as its independently
buildable Python project. Preserve its `amplifier_publishing` imports and existing
module entrypoint; Unified builds directly from the nested runtime sources.
Its portable tests stay with the package, while host policy and integration tests
stay in Unified. It is not a generated delivery copy.

`components/scheduling/` is temporarily authored here as its independently
buildable Python project. Preserve its normal `amplifier_scheduling` import.
Unified consumes only the generated `scheduling-delivery/` snapshot selected by
`scheduling.source.json`; author edits and publication do not change this default.
Never edit the delivery copy by hand or coinstall the standalone distribution with
Unified, which remains the sole installed owner. Follow the author project's README.

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
