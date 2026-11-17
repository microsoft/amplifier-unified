# Component projects

[amplifier-platform-operations](https://github.com/bkrabach/amplifier-platform-operations)
owns operation observations, delivery cursors and their direct tests. Unified
selects its published artifact into generated `amplifier_operations/`, preserving
editable and isolated-worker imports. The host retains admission, permissions,
Stop, cancellation and execution policy. Publication does not change the app's
selected default; follow the [artifact selection instructions](#select-an-operations-artifact).

[`status/`](status/README.md) authors the dependency-free local status publisher,
reader and optional hook. Unified selects the published artifact into generated
`amplifier_web/status_protocol`, bound by `status-emitter.source.json`. Its private
import and CLI remain distinct from standalone `amplifier_status` and
`amplifier-status`, preserving coinstallation and uninstall isolation. Author
edits and publication do not refresh the app's selected bytes or enable emission.

[`recall/`](recall/README.md) prepares the existing conversation search and
versioned-memory storage package for independent development and installed tests.
Unified consumes the published artifact selected into `recall-delivery/`, recorded
by `recall.source.json`; author edits and publication do not refresh this generated
copy. Source access, authorization, opt-ins,
context assembly and model execution stay in the host. Existing imports and
database formats are unchanged, including durable explicit notes and receipts.
Do not coinstall the standalone distribution with Unified.

[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces)
owns the independently buildable workspace registration metadata package and its
direct tests. Unified consumes the published artifact selected into
`workspace-metadata-delivery/`, recorded by `workspace-metadata.source.json`, and
keeps host selection, filesystem, native history, authority and persistence.
Author edits and publication do not refresh this generated copy. The standalone
distribution must not be coinstalled with Unified. This metadata boundary does
not extract the full workspace domain. See the
[artifact selection instructions](#select-a-workspace-metadata-artifact).

[`kernels/`](kernels/README.md) authors the independently buildable computation
mechanism. Unified consumes its published artifact at generated
`amplifier_kernels/`, selected by `kernels.source.json`; the root location preserves
runtime bootstrap origin checks. Its real Python/Node tests use selected process
and operation-journal peers without starting Unified. The host retains authority,
question and recovery tests. Author changes and publication do not update the app
selection or ACP's separate public Git dependency. Unified remains the sole
installed owner; do not coinstall the standalone distribution.

[`outputs/`](outputs/README.md) authors the independently buildable output
relationship, saved-body decoder and read-only Git evidence package. Its existing
Git/SQLite mechanism tests run without Unified or Core; host authorization,
transactions, resource storage and retention stay in Unified. Unified consumes the
published artifact selected into `outputs-delivery/`, recorded by
`outputs.source.json`. Author edits and publication do not refresh this generated
copy; Unified remains the sole installed owner. Follow the
[selection instructions](outputs/README.md#unified-artifact-selection), and do not
coinstall the standalone distribution.

[`portability/`](portability/README.md) is the temporary authoring home for the
independently buildable signed transfer journal and Git capsule package. Its
standalone development uses the published worktrees artifact; the local dependency
and installed-test instructions are in its README. Unified consumes the generated
`portability-delivery/` and `worktrees-delivery/` selections, preserving the exact
published dependency receipt; author edits do not refresh either app selection.

[`worktrees/`](worktrees/README.md) is the temporary authoring home for the
independently buildable managed Git worktree package and its real-Git tests.
Unified consumes its published artifact selected into `worktrees-delivery/`,
recorded by `worktrees.source.json`. From the author directory, `uv build` builds
the standalone wheel/source distribution and `uv run --extra test pytest` runs its
mechanism tests; no Unified or Core installation is needed. Normal Unified builds
already include the package: do not coinstall its standalone distribution there.
The future repository handoff remains a separate decision.

[`publishing/`](publishing/README.md) is the temporary authoring home for the
independently buildable static publishing package and its portable tests. Unified
consumes the public artifact selected into `publishing-delivery/`, recorded by
`publishing.source.json`; its host publishing adapters, authorization and policy
remain in Unified. Author edits and publication do not refresh this generated
copy; Unified remains the sole installed owner. Follow the
[selection instructions](publishing/README.md#unified-artifact-selection), and do
not coinstall the standalone distribution. The author project stays here until
a separate repository and handoff are ready.

[amplifier-platform-scheduling](https://github.com/bkrabach/amplifier-platform-scheduling)
owns the independently buildable scheduling policy/store and its standalone tests.
Unified consumes the explicitly selected published wheel through the generated
`scheduling-delivery/` snapshot and [`scheduling.source.json`](scheduling.source.json).
Author changes and component publication do not change that app default. The app
remains the sole installed distribution owner; this is artifact assembly, not a
separately updatable pip dependency. Do not restore a second author copy here.

## Select an Operations artifact

Unified selects Operations 0.1.0 from the public repository's
[`operations-v0.1.0` release](https://github.com/bkrabach/amplifier-platform-operations/releases/tag/operations-v0.1.0).
`operations.source.json` binds its original receipt and artifact hashes. The
public repository owns source, standalone tests and publication; do not restore
an author copy or publication workflow here. Historical receipts retain their
original source identities.

Download the wheel, source archive, `operations-source.json` and `SHA256SUMS`
into a private directory. Verify the original receipt's SHA256 against the
reviewed publication, then run:

```sh
python scripts/scheduling_delivery.py select --component operations \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component operations
```

The selector verifies the artifact bytes and refuses a locally changed delivery
copy. Normal wheel, source archive and editable builds verify the selected
snapshot offline without a producer checkout. Unified remains the sole installed
owner and includes the original provenance and license under
`amplifier_web/component_delivery/`. Do not edit generated files or coinstall the
standalone distribution with Unified.

This selection preserves operation receipts, retry identities and delivery
cursors. Ordinary app rollback restores code while preserving newer caller-owned
state; it never authorizes replay of effects.

## Select a workspace metadata artifact

Unified selects Workspace Metadata 0.2.0 from the public repository's
[`workspace-metadata-v0.2.0` release](https://github.com/bkrabach/amplifier-platform-workspaces/releases/tag/workspace-metadata-v0.2.0).
The original receipt and artifact hashes are retained in
`workspace-metadata.source.json`. The public repository owns publication; the old
Unified workspace publication workflow is retired. Historical artifact verification
remains supported.

Download the release's wheel, source archive, `workspace-metadata-source.json`
and `SHA256SUMS` into a private directory. Verify the original receipt's SHA256
against the reviewed publication, then run:

```sh
python scripts/scheduling_delivery.py select --component workspace-metadata \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component workspace-metadata
```

The selector verifies the original artifact bytes and refuses a locally changed
delivery copy. Normal wheel, source archive and editable builds verify the selected
snapshot offline; rebuilding the source archive requires no producer checkout or
Git history. Review the selection record and qualify the installed package before
release. Do not edit generated files or coinstall the standalone distribution.
Unified remains the sole installed owner and carries the original provenance and
license under `amplifier_web/component_delivery/`.

Selecting this package does not move files, allocate workspace identities, change
permissions or migrate settings/history. Ordinary app rollback restores selected
code while preserving newer caller-owned workspace records.

## Select a Scheduling artifact

Unified selects Scheduling 0.1.0 from the public repository's
[`scheduling-v0.1.0` release](https://github.com/bkrabach/amplifier-platform-scheduling/releases/tag/scheduling-v0.1.0).
The source record binds its original publication and exact runtime bytes. Historical
Unified publication receipts remain unchanged; source handoff and component
publication do not automatically refresh this app selection. Never edit the
generated snapshot or rewrite original publication records.

Download a release's four published assets (wheel, source archive,
`scheduling-source.json` and `SHA256SUMS`) into a private directory. Verify the
source-receipt SHA256 against the reviewed publication, then run from Unified:

```sh
python scripts/scheduling_delivery.py select --component scheduling \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify
```

Selection checks the original wheel, source archive and receipt, and refuses a
locally modified delivery copy. It needs the reviewed artifacts and receipt
checksum, not the producer checkout or Git history. Review the generated selection
and any runtime requirement change together; Unified declares the current Windows
`tzdata` requirement. The app installs the selected provenance and license under
`amplifier_web/component_delivery/`.

Normal wheel, source archive and editable builds verify the selected snapshot
offline through Hatch. The source archive carries the snapshot, receipt and build
helpers, so rebuilding needs no Git, network or producer checkout. Editable installs
map the selected copy; develop the standalone library in its separate environment.
Do not coinstall `amplifier-scheduling` with Unified. The five policy/store cases
stay with the public package; Unified keeps host integration tests.

Ordinary app staging and rollback retain one installed owner and existing activation
rules. They do not update Scheduling independently, alter schedule databases,
discard newer schedules or replay uncertain run effects. Component publication
belongs to the public repository; the retained Unified package workflow publishes
Status only. Historical Scheduling artifact verification remains supported.

## Web client ownership and assembly

The [Amplifier web client](https://github.com/microsoft/amplifier-client-web) owns
browser source, client tests, production dependencies and its build tools.
[`web-client.source.json`](web-client.source.json) selects an exact accepted public
repository revision and production-input digest. Preparation fetches that revision
into ignored `.build/web-client`; no client source snapshot is committed in Unified.
A qualified selection need not equal the latest client branch tip: record accepted
upstream changes not yet delivered and assign their next handoff rather than
silently mixing them into an already qualified artifact.

Unified owns `scripts/web_client_build.py`, canonical shared inputs, package
assembly and [`tests/web`](../tests/web/README.md). The latter contains host,
browser-integration and input-producer checks with an explicit test dependency
lock. Client-only checks remain with the client. Shared generic test helpers have
one implementation; each owner injects its test-framework facade where needed.

## Prepare, test and build

Run from the Unified root in the owned development environment:

```sh
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
npm test --prefix .build/web-client
npm test --prefix tests/web
python scripts/web_client_build.py build
npm run test:artifact-assets --prefix .build/web-client
python scripts/web_client_build.py verify
```

These commands have separate scopes: client units, host integration/producer
units, production build, built-asset checks and installed-asset provenance.
Select affected checks under the existing release policy; this is not a new
mandatory full-suite release gate. Browser journeys use the registries described
in [tests/web/README.md](../tests/web/README.md), with owned Python fixtures and
matching Chromium where required.

Preparation creates ignored `.build/web-client` from the exact selection,
including its own package, lock and tests. It verifies current catalogs, SDK and
branding against the selected shared-input lock offline. The production build
uses that client's tools and installs its artifact in `amplifier_web/static`.
Reuse dependencies when their locks and toolchain match. Repository delivery
requires exact source preparation before a managed source snapshot is transferred;
missing preparation is an error, not a fallback to another client selection.

The client has an independent version. `static/client-component.json` records
its canonical source, shared inputs, build and asset hashes. Its paired-server
declaration identifies shared-input provenance, explicitly unqualified; actual
compatibility comes from Unified's exact-source test/release evidence. This record
does not hash itself; whole-static package checks include it. Released wheels and
source distributions contain complete assets and require no Node or client
checkout to run or reinstall. Raw source development requires Node.

## Local experiments

`python scripts/web_client_build.py dev` runs the prepared client's development
server. `python scripts/web_client_build.py build-development` installs deliberately
edited local output for an owned experiment or negative browser check, without a
qualifying component receipt. `.build/web-client` remains disposable: move desired
changes into the canonical client repository, then select that source. Preparation
refuses to overwrite unknown edits; save or discard experiments deliberately
before refreshing. A normal production build restores selected assets.

## Prepare a standalone copy

With Python, Git and Node available in the owned environment:

```sh
python scripts/web_client_source.py /tmp/my-web-client
cd /tmp/my-web-client
npm ci
npm run prepare:inputs
npm run build
npm run package:artifact -- /tmp/my-web-package PAIRED_SERVER_COMMIT \
  --source-record /tmp/my-web-client/.client-source.json
```

Choose a new task-owned directory. Preparation fetches and verifies the selected
public source and carries `.client-source.json`, including when Unified itself is
a Git-free source export. No Git metadata, dependencies or local state is copied.
`prepare:inputs` retrieves
only the exact public files pinned by the client; its README also documents
offline input preparation. A paired-server declaration is not a compatibility
claim. The selection record is not independent authentication if both source and
record are replaced.

## Select public client source

A client-only PR merge is source acceptance, **not delivery in Unified**. The
responsible owner must carry the selection through this workflow or make an
explicit handoff to a named integration owner with exact client and Unified
revisions and remaining work marked pending. Coordinate with the current owner
rather than duplicating a port or build campaign.

1. Commit and review changes in the canonical client first. Move any desired
   local experiment upstream before selection. Review the accepted revision
   against the current client and Unified selections; preserve later accepted
   changes rather than selecting an older feature-only snapshot.
2. From the exact accepted client commit, run
   `node scripts/client-source.mjs record /tmp/client-source.json`. Update
   `components/web-client.source.json` with its exact source revision and
   `inputsSha256`, retaining `delivery: {"type":"repository"}`. The first
   preparation needs Git, Node and access to that public source. An already
   prepared exact selection and its locked shared inputs can be reused offline.
   Do not use a moving branch or generate a replacement client lock.
3. Use the existing [prepare, test and build workflow](#prepare-test-and-build)
   and [release policy](../docs/validation/release-qualification.md) for affected
   checks. Preparation verifies the exact selection; build and `verify` check
   artifact provenance. Neither `.build/web-client` nor `amplifier_web/static`
   is an alternative source owner. Released wheel/sdist installs use their
   included assets without Git, Node or a client source fetch.
4. Record exact client and Unified PRs/commits, selected source revision, affected
   checks and any pending integration. Distinguish source acceptance, selection,
   build/paired qualification and Unified release. A selection edit alone does
   not deliver new client assets.

Shared catalogs, SDK and normative contracts retain their existing owners. No
wrapper package, lock or duplicate client tests are generated in Unified.

Preparation uses Unified's existing repository-access policy. Client verification,
artifact format and runtime do not change with delivery mode. Inspect source in
the canonical repository or its exact prepared selection; the visible source
record connects that selection to the shipped artifact.

Retire obsolete owned prepared copies and dependencies after review, retaining
useful finite evidence and recoverable commits. Historical evidence keeps its
original source and delivery identity.
