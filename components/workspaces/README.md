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

`0.1.0` is a preparation version, not a published release or a new repository.
Unified currently includes these exact runtime sources in its own distribution.
**Do not coinstall `amplifier-workspace-metadata` with Unified:** both would claim
the same import package. Repository publication and independent artifact adoption
are separate steps. The planned supporting repository name remains
`amplifier-platform-workspaces`; this package covers only metadata.
