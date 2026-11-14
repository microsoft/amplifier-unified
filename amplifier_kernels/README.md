# Persistent computation mechanism

`Kernels(transport, observe, runtimes)` maintains Python or Node variables across
cells in an owned process. The package uses only the standard library. It does
not launch processes itself, evaluate submitted code in the host interpreter,
select accounts or grant permissions. Callers provide authorized process
operations, a durable observation sink and trusted absolute runtime paths.

The transport owns `list`, `start`, `write`, `wait` and `terminate` effects. The
observer records kernel and cell identities, generations, output and honest
outcomes. One cell executes at a time. Reset requires observed termination;
lost ownership or uncertain delivery never replays code or reconstructs variables.
The adjacent Python and Node runners execute in those owned child processes.
Node's VM is a persistent namespace, not a security boundary. Existing process
authority and cleanup still apply.

## Build and test independently

Use an owned Python 3.13 or newer environment. From this directory:

```sh
uv build
uv venv .venv
uv pip install --python .venv/bin/python "dist/amplifier_kernels-0.1.0-py3-none-any.whl[test]"
```

Before running the tests, install the public `operations-v0.1.0` wheel
(`amplifier-operations` 0.1.0, SHA-256
`05675519079bbfb75be9ce0bc6fcba8c4a4ba6fd06dc8105ca706aec5ac43a1a`),
`amplifier-module-tool-bash` from public commit
`d5bb46d24dcae8e260211bf8fb5c22de3a67f406`, and the compatible public
`amplifier-core` 2.0.2 wheel into this same environment. BashTool imports Core
without declaring it; this is a test-peer requirement, not a kernels runtime
dependency. Include the peers' declared dependencies. Do not install Unified to
obtain these peers or build Core from source. The
[kernels workflow](../.github/workflows/kernels-release.yml) pins the Linux
publication-test wheels by SHA-256 and uses Node 22.23.3. It installs the original
kernels wheel outside the checkout and requires all ten cases without skips.

Run `.venv/bin/python -m pytest -q tests` after installing those prerequisites.
Python and Node must both be available to exercise all ten mechanism cases.
A missing BashTool or Node causes existing fixture skips, which are limitations,
not successful real-process qualification. The cases cover persistent state,
runtime identity, reset, interruption, bounded output, damaged protocol, unknown
termination, denied start and durable finalization under reader cancellation.
No account or model call is required. This preparation is qualified on Linux;
it does not establish Windows process transport support.

To verify the installed artifact, copy only `tests/` and this `pyproject.toml`
outside the source checkout and run there without a source `PYTHONPATH`. The wheel
contains the four runtime files, including `node_runner.cjs`; the source archive
also carries these tests. Standalone editable development uses this same source.

## Authoring and delivery

This directory is the canonical authoring home. Unified still bundles these
unchanged files in its ordinary wheel and editable installation. Its host tests
retain mounted tool approval, question dependencies, conversation selection,
recovery and execution authority. The in-place layout preserves its existing
runtime bootstrap origin checks.

Released [ACP 0.1.7](https://github.com/microsoft/amplifier-app-acp/releases/tag/v0.1.7)
selects this public project at Git commit
`8f4c05c53e5343a39433bc4a64f0d9e0104ce0e8`, subdirectory `amplifier_kernels`.
Kernel changes originate here; each consumer selects and qualifies its own update.
The standalone producer does not change that dependency or Unified's bundled
source, fetch packages at runtime, or automatically synchronize consumers.

## Standalone publication

The existing component publisher accepts `--component kernels`. Its assets are
`amplifier_kernels-0.1.0-py3-none-any.whl`, `amplifier_kernels-0.1.0.tar.gz`,
`kernels-source.json` and `SHA256SUMS`, under `kernels-v0.1.0`. The receipt binds
both artifacts to the exact public Git source, including `node_runner.cjs` and
the maintained tests. The workflow builds once and qualifies that same wheel;
PRs qualify without publishing. After review and merge, the sole release owner
may dispatch `kernels-release.yml` on `main` with the full merged revision.
Existing tags and asset bytes are immutable; inspect saved draft state after an
uncertain operation before retrying the ordinary publisher.

Workflow support is not a publication or consumer-adoption receipt. The `0.1.0`
package version and repository ownership are unchanged. Use separate environments
for the standalone distribution and Unified: do not coinstall them while both
own `amplifier_kernels` package files.
