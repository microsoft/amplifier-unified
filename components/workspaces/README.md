# Workspace registration metadata

`amplifier_workspace_metadata` owns registration edits, native observation
reconciliation, local-location interpretation, and list/search/paging over
caller-owned records. It uses only
Python's standard library. It does not import Unified, Core or Foundation, read
folders, allocate identities, persist state, or authorize access.

The public exports are `register`, `edit`, `reconcile`, `listing`,
`RegistrationReconciliation`, `local_location`, and `LocalLocation`.
Callers supply the current mutable registration
list. Edits preserve existing row objects and custom fields. Reconciliation
returns ordered reference remaps for the caller to apply under its existing
lock. `listing(registrations, args)` returns shallow row copies with the current
query, label and paging rules; nested custom values retain their references.
The caller validates action inputs and resolved folders, and owns transactions,
selection, pins, Canvas effects, history and publication.

Current path-derived IDs, unresolved native-project placeholders and display
aliases retain their meanings. This package does not implement multi-location
bindings, portable identity, shared context, filesystem grants or a second chat
catalog. Those remain separate workspace and conversation-owner contracts.

## Interpret a legacy local location

`local_location(registration)` returns an immutable
`LocalLocation(workspace_id, path, available)` snapshot, or `None` when the
legacy path is missing, `None` or empty. It preserves the existing ID and exact
path spelling without resolving folders, normalizing paths or probing availability.
A nonempty path with `available=False` remains a known location; missing or
`None` availability remains unknown. The recorded availability is not coerced.

The observation is local to the caller's existing installation/account scope.
It is not a location identity, native history locator or permission grant.
The existing registration's top-level `path` remains the sole writable location;
the snapshot is derived, never a second persisted record. Call again after a
registration changes. Custom fields, including any existing `locations` field,
remain opaque and untouched. Archive and display-name edits do not erase the
location. Unresolved native-project placeholders still follow the existing
ordered reconciliation/remap rules.

This API adds interpretation only. It does not change host ID derivation,
discovery, selection, pins, settings, sessions, Canvas, execution directories or
history. Interpreting a changed path does not move files or running work.

## Develop and qualify

Use Python 3.13 or newer and `uv`. From this directory:

```sh
uv run --extra test pytest
uv build
```

The build uses ordinary isolated setuptools dependencies; no application install
is needed. To check the installed wheel outside this source directory, use a
separate temporary directory and environment:

```sh
check_dir=$(mktemp -d)
uv venv --python 3.13 "$check_dir/venv"
uv pip install --python "$check_dir/venv/bin/python" \
  ./dist/amplifier_workspace_metadata-0.2.0-py3-none-any.whl 'pytest>=8'
cp -R tests "$check_dir/tests"
(cd "$check_dir" && ./venv/bin/python -I -m pytest tests)
"$check_dir/venv/bin/python" -I -c \
  'import amplifier_workspace_metadata as m; print(m.__file__)'
```

Confirm the import resolves inside that environment and Unified/Core/Foundation
are absent. The source distribution includes the same direct tests. Host action,
native history and shell integration tests remain in Unified; standalone checks
do not establish those promises or untested platform/interpreter support.

The package version alone does not identify published or qualified bytes; use the
actual release source receipt and artifact hashes. Unified includes an explicitly
selected public artifact in its own distribution, independently of author edits.
**Do not coinstall `amplifier-workspace-metadata` with Unified:** both would claim
the same import package. Repository publication and independent artifact adoption
are separate steps. The planned supporting repository name remains
`amplifier-platform-workspaces`; this package covers only metadata.

## Component artifact delivery

The `Workspace metadata package` workflow qualifies pull requests on
Linux/Python 3.13. The release owner can dispatch it from `main` with an exact
merged source commit. It builds the wheel and source archive once, then installs
that same wheel in a separate environment and runs the existing metadata tests
outside the checkout. This does not establish native Windows or macOS acceptance.

Publication produces four assets under `workspace-metadata-vX.Y.Z`: the wheel,
source archive, `workspace-metadata-source.json`, and `SHA256SUMS`. The receipt
binds the exact source revision, project files and artifact hashes. Verify the
checksums before installing into a separate standalone environment. No app,
Core, Foundation, sibling checkout or runtime dependency is required. A PR never
publishes; the component release never replaces the application latest release.

For interrupted publication, retain the original build artifact and rerun only
failed jobs. Rebuilding is not a byte-reproducibility promise. Existing tags and
assets must match exactly; mismatches remain for review rather than being
overwritten. A completed release retry only verifies its existing bytes.
Publication does not change Unified's selected package or workspace behavior.

## Unified artifact selection

Unified selects the four verified public release assets explicitly. From the
Unified repository, use the published receipt's revision and checksum:

```sh
python scripts/scheduling_delivery.py select --component workspace-metadata \
  --dist /path/to/verified-release-assets \
  --revision 8b82dc82119ca5fc19f4be976d48f50c73754945 \
  --receipt-sha256 e19a04e01908992dfe970bd48d14dc58e4b502b7629d9d8e145e26fcd316a911
python scripts/scheduling_delivery.py verify --component workspace-metadata
```

The selection creates `components/workspace-metadata-delivery/` and
`components/workspace-metadata.source.json`. Never edit the generated copy by
hand. Normal builds verify it offline and include only its runtime package,
provenance and license; they do not fetch artifacts or consult this author tree.
Qualify the installed app before adopting a new selection. Ordinary app rollback
restores the prior selected code while preserving the caller's workspace records;
selection does not migrate identities, history or settings.
