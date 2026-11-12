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

Before running the tests, install the selected `amplifier-operations` wheel and
the public managed-process `amplifier-module-tool-bash` artifact, including that
peer's declared dependencies, into this same environment. The operations project
currently has preparation artifacts rather than an independent published release;
build its exact selected source separately and supply that local wheel. Do not
install Unified to obtain these peers. Record both source/artifact identities.
The BashTool test peer may depend on Core; the kernels runtime does not.

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

The ACP adapter currently carries a byte-identical temporary delivery copy under
`amplifier_acp.native.kernels`. Changes must originate here and reach that copy
with exact source/content provenance and separate consumer qualification. This
preparation does not switch ACP imports or automatically synchronize that copy.
It adds no runtime fetch, package selection or new installation path.

The `0.1.0` version is preparation metadata, not a published release or a repository
choice. Use separate environments for the standalone distribution and Unified:
do not coinstall them while both own `amplifier_kernels` package files.
