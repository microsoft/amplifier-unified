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
Unified selects a published artifact into the generated top-level
`amplifier_operations/` directory. That location preserves ordinary editable and
isolated-worker imports; this nested project remains the sole authoring owner.
`components/operations.source.json` binds the exact original receipt and wheel.
Author edits and component publication do not change the app default.

The package version alone does not identify published or qualified bytes; use the
actual release source receipt and artifact hashes.
Use a separate environment for the standalone distribution: do not coinstall it
with `amplifier-unified` while both distributions own the same package files.
Never edit the generated runtime snapshot by hand. Normal app wheel, source
archive and editable builds verify the committed selection offline, without
requiring the author tree to match. Return to a prior selection through ordinary
app rollback, preserving newer operation receipts and delivery cursors. This is
independent release consumption, not standalone pip replacement.

To select verified original release assets from the Unified root:

```sh
python scripts/scheduling_delivery.py select --component operations \
  --dist /path/to/downloaded-assets --revision FULL_SOURCE_SHA \
  --receipt-sha256 EXACT_OPERATIONS_SOURCE_SHA256
python scripts/scheduling_delivery.py verify --component operations
```

The original 0.1.0 receipt retains its historical `amplifier_operations` author
path. The producer also recognizes the current `components/operations` path at
newer revisions; moving the author project never rewrites old release identity.
The intended repository name is `amplifier-platform-operations`; repository naming
does not rename the existing Python import or its data contracts.


## Component artifact delivery

The `Operations package` workflow qualifies pull requests on Linux/Python 3.13.
The release owner can dispatch it from `main` with an exact merged source commit.
It builds the wheel and source archive once, then installs that same wheel into a
separate environment and runs the existing package tests outside the checkout.
The copied pytest configuration keeps the asynchronous notifier case enabled.
This qualification does not establish Windows or macOS acceptance.

Publication produces four assets under `operations-vX.Y.Z`: the wheel, source
archive, `operations-source.json`, and `SHA256SUMS`. The receipt binds the exact
source revision, project files and artifact hashes. Verify the checksums before
installing the wheel into a separate standalone environment. No app, native
runtime, sibling source checkout or runtime dependency is required. A PR never
publishes; the component release never replaces the application latest release.

For interrupted publication, retain the original build artifact and rerun only
failed jobs. Rebuilding is not a byte-reproducibility promise. Existing tags and
assets must match exactly; mismatches remain available for review rather than
being overwritten. A completed release retry only verifies the existing bytes.
