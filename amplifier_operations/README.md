# Operation observations and coordination delivery

This standard-library package records durable operation evidence and provides
bounded coordination delivery. It imports no app, agent engine, provider or UI.

`OperationJournal` owns its SQLite observations, output chunks and retry identities.
Callers supply the conversation, runtime and operation owner identity. Repeated
events preserve their result; conflicting identities or payloads are rejected.
Recovery marks unfinished work unknown without replaying it. Output retention
keeps outcome and deduplication evidence while making expired output explicit.

`coordination` provides attributed peer-input formatting, final-result checks,
bounded UTF-8 delivery with identity-bound cursors, and change notifications.
Applications supply the snapshot and retain authority over admission, permissions,
Stop, cancellation and execution. These helpers do not start or control work.

## Build and test independently

From this directory in an owned Python 3.13 or newer environment:

```sh
uv build
uv venv .venv
uv pip install --python .venv/bin/python "dist/amplifier_operations-0.1.0-py3-none-any.whl[test]"
.venv/bin/python -m pytest -q tests
```

On Windows, use `.venv\Scripts\python.exe` for the interpreter paths. The wheel
contains the three runtime files and distribution metadata. The source archive
also contains the standalone tests and synthetic event fixtures. They exercise
restart/unknown outcomes, duplicate ownership, partial output, prospective expiry,
indexed retention pages, cursor recovery and notification ordering without an
application boot, account, network service or sibling checkout.

To verify the installed wheel, copy only `tests/` and this `pyproject.toml` into a
separate directory and run its tests there; do not add the source to `PYTHONPATH`.
The test configuration enables the existing asynchronous notifier check.

## Ownership and delivery

These Python files remain the sole implementation. The pure package tests live
here; Unified retains host integration, permissions, Stop and real-process tests.
Unified still bundles and imports these unchanged `amplifier_operations` files.
This preparation adds no daemon, database migration, runtime dependency, new policy
or automatic package selection.

The `0.1.0` component version is preparation metadata, not a published release.
Use a separate environment for the standalone distribution: do not coinstall it
with `amplifier-unified` while both distributions own the same package files.
Independent publication and Unified dependency selection are later delivery steps.
The intended repository name is `amplifier-platform-operations`; repository naming
does not rename the existing Python import or its data contracts.
