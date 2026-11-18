# Component projects

[amplifier-platform-operations](https://github.com/bkrabach/amplifier-platform-operations)
owns operation observations, delivery cursors and their direct tests. Unified
selects its published artifact into generated `amplifier_operations/`, preserving
editable and isolated-worker imports. The host retains admission, permissions,
Stop, cancellation and execution policy. Publication does not change the app's
selected default; follow the [artifact selection instructions](#select-an-operations-artifact).

[amplifier-platform-status](https://github.com/bkrabach/amplifier-platform-status) authors the dependency-free local status publisher,
reader and optional hook. Unified selects the published artifact into generated
`amplifier_web/status_protocol`, bound by `status-emitter.source.json`. Its private
import and CLI remain distinct from standalone `amplifier_status` and
`amplifier-status`, preserving coinstallation and uninstall isolation. Author
edits and publication do not refresh the app's selected bytes or enable emission.

[amplifier-platform-recall](https://github.com/bkrabach/amplifier-platform-recall)
owns conversation search, versioned-memory storage and its direct SQLite tests.
Unified consumes the published artifact selected into `recall-delivery/`, recorded
by `recall.source.json`; author edits and publication do not refresh this generated
copy. Source access, authorization, opt-ins, context assembly, model execution and
Stop stay in the host. Existing imports and database formats are unchanged,
including durable explicit notes, versions, settings and receipts. Unified is the
sole installed owner; do not coinstall the standalone distribution or restore an
author copy here. Follow the [selection instructions](#select-a-recall-artifact).

[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces)
owns the independently buildable workspace registration metadata package and its
direct tests. Unified consumes the published artifact selected into
`workspace-metadata-delivery/`, recorded by `workspace-metadata.source.json`, and
keeps host selection, filesystem, native history, authority and persistence.
Author edits and publication do not refresh this generated copy. The standalone
distribution must not be coinstalled with Unified. This metadata boundary does
not extract the full workspace domain. See the
[artifact selection instructions](#select-a-workspace-metadata-artifact).

[amplifier-platform-kernels](https://github.com/bkrabach/amplifier-platform-kernels) authors the independently buildable computation
mechanism. Unified consumes its published artifact at generated
`amplifier_kernels/`, selected by `kernels.source.json`; the root location preserves
runtime bootstrap origin checks. Its real Python/Node tests use selected process
and operation-journal peers without starting Unified. The host retains authority,
question and recovery tests. Author changes and publication do not update the app
selection or ACP's separate immutable wheel selection. Unified remains the sole
installed owner; do not coinstall the standalone distribution.

[amplifier-platform-resources](https://github.com/bkrabach/amplifier-platform-resources)
authors the independently buildable output
relationship, saved-body decoder and read-only Git evidence package. Its existing
Git/SQLite mechanism tests run without Unified or Core; host authorization,
transactions, resource storage and retention stay in Unified. Unified consumes the
published artifact selected into `outputs-delivery/`, recorded by
`outputs.source.json`. Author edits and publication do not refresh this generated
copy; Unified remains the sole installed owner. Follow the
[selection instructions](#select-an-outputs-artifact), and do not restore an author
copy here or coinstall the standalone distribution.

[microsoft/amplifier-portability](https://github.com/microsoft/amplifier-portability)
owns signed handoff receipts, workspace capsules, indexed selected reads and
historical payload/evidence APIs. Its standalone recovery and mechanism tests
remain there; Unified retains admission, native fencing, data projection and
host policy. Generated `portability-delivery/` and `worktrees-delivery/` selections
preserve the exact published dependency receipt. Source changes and publication
do not refresh either app selection. See the
[paired selection instructions](#select-worktrees-and-portability-artifacts).

[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees)
is the public authoring owner for the managed Git worktree mechanism and its
real-Git tests. Unified consumes the exact published artifact selected into
`worktrees-delivery/`, recorded by `worktrees.source.json`. Follow the
[paired selection instructions](#select-worktrees-and-portability-artifacts).
The host retains active-task, permission, handoff and cleanup policy. Do not
restore an author copy here or coinstall the standalone distribution with Unified.

[microsoft/amplifier-publishing](https://github.com/microsoft/amplifier-publishing)
owns the static publishing package, private-service mechanism and their direct
tests. Unified consumes the published artifact selected into
`publishing-delivery/`, recorded by `publishing.source.json`; host publishing
adapters, authorization and policy remain in Unified. Source acceptance and
publication do not refresh this generated copy. Unified remains the sole installed
owner; follow the [selection instructions](#select-a-publishing-artifact), and do
not restore an author copy or coinstall the standalone distribution.

[amplifier-platform-scheduling](https://github.com/bkrabach/amplifier-platform-scheduling)
owns the independently buildable scheduling policy/store and its standalone tests.
Unified consumes the explicitly selected published wheel through the generated
`scheduling-delivery/` snapshot and [`scheduling.source.json`](scheduling.source.json).
Author changes and component publication do not change that app default. The app
remains the sole installed distribution owner; this is artifact assembly, not a
separately updatable pip dependency. Do not restore a second author copy here.

## Select a Publishing artifact

The public [Publishing repository](https://github.com/microsoft/amplifier-publishing)
owns source and standalone publication. `publishing.source.json` is the authority
for Unified's selected source, original receipt and artifact bytes; a public
source merge or release does not change that selection. Historical Unified
publication receipts remain valid and are not rewritten by this handoff.

Download the reviewed release's wheel, source archive, `publishing-source.json`
and `SHA256SUMS` into a private directory. Verify the original receipt SHA256
against the reviewed publication, then run:

```sh
python scripts/scheduling_delivery.py select --component publishing \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component publishing
```

The selector verifies original artifacts and refuses locally changed delivery.
Normal wheel, editable and Git-free source-archive builds verify the selected
snapshot offline; there is no author-source fallback. Review the selection and
qualify the installed package before a Unified release. Unified is the sole
installed owner and carries the original license and provenance under
`amplifier_web/component_delivery/`; do not coinstall the standalone distribution.
The old Unified Publishing publication workflow is retired.

This handoff preserves store formats, immutable release identities, receipts,
pins, opted-in retention and unknown-outcome recovery. It does not move user data,
activate expiry, restart listeners or replay work. Host authorization, serving
policy and remote-target integration stay in Unified. Package publication
controls GitHub artifacts; it does not add GitHub release automation to the
Publisher domain.

## Select a Recall artifact

The public Recall repository publishes `amplifier-recall` under `recall-vX.Y.Z`,
preserving the `amplifier_recall` import and existing SQLite formats. Download the
original wheel, source archive, `recall-source.json` and `SHA256SUMS` from one
accepted release. Verify the receipt and assets, then select that exact public
receipt through the existing assembler:

```sh
python scripts/scheduling_delivery.py select --component recall \
  --dist /path/to/verified-recall-assets --revision FULL_SOURCE_SHA \
  --receipt-sha256 ORIGINAL_PUBLIC_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component recall
```

The generated `recall-delivery/` and `recall.source.json` travel together. Qualify
the packaged, editable and source-archive builds before the normal Unified
release; component publication alone does not change the app's selected code.
Unified remains the sole installed owner. Historical monorepo receipts remain
valid for explicit selection and ordinary app rollback, without migrating or
deleting durable notes, versions, settings, command receipts or suppression
records. This handoff does not replace Foundation's separate optional Recall API.

## Select an Outputs artifact

The public Resources repository publishes `amplifier-outputs` under `outputs-vX.Y.Z`;
the repository name does not rename its distribution or `amplifier_outputs` import.
Download the original wheel, source archive, `outputs-source.json` and `SHA256SUMS`
from one accepted release. Verify the receipt and assets, then select that exact
public receipt through the existing assembler:

```sh
python scripts/scheduling_delivery.py select --component outputs \
  --dist /path/to/verified-outputs-assets --revision FULL_SOURCE_SHA \
  --receipt-sha256 ORIGINAL_PUBLIC_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component outputs
```

The generated `outputs-delivery/` and `outputs.source.json` travel together.
Qualify the packaged, editable and source-archive builds before the normal Unified
release; component publication alone does not change the app's selected code.
Unified remains the sole installed owner. Historical monorepo receipts remain
valid for explicit selection and ordinary app rollback, without migrating or
deleting output relationships, command receipts or retained bodies.

## Select Worktrees and Portability artifacts

Worktrees is authored and published in
[amplifier-platform-worktrees](https://github.com/bkrabach/amplifier-platform-worktrees);
Portability is authored and published in
[microsoft/amplifier-portability](https://github.com/microsoft/amplifier-portability).
Retain the original public source receipts and four assets for each selection.
The package names remain `amplifier-worktrees` and `amplifier-portability`; source
repository names do not change their installed imports.

Unified selects the public Portability 0.1.3 artifact with the exact public
Worktrees 0.1.0 receipt it was qualified against. Historical Unified-produced
Portability receipts remain valid for explicit selection and ordinary app rollback;
they are not rewritten as part of this authoring handoff.

Download each release's wheel, source archive, source receipt and `SHA256SUMS`
into separate private directories. Verify both original receipt hashes against
the reviewed publications. Select Worktrees first, then its matching Portability:

```sh
python scripts/scheduling_delivery.py select --component worktrees \
  --dist /path/to/worktrees-assets --revision FULL_WORKTREES_SOURCE_SHA \
  --receipt-sha256 REVIEWED_WORKTREES_RECEIPT_SHA256
python scripts/scheduling_delivery.py select --component portability \
  --dist /path/to/portability-assets --revision FULL_PORTABILITY_SOURCE_SHA \
  --receipt-sha256 REVIEWED_PORTABILITY_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component worktrees
python scripts/scheduling_delivery.py verify --component portability
```

Portability must name the exact selected Worktrees source receipt and artifact;
matching versions alone do not suffice. The public package keeps its original
runtime requirement ranges. Unified retains its tighter cryptography/filelock
constraints and exact Worktrees source equality; dependency metadata is recorded
without rewriting it. Source authoring, standalone publication and Unified
selection remain separate. Keep both selections together in the reviewed change;
do not publish an intermediate mismatched pair.

For standalone library development and installed checks, use the public owner's
[instructions](https://github.com/microsoft/amplifier-portability#development).
Verify its original Worktrees receipt and assets before installing the two wheels
outside either author checkout. Never install them alongside Unified's copies.

Normal wheel, source-archive and editable builds verify both generated snapshots
offline, without their author checkouts. Unified remains the sole installed owner
and carries each original provenance record and license under
`amplifier_web/component_delivery/`. Historical receipts and releases retain their
original source identities. No worktree files, ownership records, transfer
journals or active work are moved or migrated; ordinary app rollback restores
code while preserving newer caller-owned state.

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

## Select a Status artifact

[amplifier-platform-status](https://github.com/bkrabach/amplifier-platform-status)
owns publication. Download its wheel, source archive, `status-emitter-source.json`
and `SHA256SUMS` into a private directory. Verify the original receipt against the
reviewed public release, then select it:

```sh
python scripts/scheduling_delivery.py select --component status-emitter \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component status-emitter
```

The selected snapshot remains private `amplifier_web.status_protocol` with the
`amplifier-unified-status` command. Standalone `amplifier_status`, `amplifier-status`
and its optional hook stay independently installable and removable. Qualify that
coinstallation boundary before release; do not edit the generated snapshot.
Selection neither enables emission nor changes host authority or stored records.
Historical receipts retain their original source identities and remain verifiable.
The old Unified author project and publisher are retired.

## Select a Kernels artifact

[amplifier-platform-kernels](https://github.com/bkrabach/amplifier-platform-kernels)
owns publication. Download its wheel, source archive, `kernels-source.json` and
`SHA256SUMS`; verify the original receipt against the reviewed public release:

```sh
python scripts/scheduling_delivery.py select --component kernels \
  --dist /path/to/release-assets --revision FULL_PUBLISHED_SOURCE_SHA \
  --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify --component kernels
```

Unified remains the sole installed owner of the selected `amplifier_kernels`
snapshot, including its Node runner. Normal, editable and Git-free source-archive
builds verify it offline; do not coinstall the standalone distribution or restore
an author copy here. The old Unified publisher is retired. This selection does not
change ACP's independent immutable wheel selection, process permissions, ongoing
work or recovery. App rollback preserves original receipts and newer user state.

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
