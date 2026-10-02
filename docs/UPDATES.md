# Updates

Available updates are listed immediately at the top of Settings. The application has its own card showing the installed version even before the first check, plus the latest published release after a successful check. The complete source inventory is behind **Show all sources**; failed checks are called out separately. Successfully installed application releases are removed from the pending count on restart. An installation newer than the published release is identified explicitly; application updates follow stable GitHub Releases, not every commit on main.

Automatic update checks default to every **4 hours**. Under **Update preferences**, choose every **1 hour**, **4 hours**, **8 hours**, or **daily**. Existing saved schedules remain unchanged on upgrade, including the previous daily default and older 6-hour or weekly choices. Older choices are shown with an explanation until replaced. Resetting app settings restores the 4-hour check interval while leaving automatic installation off.

Settings also provides manual checks, installation, and ecosystem rollback. Scheduled checks run while the local host is open; this is not a separate OS daemon. The Settings icon shows an update indicator. All these controls use the same agent-accessible action registry.

Checks compare the actual commit in all eligible app-owned Foundation Git caches against their remote branches, including unregistered transitive modules and skill copies. Usage is a separate classification, never an eligibility gate: `usage: configured` has positive repository/ref evidence from default and saved-session bundle selections, known workspaces' existing settings, enabled app bundles, module source overrides or explicit Git inputs in module configuration (including skill sources). `usageEvidence` reports those categories without exposing workspace paths or private source URLs. Shared global, project, local and native-session settings use the same merge rules and session identity as runtime configuration, but checks never import settings, load keys or prepare bundles. Bundle aliases resolve through shared settings; retained registry entries are not selection authority or evidence of use. Different refs, including main and master, are never assumed equivalent.

Caches without that evidence have `usage: unknown`. They may be transitive dependencies, dependencies of local bundles, dynamically loaded skills, shared-session checkpoint selections, or old leftovers; none are declared unused. Their **Current**, **Update available**, **Check failed**, **Local changes**, or **Pinned** status remains independent and checks and eligible installations continue normally. The UI separates configured problems under **Needs attention** from **Other cached sources**. The latter shows counts by condition and its failures/local changes even with the full inventory collapsed; unknown updates remain in the installable list. All failures still contribute to attention. Usage reporting is not complete reachability analysis. Unknown or modified sources are retained. New component generations can adopt verified clean source copies into the shared store described below.

Shared remote/ref lookups join across application and worker checks, with bounded concurrency/timeouts. Sibling refs for the same repository use one Git request. Manual checks bypass saved availability results; automatic checks can reuse successful results within the configured interval. Credential/access identity changes invalidate reuse, and a failed fresh lookup invalidates its earlier success. A failed source stays **Check failed**, never **Current**, regardless of usage. Missing workspace directories retained for history are skipped without creating a warning; existing directories are checked even if a saved availability flag is stale. Unresolvable selected names or unreadable configuration produce a separate classification warning without hiding cache failures or blocking otherwise eligible updates. Installation checks source identity, revision and local changes again in staging, without gating on usage. Local edits and SHA/version pins are not automatically replaced. The check distinguishes real source edits from tracked Python bytecode rewritten on import and symlinks flattened by an earlier cache migration. Only verified generated changes are exempted; normalization happens in the isolated staging copy, never the active cache. Tracked source changes carry an explanatory diagnostic: builds can also modify tracked files, so their origin is not assumed to be a person. A version filename or generated-file comment alone never exempts an edit. The exact wiki-weaver version output is recognized only with its reviewed build-hook hash, matching build configuration, unchanged index and a consistent commit identity; unknown hooks and genuine edits remain protected. New migrations preserve symlinks. Missing, not-yet-prepared bundles are outside the cached-source inventory; their normal first preparation still resolves them through Foundation.

Installation stages changed source bindings and prepares the **currently offered app profiles** with the enabled app behaviors. Saved chats and historical workspace/bundle combinations do not expand this qualification set. A single dependency batch collects selected modules and bundle-root packages, resolves their union once in an isolated environment, freezes the graph, and mounts each offered profile in a fresh process. These checks deny approval requests, verify required host capabilities and absence of CLI libraries, and never execute a model turn. Module initialization hooks still run.

A proven catalog-data-only change reuses an already qualified runtime. This requires an exact tree comparison, an allowlisted data contract and unchanged app-profile inputs. Unknown files, code, dependency or profile changes take the qualification path. Failed qualification leaves the active pointer unchanged.

Qualified component generations use per-worker activation. Promotion atomically changes the generation selected by new starts. Running workers retain their source generation and environment; after they park with no active jobs, queued input, approval or uncertain operation, the existing retirement handshake releases them. The next explicit work starts a fresh worker on the current generation and resumes native history. Active voice protects its worker. The updater does not interrupt a turn, automatically replay an operation, or add another checkpoint format. Application replacements, incompatible worker protocols and legacy generations retain the coordinated restart boundary.

Custom workspace/session dependency overrides qualify a separate environment on explicit execution, coalesced by effective configuration and source content. Browsing a chat does not prepare it. Identical qualified graphs share immutable environments; provider keys, connection IDs, model selection and prompt text do not define a dependency graph. Conflicting distribution sources in an offered union fail candidate qualification instead of silently choosing one. Local overrides retain their explicit source semantics.

The logical roles are candidate, current and previous. Physical generations remain while live workers, custom profiles, recorded environments or explicit source references need them. Private worker leases protect admission and process lifetime. Cleanup retires only proven, unreferenced owned generations and environments; uncertain data and shared source objects are retained. A busy worker on one generation does not block cleanup of an unrelated unused generation. Rollback promotes the previous qualified generation through the same adoption boundary and disables automatic installation.

Availability checks reuse a private inventory index, invalidated by generation, registry and configuration changes, with periodic full reconciliation. Remote-ref requests retain coalescing and bounded cached availability; a manual refresh refreshes remote availability. Every installation repeats exact source preflight, so cached availability cannot authorize overwriting an intervening local edit. Unified owns its registry, source store and runtimes. CLI caches and registries remain separate; shared settings, credentials and native projects/history authorities are unchanged.

Core, Foundation's host library, the streaming engine and loop-live track their configured `@main` sources. Provider/tool modules also follow their configured mutable sources. Candidate preparation resolves exact revisions in an isolated runtime and records them as validation evidence; these receipts do not replace branch-tracking configuration with commit pins. Activation still requires the candidate runtime checks described above. Application releases come from the private `microsoft/amplifier-unified` GitHub repository. Checks use the signed-in GitHub CLI and resolve the release tag to an exact commit. Installation first builds a separate uv tool environment and checks the host import, packaged frontend assets and package version. This validates packaging, not every provider account or live audio connection. Once idle, the host installs that exact revision into its normal uv tool location and restarts on the same port, workspace and data directory. Restart output is retained privately in `updates/restart.log`; the prior Git install reference is recorded in `updates/previous-app.json` for manual recovery. Ecosystem rollback does not roll back application code. If there is no accessible release or GitHub sign-in, the UI reports a failed check rather than claiming the application is current.

Local data: `updates/inventory.json` (private source details), `updates/releases/<id>/` (staged sources/configuration), `updates/active.json` (current/previous pointer), and per-generation runtime environments. Previous and referenced generations remain available for rollback and active workers; unreferenced, verified owned generations and environments are reclaimed. Shared source objects are retained pending complete proof that no references remain. State shown to the UI/agent contains credential-safe source labels and commit IDs, not credential-bearing remote URLs.

Application activation also verifies that the running host and the executable on PATH belong to the same uv tool installation. A development checkout or a different launcher cannot replace another installed tool. Candidate and installed-package probes use isolated Python imports, reject packages outside that environment, and cannot accidentally validate a checkout from the launch directory or PYTHONPATH. The installed package is checked again after replacement; a failed check leaves the running host up for recovery. The restart helper uses the separate validated environment, and new work remains blocked until restart. It preserves the effective bind addresses, public origins, TLS certificate/key, session lifetime, port, workspace and data directory without writing CLI overrides into saved server settings. The replacement host acknowledges the pending restart only after an authenticated HTTP request to its live listener confirms the expected version, exact installed Git revision, data directory identity, and a different process instance. Package probes and application construction alone cannot acknowledge a restart. Smart Tool servers are reported disconnected after restart and can be reconnected explicitly. The recovery record stores the actual prior installation source when available, rather than assuming an older release tag exists. Repeated installation requests reuse a pending validated generation. Cache symlinks remain symlinks while staging; external workspace trees are not copied into the update.

## Optional app features

Before replacing an active app installation, the updater durably records
`pendingReplacement`: the source process identity and exact qualified app,
optional extras and dependency digest. This marker closes admission independently
of the displayed error phase. Cancellation, failed replacement verification and
process exits retain it; a package appearing on disk cannot satisfy installation
or permit another feature request. Updates shows **Installation needs
verification** and disables additional installs while this outcome is unknown.

Successful replacement verification atomically converts the marker to the normal
pending restart. If the process exits earlier, a healthy new host can reconcile a
complete marker only by proving the exact app source/revision, extras, dependency
graph and authenticated listener/data identity, and repeating the isolated
package/import verification in that new host's interpreter. The receipt and
installed inventory are rechecked after the probe before work resumes. This
verification does not install packages or capture desktop content. The original process cannot
acknowledge its own in-place replacement. Repair a damaged installation to the
retained qualified candidate before restarting; no installer or task input is
automatically replayed. An older `activating` application receipt with missing or
invalid restart evidence stays blocked as unqualified replacement uncertainty.
It cannot be cleared by inferred success or package presence; incomplete evidence
requires operator qualification. Ecosystem rollback does not repair app code.

**Settings → Desktop & browser** offers an explicit **Install native screen
observation** action after checking the current host. The shared action is
`updates.featureInstall` with `feature="native-desktop"` and the checked
`hostInstanceId`. This narrowly adds the absent optional feature to the same
packaged Microsoft app revision. It does not select a newer app release, upgrade
existing components, remove existing extras such as TUI, or grant OS permission
or desktop control. A development checkout or custom source is not eligible.
The existing installation-owner check also requires the actual serving uv tool
environment and its active launcher to match before any replacement.

An older running app that does not expose this action must first adopt a reviewed
ordinary app release containing it. That update preserves the older installation's
selected extras. Check the newly serving app identity, then request the optional
feature against that identity. Do not inject an installer into an old process or
create pending updater state to bypass this adoption order.

Qualification records the actual app source and installed dependencies, builds
an isolated generation, and preserves every baseline distribution with exact
per-candidate resolver requirements. It checks imports, packaged assets, exact
app provenance, the complete component graph, and dependency version constraints
including activated transitive extras. A conflict fails qualification rather
than upgrading the baseline. Candidate and serving metadata are rechecked before
the existing lifecycle/idle/queue guards admit replacement. The prior actual app
reference remains in the normal recovery record; ecosystem rollback behavior is
unchanged. Resolver receipts apply only to this feature addition: later ordinary
updates continue checking published Microsoft app releases and current configured
component sources.

The accepted action returns a stable `requestId`. Both users and agents read its
durable result in `updates.featureResults` (the latest 20 requests). Admission
persists `queued` together with the command receipt, so an exit before the task
starts becomes `interrupted` without replay. Qualification, activation and pending
restart remain distinct from success. Exact command retries reuse their receipt.
Inspect an uncertain/interrupted operation before requesting a new one. A saved
qualified generation retains the existing guarded activation path; malformed
restart intent is retired by the normal restart-repair handling.

For this same-app addition, a healthy successor must match the original app
source and revision, desired extras and exact qualified dependency digest, as
well as the usual authenticated listener/data/process identity. Until then the
restart gate remains closed. Success preserves any unrelated newer app update
already listed. Installing the library does not satisfy Screen Recording consent:
check setup again and grant observation separately for a connected voice call.

## Publishing application releases

### Changelog and high-impact notices

Updates includes an offline changelog packaged from `amplifier_web/release-notes.json`. History begins at 0.11.0; earlier releases are not reconstructed. Update checks fetch this same file from the exact published Git commit, so skipped releases and their notices appear before installing. Unavailable or invalid notes show a fallback message without blocking installation. Cached notes survive failed checks and restarts; installed package entries take precedence over older cached wording.

Add an entry for every release with `version`, a concise `title`, a nonempty `changes` list, and `notices` (empty for ordinary changes). Add a notice for configuration migrations, compatibility changes, changes to existing workflows, or required action. Each notice needs a stable `id`, `title`, `detail` explaining the impact, and `action` explaining what to check or do. Do not use HTML or invent notices from commit titles. Keep existing entries so users who skip versions can review the intervening changes.

High-impact notices appear above the update controls and contribute to the shared Settings / Updates attention badges. `attention.read` marks the current notice wording reviewed across devices. Reviewing is acknowledgment, not confirmation that an action was performed, and never gates installation. Notices remain in the changelog after review; changed wording becomes unread again. All high-impact notices in the available history start unread, including on a fresh installation.

The release validator requires an entry for the package version, rejects future or duplicate versions and malformed notices, and checks that the wheel and source archive contain the validated history. The publisher generates GitHub release text from that entry. Historical immutable releases through 0.11.2 can still be rerun without a changelog; new releases require one. The bounded format supports up to 100 releases, five notices per release and 256 KB of text; archive older history deliberately when approaching those limits.

### Release workflow

The **Publish application release** GitHub Actions workflow runs when the package version or release tooling changes on main, and can also be started manually on main. A release requires the same stable `X.Y.Z` version in `pyproject.toml` and `amplifier_web/__init__.py`, with the lockfile updated. Build the frontend and commit the resulting `amplifier_web/static` assets before merging; Git-based installs consume those checked-in files.

The workflow runs the Python and frontend tests, rebuilds the frontend and checks for asset drift, builds a wheel and source archive, checks their versions and packaged source/assets, and imports the installed wheel in a fresh environment. It then creates the immutable `vX.Y.Z` tag and a draft release, attaches both distributions and SHA-256 checksums, and publishes the release. Publication happens only after validation and asset upload, so incomplete drafts are invisible to the updater. The workflow uses the repository's GitHub Actions token with contents-write permission; no additional provider credentials or paid model calls are needed.

Manual reruns can resume a draft and replace incomplete draft assets only when the selected commit exactly matches the existing tag's commit. A dispatch from a later commit with the same package version fails with instructions to increment the version; it never silently checks out the older release. A published release and its assets remain unchanged. Existing tags are never moved; a tag/package mismatch or conflicting remote revision fails the run. Releasing an older version does not replace a newer stable release as Latest. To ship additional code, increment the version and merge again.

Full private state backup and selected reset are available under Maintenance. Backups include credential files and are mode 0600, but are not encrypted. Reset previews its selected app-owned areas, requires typing RESET, makes a backup, and moves originals into a retained recovery directory; workspace and CLI files are untouched.


The source inventory groups matching repository/ref/current-revision/latest-revision/status/update-tier rows across source caches and worker packages. Source details retain package names, subdirectories, roles and usage evidence. Different branches, revisions, statuses and update tiers stay separate. All source lists sort alphabetically by their displayed name, with repository/ref/revision tie-breaks. Installation retains its private full inventory and updates every eligible cached copy or generation binding. Successful checks use compact inline status; failures include an explanatory notice.

Unread attention counts lead from Settings to Updates. Users and agents can mark items reviewed through `attention.read`; the underlying condition stays visible until resolved, and a new version or changed error becomes unread again.

Source classification recognizes local bundle files and directories in the standard
project `.amplifier/bundles` and shared user bundle folders, using the same lookup
as conversation preparation. Local bundles remain unknown for remote dependency
usage: checks do not execute them or guess their transitive includes. Historical
workspace entries without a resolved directory are retained but skipped during
source classification, just like directories that are no longer available.

Native history explicitly marked read-only (including worker and legacy session
identifiers) is not treated as resumable session configuration. Its history is
preserved, and its workspace settings are still checked. Classification reads a
detached selection snapshot outside the request thread, so large history catalogs
do not block chat or the update page while settings are inspected.


## Update diagnostics

Each application update now has an attempt ID and a separate receipt for candidate installation, candidate probe, installed-tool discovery, runtime shutdown, tool replacement, installed-package probe/version comparison, and restart. Ecosystem copying, fetching, checkout, compatibility probes and activation use the same receipt format. Failed phases remain visible in `updates.diagnostics.lastFailure`; successful staging or restart clears the current failure while retaining recent history. The user and agent read the same state.

The main update card identifies the current component batch and shows successful
configuration checks out of the total admitted for that attempt. Preparation and
compatibility have separate counters; failed checks do not count as completed.
Progress from another attempt is not displayed as current. A ready batch shows
the idle wait rather than continuing to say it is preparing.

Recently installed batches remain visible while a subsequent batch is prepared,
including when a new source revision was published during the previous batch.
This bounded history is recorded only after activation succeeds, not when
staging or validation finishes, and survives event-list eviction and reload.
Partial Smart Tool activation names only the tools that actually activated.
Rollback is a distinct operation, not installation of a failed candidate.
Cleanup cancellations retain the primary failure receipt; an independent
interruption still has its own receipt. These display changes do not alter
eligibility, installer concurrency, idle admission or automatic-update policy.

Receipts contain only a fixed set of operational facts: phase/status, correlation IDs, elapsed time, exit status, output byte counts, fixed error classifications and allowlisted probe results. Subprocess text, command arguments, workspace paths, source URLs, exception messages and environment variables are never included. Python probes return framed JSON, so unrelated startup messages cannot be mistaken for the package version. Missing imports, missing assets, package location checks, version mismatches, command failures and timeouts remain distinguishable.

When the app diagnostic collector is available it owns durable storage and configured routing of these events. Otherwise the updater writes a private `updates/diagnostics.jsonl` fallback (0600), rotated at approximately 1MB with one previous file. App state retains the latest 50 events. There is no automatic replay after an interrupted install, paid call, or uncertain result. Historical failures without command receipts cannot be reconstructed from successful later probes; a successful retry does not establish the original cause.

## Managed restart handoff and recovery

For a systemd user service, the host queues `systemctl --user --no-block restart amplifier-unified.service`. Systemd owns the replacement process; Unified does not launch a second unmanaged host. A successful command response means the job was accepted, not that the new server is ready. If systemd terminates the requesting child before it receives that response, the request remains unconfirmed. The attempt ID, expected version/revision, and pending restart survive signals, cancellation, timeouts, and rejected requests. New work stays paused until a successor is verified. Genuine request rejection shows service-restart guidance and retains its diagnostic receipt.

The successor makes a bounded local HTTP health check after startup. HTTPS checks pin the configured leaf certificate; they do not disable certificate verification or forward credentials through configured proxies. Only authenticated control requests receive the captured running Git revision and process identity. A wrong version, wrong revision, different server, missing provenance, or an unready listener never counts as success. Timeout/cancellation keeps the attempt available for diagnosis and later confirmation.

An older incomplete or malformed pending restart receipt is retired at startup instead of keeping updates and conversations paused forever. The active restart marker and stale pending application are cleared, a sanitized failed `restart-repair` diagnostic is recorded, and the interrupted restart remains explicitly unacknowledged: Unified does not infer success from a version-only receipt or probe it as though it were a complete handoff. Any unrelated pending ecosystem release is retained. A prior diagnostic failure remains the current failure record for review.

Older releases could report a failed `systemctl` command after an otherwise successful restart and erase the pending marker. The successor can reconcile that exact legacy state using its matching validated release receipt, successful replacement probe, and current HTTP health. It records `restart-reconcile`, retains the original failed command in history, and stops recommending another restart. This is current-health verification, not an invented historical `restart-ack`. Unrelated failures or mismatched installation identities are not cleared. This recovery also covers the first upgrade from an old process still running the faulty updater in memory.

Run `python scripts/test_systemd_update.py` to exercise the Linux handoff in a disposable Docker container. It requires privileged Linux-container support, publishes no host ports, and uses an isolated user service. Package staging is a local fixture; cgroup membership, systemctl requests, signals, replacement processes, HTTP readiness, rejection, and the historical outgoing updater are real. The managed-update CI workflow runs the same integration check.
# Selecting a maintenance release

The manual release workflow accepts an optional `release_revision`: the full
40-character SHA of a commit already merged into main. This allows a qualified
maintenance version to finish publication after main advances to the next
version. Branch names, abbreviated SHAs, unknown commits and unmerged commits
are refused. The selected commit supplies the version and is checked out before
all normal runtime, browser and package gates. Existing immutable tags cannot
be moved or reused for different source. Leaving the input blank keeps the
normal main-commit release behavior.


## Shared sources and incremental update preparation

Availability checks share a durable success cache and join identical in-flight
requests. Automatic checks may reuse results within the configured check interval;
manual checks fetch fresh upstream results. Cache identity includes the repository,
ref or package and an opaque access-context fingerprint. Failed fresh lookups
invalidate earlier successes. Git refs for the same repository are batched, registry
requests share a connection pool, and local checkout inspections use bounded
concurrency. New configuration still produces a new inventory; a cached availability
result never qualifies an installation.

With a compatible Foundation component, Unified supplies an application-owned
`AMPLIFIER_SOURCE_STORE`. Git bundles, module sources and Skills share one
read-only checkout per repository and exact commit. Each generation has its own
repository/ref binding; advancing `main` creates a new object and changes only
the unpublished generation's binding. Old readers and rollback keep their exact
objects. Different revisions and dependency environments still need separate
identities; this is not a mutable global latest checkout.

Legacy source adoption happens only in the unpublished staging copy. Every copy
of the same repository/ref is checked before adoption. Tracked changes, ignored
or untracked local files, external Git directories, conflicting revisions and
uncertain metadata prevent adoption for that identity. The serving and rollback
copies remain intact. Foundation's file, HTTP, archive and non-opted-in Git
sources retain their existing behavior. Skills without the compatible Foundation
API retains its legacy resolver until both components are updated. Legacy skill
copies remain authoritative until staging adoption, and Skills keeps its existing
default `main` ref. Foundation's status API reads shared bindings without cloning.

Python build backends receive a separate writable source view. Builds of that
view are serialized; the canonical object never becomes an editable install.
uv remains responsible for wheel reuse and dependency resolution. Staging uses
filesystem copy-on-write when supported, with an ordinary-copy fallback and no
writable hardlinks.

Worker reuse requires the same complete installed graph, source content,
manifest, Python version, OS/architecture and unchanged lockfile. Each update
keeps separate source policy and qualification receipts. If an existing project
has changed, it is preserved and a new project is built; it is never repaired or
synced beneath readers. Recorded worker starts use `--no-sync`. Fresh generation
preparation and no-model compatibility probes remain required. The candidate
lock is synchronized once before preparation. Duplicate module installs within
that attempt can reuse evidence only while their complete installed metadata,
source content and explicit policy match; graph changes invalidate that reuse.
A new attempt always starts with fresh dependency resolution. Existing explicit
local/registry overrides are not replaced by a remembered Git lock.

After the uv-managed launcher supports generation activation, an already tested
application environment becomes the live launch target through an atomic private
pointer. This avoids installing its exact packages a second time. Older
launchers make one normal uv replacement first. A manual uv reinstall/upgrade
changes the bootstrap identity and takes precedence; source checkouts and
unrelated development venvs are never redirected. Package graph and restarted
HTTP readiness are still separately verified before work resumes.
The qualified app starts with an isolated Python import path so a checkout in the
launch directory cannot replace its tested package.

After successful component activation, idle background retirement considers only proven,
application-owned ecosystem generations. Current, previous rollback, pending,
process-referenced and configuration-referenced generations stay. Modified
sources, unverifiable receipts, inaccessible process inventories and unknown
references are retained. Shared worker environments are kept while another
retained generation references them. The shared source store, global uv cache,
CLI installation, history and unverified legacy folders are not swept.

The last check reports its elapsed time and reused lookups. These are local
operation measurements, not a promised network or installation duration.
Storage reuse does not bypass package qualification, configured bundle/module
compatibility, source integrity or restarted-host readiness.

### Compatibility and validation boundary

The implementation was checked against the component catalog in
`microsoft/amplifier` at `33b75749920472257d1d85d20a5699ecf9d1ece7`, Foundation at
`89575c3482e3e8afe5a03df72e723cf815fa1f6c`, Skills at
`402ee0d1e75f2d13e8b090d2b10c716cd60f526d` and Unified at
`2d6962d712348ff1b92d9e24861bac3a11c131b7` before these changes.

| Implementation | Boundary |
| --- | --- |
| Foundation Git resolve/status/update | Shared storage is opt-in; exact source roots and `ResolvedSource` are retained. |
| Foundation file/HTTP/archive/custom handlers | Handler selection and existing cache behavior are unchanged. |
| Foundation Python module activation | Shared objects build into wheels from locked writable views; ordinary local/editable sources remain local/editable. |
| Skills remote sources | Compatible shared API is optional; absent API and unadopted legacy copies retain their existing behavior. |
| Unified host, worker, bundle and registered-source checks | One availability scope; separate installation and generation receipts remain authoritative. |
| Smart Tool and global uv installations | Their package environments stay separate; source sharing does not combine executables, ABI contracts or credentials. |
| Other CLI/agent/community consumers | No automatic opt-in or external cache migration; consumers can choose an explicit shared root. |

Local validation includes real offline Git repositories, uv wheel installation,
worker freeze/reuse, app interpreter delegation, protected storage and desktop/mobile
source-list rendering. A synthetic check fixture with 50 repositories, three refs
and two consumer roles required 50 Git transport requests for 300 logical lookups,
zero for the warm automatic pass, and 50 again for a manual fresh pass. This measures
request elimination, not real network latency. Installed-service, account-backed
and physical-device acceptance remain separate from these tests.

### Shared user resources and independent host downloads

Unified reads shared Amplifier settings, credentials, native conversation
history, routing, instructions, and user/workspace-authored bundles and skills.
It constructs bundle registrations from those settings rather than importing
CLI registrations. It never copies the CLI cache or registry on first startup.
A previously imported registry in Unified's own directory is ignored for
session source selection; its saved contents are retained. CLI behavior and
its default cache locations are unchanged.

Root and nested-agent Skills downloads use the current Unified generation's
`cache/skills` directory, including remote sources added during a session.
The explicitly enabled shared-model runtime adapter also prepares modules in
the app-owned cache. Local skills stay shared, and explicit user cache paths
remain authoritative. Skills' generic `cache_dir` option avoids changing
`AMPLIFIER_HOME`, which remains the shared settings/history root. Older Skills
modules must receive the component update before they honor that option;
source tests do not establish adoption by already running workers.
