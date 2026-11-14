# Task transfer mechanism

This package owns signed transfer journals (`TransferNode`) and bounded Git
capsules. It does not run an agent, contact a provider, transport files over the
network or convert native session histories. The embedding host supplies task
authorization, checkpointing, native writer fencing, destination readiness and
execution admission. Unified's [task portability guide](https://github.com/microsoft/amplifier-unified/blob/main/docs/TASK-PORTABILITY.md)
describes that host workflow and its existing contract.

`TransferNode` retains private signing identities, configured peer keys, exact
receipt matching, revision/generation ordering and durable transfer outcomes.
A destination's readiness receipt is not source release. Source release is
irreversible; lost acknowledgements and incomplete effects remain inspectable
without restoring source ownership or replaying work. Cancellation and destination
discard retain their existing signed receipts and evidence.

`capture_workspace` and `restore_workspace` preserve the exact base commit and
reviewed staged, unstaged and untracked changes. The destination must already
have the commit; no Git objects, remotes or source settings travel in the capsule.
Existing validation, size bounds and partial-destination preservation remain
unchanged. Packages are signed, **not encrypted**. The bounded credential filter
rejects recognizable secrets without rewriting history; it is not proof that
arbitrary content is confidential. The host owns review and private transport.

## Standalone development

Use Python 3.13 or newer and Git on `PATH`. Runtime dependencies are `cryptography`,
`filelock` and the separately prepared `amplifier-worktrees==0.1.0` artifact.
No Unified, Core, Foundation, provider or account installation is needed for the
mechanism tests. They use real Git, signing keys and synthetic private files.

Standalone delivery selects the public worktrees artifact recorded in
`worktrees-source.json`. Download all four assets from that receipt's component
release into an owned directory, compare the downloaded receipt byte-for-byte
with this selection, and verify `SHA256SUMS` there. The receipt binds the exact
wheel/source hashes and source revision; a version match alone is insufficient.
No sibling worktrees checkout or assumed PyPI publication is needed.

For local development, install that verified wheel explicitly alongside this
project (replace the wheel path):

```sh
uv venv
uv pip install /path/to/amplifier_worktrees-0.1.0-py3-none-any.whl '.[test]'
.venv/bin/python -m pytest
```

For an independently published installation, download the portability release's
four assets into a separate directory and verify its `SHA256SUMS`. Install both
verified wheels together into a fresh standalone environment:

```sh
uv venv /path/to/portability-env
uv pip install --python /path/to/portability-env/bin/python \
  /path/to/worktrees/amplifier_worktrees-0.1.0-py3-none-any.whl \
  /path/to/portability/amplifier_portability-0.1.0-py3-none-any.whl
```

This explicit two-artifact path also satisfies the package's exact worktrees
version requirement. Do not substitute a sibling source tree or copy its helper
implementations into this package. Retain both source receipts and artifact hashes.

`uv build` creates this package's wheel and source distribution. For an installed
check, install both exact wheels and pytest in a separate environment, copy this
project's `tests/` there, and run them outside either source checkout. Keep
package-only fixtures separate from full host/native/provider transfer acceptance.
Qualification covers Linux ARM64/Python 3.13; POSIX directory-sync, file modes and
path assumptions do not establish Windows or macOS acceptance.

The package version alone does not identify published or qualified bytes; use the
actual release source receipts and artifact hashes.
Unified already bundles these runtime sources and the worktree package from their
single authoring owners. Do not coinstall either standalone distribution into
Unified. Separate repositories and Unified dependency adoption remain separate
reviewed delivery decisions.


## Component artifact delivery

The `Portability package` workflow qualifies pull requests on Linux/Python 3.13.
The release owner can dispatch it from `main` with an exact merged source commit.
It builds the wheel and source archive once, verifies the selected public
worktrees dependency, and runs the existing real Git/signing/recovery tests from
outside both source checkouts with those two installed wheels. It then publishes
only portability's wheel, source archive, `portability-source.json`, and
`SHA256SUMS` under `portability-vX.Y.Z`, without selecting the application latest
release or changing Unified's dependencies. A PR never publishes.

The dependency receipt is delivery selection, not a second worktrees authoring
owner or a new release identity. It is included in the source archive so consumers
can recover the exact two-artifact installation. No worktrees implementation or
second distribution metadata is bundled in the portability wheel.

For an interrupted publication, retain the original build artifact and rerun only
failed jobs. Rebuilding is not a byte-reproducibility promise. Existing tags/assets
must match exactly; mismatches are preserved rather than overwritten. A completed
release retry only verifies the existing bytes.
