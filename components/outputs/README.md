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

The version alone does not identify published or qualified bytes; use the actual
release receipt and artifact hashes. Unified builds the explicitly selected public
artifact into its own distribution; author changes do not refresh that selection.
**Do not coinstall `amplifier-outputs` in a Unified environment**: both distributions
would claim the same import package. Repository delivery and the broader
resource-lifecycle boundary remain separate decisions.

In the Unified checkout, [the output guide](../../docs/OUTPUTS.md) describes host
actions, resource retention, authorization and integration behavior. Its host
tests remain with Unified; the standalone tests do not establish those promises.

## Independent artifact delivery

The `Outputs package` workflow builds one wheel and source distribution from an
exact selected commit, then runs the existing Git/SQLite tests against that wheel
in a separate Linux environment outside the checkout. It copies both `tests/` and
`pyproject.toml` so the package's asynchronous test configuration remains active.

After review and merge, the release owner can dispatch `outputs-release.yml` on
`main` with the full merged commit SHA. The workflow publishes those same original
artifacts under `outputs-vX.Y.Z`, alongside `outputs-source.json` and `SHA256SUMS`.
The receipt binds the source revision, project tree and files to the artifact
bytes. Component publication leaves the application's latest release unchanged
and does not select a new Unified dependency or refresh its bundled sources.

Existing tags and assets are never replaced. To resume an interrupted publication,
rerun the failed publication job with its original build artifacts. A full rebuild
may produce different archive bytes and is not an exact-byte retry. Once a release
exists, download and verify its receipt and checksums before installing its wheel
in a standalone environment. Independently selected Unified delivery remains a
separate adoption step.

## Unified artifact selection

From the Unified repository, select the four verified public release assets with
the published receipt's exact revision and checksum:

```sh
python scripts/scheduling_delivery.py select --component outputs \
  --dist /path/to/verified-release-assets \
  --revision 51551349e1cf437a40318e220dcd50e59680d4ef \
  --receipt-sha256 bc652d4bc87fa119faa6e66c98e61b33f0346923ed4aaf1928b98f8a2ff88456
python scripts/scheduling_delivery.py verify --component outputs
```

The selection creates `components/outputs-delivery/` and
`components/outputs.source.json`. Never edit the generated copy by hand. Normal
wheel, source-distribution and editable builds verify it offline; they do not
fetch artifacts or consult this author tree. Unified includes only the selected
runtime package, provenance and license, with no standalone outputs distribution.
The package's mechanism tests continue to run against its own authored wheel in
the independent publisher; Unified keeps the host integration tests.

Qualify the installed app before adopting a new selection. Ordinary app replacement
or rollback changes the selected code without migrating output relationships,
receipts or retained bodies. Transactions, authorization and retention remain
with the host.
