# Component ownership

Author `amplifier_recall` and its SQLite mechanism tests in
[amplifier-platform-recall](https://github.com/bkrabach/amplifier-platform-recall).
Unified retains source access, scope, authorization, opt-ins, context assembly,
model execution, Stop and memory policy. Unified consumes only the published
artifact selected into `recall-delivery/` by `recall.source.json`, as the sole
installed owner. Never edit that generated copy by hand; author changes and
publication do not refresh the app selection. Preserve existing imports and
database formats; the database includes durable explicit notes, versions, settings
and receipts, not only rebuildable search data. Do not coinstall the standalone
distribution with Unified or restore a second author copy here. Follow the
[selection instructions](README.md#select-a-recall-artifact).

Author `amplifier_workspace_metadata` and its registration/query tests in
[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces).
Unified retains initialization, filesystem effects,
history, authority and persistence. Unified consumes only the published artifact
selected into `workspace-metadata-delivery/` by `workspace-metadata.source.json`,
as the sole installed owner; do not coinstall the standalone distribution. Never
edit that generated copy by hand. Author edits and publication do not refresh the
app selection. Do not restore a second author copy here. Follow the
[selection instructions](README.md#select-a-workspace-metadata-artifact).
This metadata boundary is not the whole workspace domain.

Author the output relationship, saved-body decoder and Git evidence package in
[amplifier-platform-resources](https://github.com/bkrabach/amplifier-platform-resources).
Preserve `amplifier_outputs` imports and its saved-body formats.
Its Git/SQLite mechanism tests belong with the package; Unified retains
transactions, authorization, source access, content integrity and retention.
Unified consumes only the published artifact selected into `outputs-delivery/`
by `outputs.source.json`, as the sole installed owner. Never edit that generated
copy by hand or coinstall the standalone distribution. Author edits and publication
do not refresh the app selection. Do not restore an author copy here; follow the
[selection instructions](README.md#select-an-outputs-artifact).
This package is not the whole resources domain.

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

[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees)
is the sole Worktrees author and owns its real-Git mechanism tests. Preserve
`amplifier_worktrees` and `amplifier_worktrees.git` imports. Unified selects public
bytes into generated `worktrees-delivery/`, recorded by `worktrees.source.json`,
as the sole installed owner. Host permissions, active-task, handoff and cleanup
policy remain in Unified. Portability must retain its exact selected Worktrees
receipt; follow the [paired selection guide](README.md#select-worktrees-and-portability-artifacts).
Never restore an author copy, edit generated delivery or coinstall the standalone
distribution with Unified.

`components/publishing/` is temporarily authored here as its independently
buildable Python project. Preserve its `amplifier_publishing` imports and existing
module entrypoint. Unified consumes only the published artifact selected into
`publishing-delivery/` by `publishing.source.json`, as the sole installed owner.
Never edit that generated copy by hand or coinstall the standalone distribution.
Author edits and publication do not refresh the app selection. Its POSIX mechanism
tests stay with the package; host authorization, policy and integration stay in Unified.

Author Scheduling's policy/store and standalone tests in
[amplifier-platform-scheduling](https://github.com/bkrabach/amplifier-platform-scheduling).
Preserve its normal `amplifier_scheduling` import. Unified consumes only the
generated `scheduling-delivery/` snapshot selected by `scheduling.source.json`;
author edits and publication do not change this default. Never restore an editable
author copy here, edit the delivery copy by hand or coinstall the standalone
distribution with Unified, which remains the sole installed owner. Follow
[the selection instructions](README.md#select-a-scheduling-artifact); host admission,
authorization, Stop and execution remain in Unified.

[bkrabach/amplifier-platform-operations](https://github.com/bkrabach/amplifier-platform-operations)
is the sole authoring owner for `amplifier_operations`.
Its SQLite operation observations and bounded delivery/cursor tests belong there;
Unified retains admission, authority, Stop and execution. The top-level
`amplifier_operations/` is the generated runtime snapshot selected by
`operations.source.json`, preserving the installed and isolated-worker import path.
Unified remains the sole installed owner. Never restore an author copy here,
edit that snapshot by hand or coinstall the standalone distribution with Unified.
Independent author edits/publication do not refresh the app selection. Follow
the [selection instructions](README.md#select-an-operations-artifact).

`kernels/` is the temporary authoring project for `amplifier_kernels`, including
its Python and Node runners and real-process mechanism tests. Unified consumes
the generated top-level `amplifier_kernels/` snapshot selected by
`kernels.source.json`, preserving editable and isolated-worker import origins.
Never edit that snapshot by hand or coinstall the standalone distribution with
Unified. Author edits and publication do not refresh the app selection; the host
retains process approval, execution, observation and recovery authority.

`status/` authors the independently published local observation package. Preserve
the public `amplifier_status` import, CLI and optional hook. Unified consumes only
the published artifact selected into generated `amplifier_web/status_protocol`,
bound by `status-emitter.source.json`; do not edit that private delivery copy.
Author changes/publication do not refresh it. Unlike the other embedded packages,
distinct private/public imports and executable names permit coinstallation. Keep
host enablement, projection and authority in Unified.

## Web client delivery

`web-client.source.json` selects an exact accepted public revision of
[microsoft/amplifier-client-web](https://github.com/microsoft/amplifier-client-web).
Maintain source, client tests and build tools there. Unified prepares the selected
repository source into ignored `.build/web-client` and assembles its built assets;
no committed client source copy is maintained here.

Read [README.md](README.md#select-public-client-source) and the source record for
selection and handoff. Unified owns `scripts/web_client_build.py` and the
host/browser/producer checks in `tests/web`. Neither prepared source nor generated
assets create another client owner. Source acceptance does not change the selected
client or publish a Unified release automatically.
