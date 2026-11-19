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

[microsoft/amplifier-portability](https://github.com/microsoft/amplifier-portability)
is the sole Portability source and standalone-test owner. Preserve
`amplifier_portability` imports, signed receipts, retained JSON recovery and public
payload/evidence APIs. Unified retains admission, data projection, native fencing,
account probes and tighter host dependency constraints. It consumes only generated
`portability-delivery/`, recorded by `portability.source.json`, whose embedded
Worktrees receipt must equal `worktrees.source.json` exactly. Publication does not
refresh these selections. Never restore an author copy or publisher here, edit
selected bytes by hand, or coinstall the standalone distribution with Unified.
Follow the [paired selection guide](README.md#select-worktrees-and-portability-artifacts).

[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees)
is the sole Worktrees author and owns its real-Git mechanism tests. Preserve
`amplifier_worktrees` and `amplifier_worktrees.git` imports. Unified selects public
bytes into generated `worktrees-delivery/`, recorded by `worktrees.source.json`,
as the sole installed owner. Host permissions, active-task, handoff and cleanup
policy remain in Unified. Portability must retain its exact selected Worktrees
receipt; follow the [paired selection guide](README.md#select-worktrees-and-portability-artifacts).
Never restore an author copy, edit generated delivery or coinstall the standalone
distribution with Unified.

[microsoft/amplifier-publishing](https://github.com/microsoft/amplifier-publishing)
is the sole Publishing author and owns its lifecycle, storage and private-service
mechanism tests. Preserve `amplifier_publishing` imports and the service module
entrypoint. Unified consumes only the published artifact selected into
`publishing-delivery/` by `publishing.source.json`, as the sole installed owner.
Never restore an author copy here, edit generated delivery by hand or coinstall the
standalone distribution. Source acceptance and publication do not refresh the app
selection. Host authorization, policy and integration remain in Unified; follow
the [selection instructions](README.md#select-a-publishing-artifact).

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

[amplifier-platform-kernels](https://github.com/bkrabach/amplifier-platform-kernels)
is the sole authoring owner for `amplifier_kernels`, including
its Python and Node runners and real-process mechanism tests. Unified consumes
the generated top-level `amplifier_kernels/` snapshot selected by
`kernels.source.json`, preserving editable and isolated-worker import origins.
Never edit that snapshot by hand or coinstall the standalone distribution with
Unified. Author edits and publication do not refresh the app selection; the host
retains process approval, execution, observation and recovery authority. Do not
restore an author copy here; follow the [selection guide](README.md#select-a-kernels-artifact).

[amplifier-platform-status](https://github.com/bkrabach/amplifier-platform-status)
is the sole author of the local observation package and its standalone tests. Preserve
the public `amplifier_status` import, CLI and optional hook. Unified consumes only
the published artifact selected into generated `amplifier_web/status_protocol`,
bound by `status-emitter.source.json`; do not edit that private delivery copy.
Author changes/publication do not refresh it. Unlike the other embedded packages,
distinct private/public imports and executable names permit coinstallation. Keep
host enablement, projection and authority in Unified. Do not restore an author
copy here; follow the [selection guide](README.md#select-a-status-artifact).

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
