# Task transfer mechanism

This package owns signed transfer journals (`TransferNode`) and bounded Git
capsules. It does not run an agent, contact a provider, transport files over the
network or convert native session histories. The embedding host supplies task
authorization, checkpointing, native writer fencing, destination readiness and
execution admission. Unified's [task portability guide](../../docs/TASK-PORTABILITY.md)
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

The worktrees preparation is **not assumed to be publicly published**. Obtain
its exact reviewed wheel and install it explicitly alongside this project. With
`uv` available, run from this directory (replace the wheel path):

```sh
uv venv
uv pip install /path/to/amplifier_worktrees-0.1.0-py3-none-any.whl '.[test]'
.venv/bin/python -m pytest
```

Within this temporary Unified authoring tree, the sibling `../worktrees` project
can build that prerequisite with `uv build --wheel --out-dir /your/owned/wheelhouse
../worktrees`. Retain the selected source revision and wheel hash; a matching
preparation version alone does not identify the exact qualified bytes. Do not
copy its helper implementations into this package.

`uv build` creates this package's wheel and source distribution. For an installed
check, install both exact wheels and pytest in a separate environment, copy this
project's `tests/` there, and run them outside either source checkout. Keep
package-only fixtures separate from full host/native/provider transfer acceptance.
Qualification covers Linux ARM64/Python 3.13; POSIX directory-sync, file modes and
path assumptions do not establish Windows or macOS acceptance.

Version `0.1.0` records package preparation, not an independent published release.
Unified already bundles these runtime sources and the worktree package from their
single authoring owners. Do not coinstall either standalone distribution into
Unified. Separate repositories, publication and normal-install dependency selection
remain later reviewed delivery decisions.
