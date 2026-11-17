# Recall and memory storage

`amplifier_recall` stores derived conversation search data and explicit, versioned
memory records in a caller-selected SQLite database. Its existing imports are
unchanged: `RecallStore`, `amplifier_recall.store` and
`amplifier_recall.personalization`. It uses Python's standard library and SQLite
FTS5/JSON functions; it does not need Unified, Core, Foundation or a provider.

The caller supplies source records and allowed source IDs, verifies originals
before using recalled text, and owns authorization, scope and retention policy.
The store owns indexing, paging, note revisions, compare-and-swap mutations and
command receipts. Its transactions, database schema, private file permissions,
WAL and secure-delete behavior are unchanged.

The database is not all disposable index data: explicit notes, their retained
versions, settings, command receipts and suppression records are durable.
`Personalization` tracks caller-admitted attempts; constructing it marks
unconfirmed claims interrupted without replaying them. It never schedules a
model call. Unified retains opt-ins, source eligibility, context assembly,
model execution and Stop. The separate `amplifier-bundle-memory` package continues
to own extraction, quotation verification and relevance; this package does not
replace those mechanisms or native history.

## Develop and qualify

Use Python 3.13 or newer with SQLite FTS5/JSON support. From this directory:

```sh
uv run --extra test pytest
uv build
```

The source distribution includes these tests. For installed qualification, install
the wheel with its `test` extra into a separate environment without Unified, Core
or Foundation. Copy `tests/` and `pyproject.toml` outside this source checkout and
run that environment's `python -I -m pytest` there. Check that imports resolve to
the installed package. The tests cover full-library lexical ranking and scope,
durable note/receipt recovery, rollback and interrupted-attempt handling. They do
not establish host permissions, model behavior or native history compatibility.
The host tests remain in Unified's `tests/test_recall.py` and
`tests/test_memory_personalization.py`.

## Independent publication

The `Recall package` workflow (`.github/workflows/recall-release.yml`) qualifies
pull requests and publishes only when the release owner explicitly dispatches it
from `main` with a full merged source commit. Coordinate that owner before choosing
a version or dispatching publication.

It builds the standalone wheel and sdist once, binds their bytes and included
source/tests to the selected Git tree, and tests the installed wheel outside the
checkout without Unified, Core or Foundation. The four release assets are the
wheel, sdist, `recall-source.json` and `SHA256SUMS`, under `recall-v<VERSION>`.
Publication does not replace Unified's latest release or change its selected code.
Existing tags and assets are immutable: a completed exact-byte retry is read-only;
a partial draft can receive only its missing matching assets. Preserve the original
four assets for a retry because rebuilding can change archive bytes.

## Unified artifact selection

Unified's ordinary wheel, sdist and editable builds consume the generated
`components/recall-delivery/` snapshot selected by `components/recall.source.json`.
The selection records the original public wheel, source commit/tree and receipt
from [recall-v0.1.0](https://github.com/microsoft/amplifier-unified/releases/tag/recall-v0.1.0).
The authored project remains independent; source edits and publication do not
change Unified's selected default. Never edit the delivery copy by hand.

To select a coordinated release, download its original wheel, sdist,
`recall-source.json` and `SHA256SUMS` into an owned directory. Verify the checksums
and source receipt against the accepted publication, then run from the Unified root:

```sh
python scripts/scheduling_delivery.py select --component recall \
  --dist "$RECALL_ASSETS" --revision "$RECALL_REVISION" \
  --receipt-sha256 "$RECALL_RECEIPT_SHA256"
python scripts/scheduling_delivery.py verify --component recall
```

Use the actual published revision and receipt hash, and review the generated copy
and record together. Normal builds verify the selected bytes offline and include
the receipt/license in the app wheel. Unified remains the sole installed
distribution owner; do not coinstall the standalone distribution in its
environment. No runtime dependency, lock, database path or stored format changes.
The producer workflow owns standalone tests; Unified keeps its host integration tests.

A future supporting repository may use
`amplifier-platform-recall`; its creation and handoff are separate decisions from
the preserved distribution/import names.
