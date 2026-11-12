# Output relationships and Git evidence

`amplifier_outputs` stores output relationships in a caller-supplied SQLite
connection, decodes historical saved-body representations, and captures bounded
read-only Git review evidence. It uses Python's standard library; it does not
need Unified, Core, Foundation, a provider or a web client.

The caller owns transactions, authorization, source access, content storage,
integrity checks and serialization. The registry owns relationship revisions,
lineage, command receipts and local comments. Unlinking changes a relationship;
it does not delete the underlying work or its retained body. Git snapshots do
not apply changes or post comments. They disable conversion filters and external
diffs, exclude untracked files and binary contents, and retain exact displayed
hunk anchors. They are bounded evidence, not transactional repository snapshots.

`amplifier_outputs.content` preserves the `canvas-json-v1`, `canvas-image-v1`
and older encoded-body representations. Decoding a body is not authorization
to read it or proof that it matches its recorded hash; those checks remain with
the caller. The existing imports and stored formats are unchanged.

## Develop and qualify

Use Python 3.13 or newer with SQLite JSON functions, Git on `PATH`, and `uv`.
From this directory, run the existing real-Git and SQLite mechanism tests with:

```sh
uv run --extra test pytest
```

`uv build` produces a wheel and source distribution. For an installed-artifact
check, create a separate environment, install the local wheel with its `test`
extra, and copy `tests/` plus `pyproject.toml` to a directory outside this source
tree. Run that environment's `python -I -m pytest` there. Confirm imports resolve
inside the environment and that Unified/Core/Foundation are absent. The source
distribution includes the same tests. The Git fixtures use a POSIX shell; Linux
qualification alone does not establish Windows or macOS behavior.

This `0.1.0` is a preparation version, not a separately published release or a
new repository. Unified currently builds these same sources directly into its
own distribution. **Do not coinstall `amplifier-outputs` in a Unified environment**:
both distributions would claim the same import package. Repository delivery and
the broader resource-lifecycle boundary remain separate decisions.

In the Unified checkout, [the output guide](../../docs/OUTPUTS.md) describes host
actions, resource retention, authorization and integration behavior. Its host
tests remain with Unified; the standalone tests do not establish those promises.
