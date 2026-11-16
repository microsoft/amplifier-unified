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

## Current assembly boundary

This is an independent package preparation, not a published artifact selection.
Unified's ordinary wheel, sdist and editable builds still include this authored
package and remain its sole installed distribution owner. Do not coinstall the
standalone distribution in a Unified environment. No runtime dependency, lock,
database path or stored format changes with this move.

Independent publication and a generated delivery copy are later explicit steps
using the existing component release/selection workflow. The authored tree is
the only current source owner; do not fabricate a selection record or copy it
into a second writable runtime tree. A future supporting repository may use
`amplifier-platform-recall`; its creation and handoff are separate decisions from
the preserved distribution/import names.
