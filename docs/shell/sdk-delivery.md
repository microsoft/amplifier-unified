# Shell SDK delivery

[bkrabach/amplifier-shell-sdk](https://github.com/bkrabach/amplifier-shell-sdk)
owns `@amplifier/shell-sdk`, its JavaScript and type contracts, direct package
tests and controls example. Its GitHub release contains a qualified npm-format
tarball, `qualification.json` and `SHA256SUMS`. Selecting that tarball does not
require npm registry publication.

Unified records an exact release and source revision/tree in
[`components/shell-sdk.source.json`](../../components/shell-sdk.source.json).
The record binds the original tarball and qualification bytes plus every selected
member. `packages/shell-sdk` contains all eleven package members. The five files
at `examples/shell-controls` are a compatibility projection of the same package's
example. Both locations are generated; make SDK changes in the public owner.

## Select an accepted release

Retrieve the three original public release assets into one directory. Confirm
the accepted source revision/tree and original published checksums, then run:

```bash
python3 scripts/shell_sdk_delivery.py select --dist /path/to/original-assets \
  --revision FULL_SOURCE_COMMIT --tree FULL_SOURCE_TREE \
  --archive-sha256 ORIGINAL_TARBALL_SHA256 \
  --qualification-sha256 ORIGINAL_QUALIFICATION_SHA256
python3 scripts/shell_sdk_delivery.py verify
```

Selection checks the original assets and exact regular-file membership before
changing delivery. It refuses altered prior selections and restores both
directories and the old record if the commit fails. If restoration itself fails,
the error identifies the retained original bytes for recovery. Historical
receipts retain their original meaning; rollback selects a prior complete source
revision and its matching delivery record.

The public web client's `build-inputs.lock.json` binds SDK `index.js`, `LICENSE`
and `package.json`. Refresh that lock in the client owner and select the accepted
client revision through the existing web delivery workflow whenever these inputs
change. A package-metadata-only change still requires this coordination. Keep the
ordinary client-input verification enabled.

## Build and integration boundaries

The existing custom build hook verifies the selection offline and checks the
wheel's SDK, example and receipt members. Explicit force-includes preserve the
installed paths under `amplifier_web/bundle_data`; the selected receipt is shipped
at `amplifier_web/component_delivery/shell-sdk.json`. Wheel, editable and source
distribution installation introduces no Node or npm requirement. Client building
remains the existing separate release/development step.

The producer owns package, strict TypeScript and example qualification. Unified
retains `tests/test_shell_authoring_package.py`, `tests/test_shell_sdk_delivery.py`,
the host/browser checks and `scripts/examples/shell-example-build.mjs`. These
check assembly and real host compatibility without a second SDK test owner.
No slot, permission, renderer, trusted-code or runtime dependency contract changes
with artifact selection.
