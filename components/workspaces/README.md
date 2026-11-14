# Workspace registration metadata

`amplifier_workspace_metadata` owns registration edits, native observation
reconciliation, and list/search/paging over caller-owned records. It uses only
Python's standard library. It does not import Unified, Core or Foundation, read
folders, allocate identities, persist state, or authorize access.

The public exports are `register`, `edit`, `reconcile`, `listing`, and
`RegistrationReconciliation`. Callers supply the current mutable registration
list. Edits preserve existing row objects and custom fields. Reconciliation
returns ordered reference remaps for the caller to apply under its existing
lock. `listing(registrations, args)` returns shallow row copies with the current
query, label and paging rules; nested custom values retain their references.
The caller validates action inputs and resolved folders, and owns transactions,
selection, pins, Canvas effects, history and publication.

Current path-derived IDs, unresolved native-project placeholders and display
aliases retain their meanings. This package does not implement portable logical
identity, location bindings, shared context, filesystem grants or a second chat
catalog. Those remain separate workspace and conversation-owner contracts.

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
  ./dist/amplifier_workspace_metadata-0.1.0-py3-none-any.whl 'pytest>=8'
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
actual release source receipt and artifact hashes. Unified currently includes
these exact runtime sources in its own distribution.
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
