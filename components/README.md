# Component projects

[`amplifier_kernels/`](../amplifier_kernels/README.md) prepares the independently
buildable computation mechanism in its existing source location, preserving
runtime bootstrap origin checks. Its real Python/Node tests use selected process
and operation-journal peers without starting Unified. The host retains authority,
question and recovery tests. Unified bundles this source; the ACP adapter's
temporary delivery copy requires a separate qualified update when it changes.

[`outputs/`](outputs/README.md) prepares the independently buildable output
relationship, saved-body decoder and read-only Git evidence package. Its existing
Git/SQLite mechanism tests run without Unified or Core; host authorization,
transactions, resource storage and retention stay in Unified. Normal Unified
builds the same source directly, without a separately installed outputs dependency.

[`portability/`](portability/README.md) is the temporary authoring home for the
independently buildable signed transfer journal and Git capsule package. Its
standalone development uses the prepared worktrees artifact; the local dependency
and installed-test instructions are in its README. Unified builds both source
owners directly, without a separately installed portability/worktrees dependency.

[`worktrees/`](worktrees/README.md) is the temporary authoring home for the
independently buildable managed Git worktree package and its real-Git tests.
Unified consumes the same sources directly. From that directory, `uv build` builds
the standalone wheel/source distribution and `uv run --extra test pytest` runs its
mechanism tests; no Unified or Core installation is needed. Normal Unified builds
already include the package: do not coinstall its standalone distribution there.
The future repository and delivery transition remain separate decisions.

[`publishing/`](publishing/README.md) is the temporary authoring home for the
independently buildable static publishing package and its portable tests. Unified
consumes its nested runtime sources directly; its host publishing adapters and
authorization remain in Unified. It stays here until a separate repository and
authoring handoff are ready.

[`scheduling/`](scheduling/README.md) is the temporary authoring home for the
independently buildable scheduling package. Unified consumes its nested runtime
sources directly in normal editable installs, wheels and source distributions.
It stays here until its separate repository and authoring handoff are ready.
The web client has a different ownership arrangement, described below.

## Web client ownership and assembly

The [Amplifier web client](https://github.com/microsoft/amplifier-client-web) owns
browser source, client tests, production dependencies and its build tools.
That repository is currently private; this is not permission to make it public.
Until an approved repository-delivery cutover, **client changes intended for
Unified must be maintained in both repositories**: author in the client repository,
then refresh the committed [`web-client/`](web-client/) delivery copy here.
[`web-client.source.json`](web-client.source.json) selects the canonical commit,
source digest and delivery location. This is one authoring owner and one required
selected delivery copy, not two places to develop the client.

Until the client repository is public and its review is complete, release
acceptance verifies that the canonical selected commit and this entire copy stay
byte-for-byte synchronized. Notify both owners when changing either side and
carry source, selection record and generated assets through the same handoff.
A qualified pinned snapshot need not equal the latest client branch tip: record
accepted upstream changes not yet delivered and assign their next handoff rather
than silently mixing them into an already qualified artifact.

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

Choose a new task-owned directory. Preparation verifies selected source and
carries `.client-source.json`. In a Git-free source export, this standalone command
copies only the bounded production manifest; use normal root `prepare` or the
canonical client checkout for client tests. No Git metadata, dependencies or local
state is copied. `prepare:inputs` retrieves
only the exact public files pinned by the client; its README also documents
offline input preparation. A paired-server declaration is not a compatibility
claim. The selection record is not independent authentication if both source and
record are replaced.

## Refresh and retire the copy

A client-only PR merge is source acceptance, **not delivery in Unified**. The
responsible owner must carry both locations through this workflow or make an
explicit handoff to a named integration owner with exact client and Unified
PRs/revisions and remaining work marked pending. Coordinate with the current
integration owner rather than duplicating a port or build campaign.
A canonical-only or copy-only change is not release-ready; a named handoff leaves
integration pending, not waived.

1. Commit changes in the canonical client first. If work began in Unified's
   copied tree or disposable prepared source, port it upstream before refreshing.
   Review the selected revision against the current client and Unified selections;
   preserve other writers' work and later accepted changes, not just the feature.
   Pin the exact reviewed and accepted canonical commit, not latest `main` or a
   merge commit unless that complete tree is accepted.
2. From the clean selected client commit, run
   `node scripts/client-source.mjs record /tmp/client-source.json`, then export its
   whole tracked tree with `git archive HEAD`. Replace only `components/web-client/`
   and update `components/web-client.source.json` together, retaining
   `delivery: {"type":"directory","path":"components/web-client"}`. Do not cherry-pick
   files into the copy or roll selection back to an older feature-only snapshot.
   Compare the complete tracked tree (file contents and modes) with that exact
   commit as well as the selection revision and digest; a bounded production-input
   digest alone does not establish whole-tree equality.
3. Use the existing [prepare, test and build workflow](#prepare-test-and-build)
   and [release policy](../docs/validation/release-qualification.md) for affected
   checks and build ownership. Preparation verifies the exact selection; build
   and `verify` check artifact provenance. Neither `.build/web-client` nor
   `amplifier_web/static` is an alternative source owner.
4. Record exact client and Unified PRs/commits, selected source revision, affected
   checks and any pending integration. Distinguish source acceptance, committed
   copy selection, build/paired qualification and Unified release; do not report
   an end-to-end change complete while an intended delivery stage remains pending.

Shared catalogs, SDK and normative contracts retain their existing owners. No
wrapper package, lock or duplicate client tests are generated in Unified.

Public visibility alone does not retire this obligation. Only an approved
public/repository-delivery cutover may change delivery to `{"type":"repository"}`;
remove the copied tree and update this guide and agent guidance in the same review.
Preparation then fetches the exact commit through Unified's repository-access
policy. Client verification, artifact format and runtime do not change with
delivery mode.

The copied tree is opt-in source-review context and excluded from Unified's
Python source distribution. Inspect selected source with
`python scripts/source_context.py --all --format paths --under components/web-client`.
Ordinary searches do not apply those attributes. Retire obsolete owned prepared
copies and dependencies after review, retaining useful finite evidence and
recoverable commits. Refreshing the copy changes the full-tree release identity;
historical evidence is never relabelled or exempted.
