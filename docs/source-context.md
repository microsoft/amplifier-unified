# Finding source and its owners

Start with the changed behavior, its owner and the relevant contract. Use this
map to find those inputs without loading built bundles or old evidence into
every review. It describes current source entrypoints, not new authority or
proof of the target architecture.

## Select paths before reading content

Run from a checkout with Python's standard library and Git; no app environment
is needed. The command reads Git membership/attributes and file metadata only.

```sh
python scripts/source_context.py
python scripts/source_context.py --format paths --under amplifier_web/agent_state.py --under tests/test_agent_state.py
python scripts/source_context.py --format paths --kind contracts
python scripts/source_context.py --format json --under components/web-client.source.json
python scripts/source_context.py --format paths --kind evidence
python scripts/source_context.py --format json --all --under amplifier_web/static
```

The default summary counts every tracked path. Path/JSON output normally omits
`generated`, `evidence`, `assets` and `locks`; select a kind explicitly or use
`--all` to include them. It lists paths, never concatenates source. `--under`
matches exact files or directory prefixes, not globs. Select related tests and
consumers as well as the changed implementation: this is not dependency closure.
Use `--format paths --null` when passing filenames to another program.

Ordinary `rg` searches do not apply Git's context attributes. After choosing the
relevant roots, search their selected paths (this example uses GNU `xargs`):

```sh
python scripts/source_context.py --format paths --null --under tests/web | xargs -0 -r rg -n --with-filename -- 'stateRevision'
# In the canonical amplifier-client-web checkout:
rg -n -- 'stateRevision' src tests
```

`-r` prevents an empty selection from becoming an unrestricted search. Git also
supports attribute-aware searches without `xargs`, for example
`git grep -n 'stateRevision' -- ':(attr:source-context=tests)tests/web'`.
Both searches keep tests alongside source; add the actual consumers and contracts
for the changed behavior rather than treating directory selection as complete coverage.

[.gitattributes](../.gitattributes) owns classification and generated-diff hints.
The tool uses effective Git attributes, including local overrides, and index
membership with current working-tree sizes. Stage intended new files to include
them; untracked files, dependencies and private scratch work are not collected.
Unknown classifications stay visible by default. `--check` reports them with a
nonzero exit status before filtering; it is an optional maintenance check, not
a new release gate. Classify a new root deliberately instead of hiding it.

Generated output still ships and must pass existing build/package checks.
The [independent client selection](../components/README.md#select-public-client-source)
pins exact public source from its sole authoring owner,
[amplifier-client-web](https://github.com/microsoft/amplifier-client-web).
Its source record and maintenance guide stay visible here. Inspect client source
and tests in that repository, or use the exact disposable `.build/web-client`
prepared by the existing assembler. That ignored preparation is not part of this
Git source inventory. A client-only merge does not update Unified's selection or
packaged assets; selection, paired qualification and release remain explicit.
Scheduling is authored in the public
[amplifier-platform-scheduling](https://github.com/bkrabach/amplifier-platform-scheduling)
repository. Its selected artifact remains in generated `components/scheduling-delivery`,
bound by `components/scheduling.source.json`; see the
[selection guide](../components/README.md#select-a-scheduling-artifact).
Portability and publishing artifact snapshots follow the same author/delivery
distinction: `components/<name>` is authored, while `components/<name>-delivery`
is generated and opt-in. Visible `components/<name>.source.json` records the
selected public wheel. Use `--all --under components/<name>-delivery` to inspect
it; a new component source edit or publication does not silently refresh the copy.
Recall storage is authored in public
[amplifier-platform-recall](https://github.com/bkrabach/amplifier-platform-recall).
Unified's generated `components/recall-delivery` snapshot and `recall.source.json`
record preserve `amplifier_recall`; see the
[selection guide](../components/README.md#select-a-recall-artifact).
Output mechanisms are authored in public
[amplifier-platform-resources](https://github.com/bkrabach/amplifier-platform-resources).
Unified's generated `components/outputs-delivery` snapshot and `outputs.source.json`
record preserve `amplifier_outputs`; see the
[selection guide](../components/README.md#select-an-outputs-artifact).
Worktrees is authored in public
[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees).
Its generated `components/worktrees-delivery` snapshot and `worktrees.source.json`
selection stay paired with Portability's exact dependency receipt; see the
[selection guide](../components/README.md#select-worktrees-and-portability-artifacts).
Workspace metadata is authored in public
[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces).
Its generated selection uses `workspace-metadata-delivery` and
`workspace-metadata.source.json`; follow the
[selection guide](../components/README.md#select-a-workspace-metadata-artifact).
Operations is authored in public
[amplifier-platform-operations](https://github.com/bkrabach/amplifier-platform-operations);
its generated selection stays
at top-level `amplifier_operations` to preserve editable and isolated-worker import
paths. `components/operations.source.json` records its published artifact.
Use `--all --under amplifier_operations` to inspect that selected runtime.
Kernels follows the same root-layout rule: author in `components/kernels`,
select through `components/kernels.source.json`, and use
`--all --under amplifier_kernels` to inspect the generated Python/Node runtime.
Status is authored in `components/status`; the selected public artifact supplies
generated `amplifier_web/status_protocol`, bound by
`components/status-emitter.source.json`. Use
`--all --under amplifier_web/status_protocol` to inspect that private runtime. Its standalone
`amplifier_status` import and CLI remain separate, permitting coinstallation.
Source bootstrap files in the canonical client's `public/` remain authored inputs
in that repository. Shipped documentation in `docs/shell` is authored here. Frozen UX inventory/control
JSON is retained
evidence; its maintained delta and completion-policy ledgers remain test inputs.
Dated documents can contain current follow-ups, so documentation is not
automatically historical. Labels select review context, never deletion candidates.
The library-performance guide remains maintained documentation, including its
historical comparison. Exact collaboration qualification records and the older
reviewed-candidate manifest are opt-in evidence; the current canonical/accepted
catalog manifests remain test inputs. A generated-looking source file without a
reproducible provenance record, such as the device client's `observed-dom.js`,
stays visible for source review.

The individually listed live-control, schedule, budget and native-provider JSON
reports are retained evidence, including the failed continuity run. Their linked
guides remain visible for current direction, reproduction and evidence limits.

## Follow the canonical boundary

| Work | Current source entrypoints | Governing meaning |
| --- | --- | --- |
| Admission, action receipts and errors | [service](../amplifier_web/service.py), domain handlers and [save scope](../amplifier_web/action_save_scope.py) | [Current wire](clients/live-sessions.md), [client state](../contracts/client-state.v1.md) |
| Runtime and provider lifecycle | [runtime](../amplifier_web/runtime.py), [worker](../amplifier_web/runtime_worker.py), [host adapter](../amplifier_web/host) | [Agent boundary](../contracts/agent-protocol.v1.md), [request recovery](../contracts/model-request-recovery.v1.md); native Core/Foundation own native mechanisms |
| History, catalog and display | [shared state](../amplifier_web/shared_state.py), [history directory](../amplifier_web/history_directory.py), [cold display](../amplifier_web/cold_display.py) | [Working set](../contracts/working-set.v1.md), [state/storage design](architecture/state-and-storage.md); derived views are not new history authorities |
| Native message display and checkpoint anchors | [record projection](../amplifier_web/native_message_projection.py), [worker checkpoint](../amplifier_web/runtime_worker.py), [peer input](../amplifier_web/collaboration_input.py) | Existing raw native identities, visibility and provenance; no catalog, storage or configuration ownership |
| Browser transport, commands and private view | Author in [amplifier-client-web](https://github.com/microsoft/amplifier-client-web); [exact public source selection](../components/README.md#select-public-client-source): [main](https://github.com/microsoft/amplifier-client-web/blob/main/src/main.jsx), [state stream](https://github.com/microsoft/amplifier-client-web/blob/main/src/state-stream.js), [draft journal](https://github.com/microsoft/amplifier-client-web/blob/main/src/draft-journal.js) | [Client state](../contracts/client-state.v1.md), [client experience](../contracts/client-experience.v1.md); stream, action and optimistic state have distinct causality |
| Agent instructions and readbacks | [app guidance](../amplifier_web/app_guidance.py), [agent state](../amplifier_web/agent_state.py), [receipts](../amplifier_web/agent_receipts.py) | [Agent results](AGENT-RESULTS.md), client privacy and receipt contracts; optional skill absence does not remove supported actions |
| Domain capabilities | Relevant `amplifier_web` handler, portable `amplifier_*` package, frontend consumer and domain tests | Domain guide under `docs/`; [component boundaries](../contracts/component-boundaries.v1.md) |
| Output relationships and review evidence | [public mechanisms](https://github.com/bkrabach/amplifier-platform-resources), [host outputs](../amplifier_web/outputs.py), [transfer reader](../amplifier_web/portability_data.py) | [Output guide](OUTPUTS.md); host owns authorization, transactions, resource integrity and retention |
| Workspace registration and selection | [metadata owner](https://github.com/bkrabach/amplifier-platform-workspaces), [host selection](../amplifier_web/workspace_selection.py), [placement](../amplifier_web/workspace_placement.py) | [Placement](workspaces/contracts/placement.v1.md), [context](workspaces/contracts/context.v1.md); selection preserves native history identity and existing Canvas visibility |
| Shell, canvas and appearance | [Canvas rendering](../amplifier_web/workspace_canvas.py), [appearance package and CSS validation](../amplifier_web/appearance_packages.py), [shell](shell), [SDK](../packages/shell-sdk), [frontend shell](https://github.com/microsoft/amplifier-client-web/blob/main/src/shell) | Public extension/appearance contracts; saved user skins and independent components remain consumers |
| Test and release mechanisms | [Python tests](../tests), [browser registry](../tests/web/browser-checks.json), [scripts](../scripts) | [Performance validation](validation/performance.md), [release qualification](validation/release-qualification.md); exact evidence and independent modes remain distinct |

The [family vision](architecture/VISION.md) and [client direction index](clients/direction.md)
separate target design, current contracts and demonstrated behavior. Follow those
owners rather than copying normative rules into another inventory. For an
individual change, inspect its real producer, consumers and focused tests; a
directory name is a starting point, not proof of isolation.

Review generated assets through [source/build identity and verified hashes](validation/release-qualification.md#recovering-a-successful-frontend-build-after-asset-parity-fails).
Retain frozen workload revisions and failure/recovery evidence when linking a
new result. Fewer lines or bytes alone do not establish a simpler design.
