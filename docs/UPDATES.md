# Updates

## Update policy contract — accepted direction

**Status:** user-directed product requirements, accepted November 9, 2026.
This section owns the intended update behavior for Amplifier Unified, not a
system-wide agent preference. It is not a claim that every requirement is
implemented or qualified. The implementation descriptions below retain their
existing evidence boundaries; in particular, shared dependency batches and host
restart coordination do not yet establish all of the failure-isolation and
minimal-interruption promises here.

For example, if Unified is already current, two providers have newer commits on
`main`, and one provider fails installation, Update must still adopt the other
provider where compatible. It retains the failed provider's working version and
reports the partial result. A session already using the older provider may
finish its turn before adopting the new one.

### Independent application and component updates

- Update brings the application to the latest published Unified release when
  needed, and independently checks and advances managed components even when
  the application version is already current. Routine component advances must
  not require a change or release in the Unified repository.
- Git components default to `@main` unless installation or configuration selects
  another source or ref. A selected moving branch follows that branch; an
  explicit SHA or tag stays selected. Never silently replace a custom source,
  branch or pin with upstream `main`. Registry-distributed components retain
  their declared package channel and platform constraints.
- Locks and exact candidate revisions record what was installed for verification
  and rollback. They do not turn a configured moving ref into a permanent pin
  for later updates.

When configuration explicitly replaces a module's full commit pin with another
commit of the same repository and package, preparation replaces the old generated
retention entry in its new candidate. The actual selected manifest must declare
that module and agree with its configured commit, package and source identity.
Authored dependency constraints and resolver overrides still apply. Preparation
rechecks configuration, source and generation identity before qualification;
it never rewrites the serving generation or its rollback receipts.

### Explicit app-only requests and idle configuration holds

`updates.app` requests only application preparation and activation, including the
app package's qualified dependencies. It supersedes an older whole-install
continuation without changing automatic-update preferences, deleting prepared
components, or resolving uncertain effects. Its retained intent follows newer
app candidate selection, replacement and restart readiness. Verified app-only
readiness ends that request; it does not install components from the superseded
sequence. `updates.install` still requests the whole app → included → other
sequence. A later ordinary policy check can start a new update cycle.

An application check can discover a newer published app while a Smart Tool
with a known staged target is waiting to activate. An explicit app request
refreshes a missing selection. Install can prepare the newer app and still
waits for an idle host before replacement. The tool keeps its staged target,
its activation gate, and any existing failure receipts. An interrupted tool
stage with no known target keeps its existing handoff protection.

Normal app replacement does not require recovery of an idle configuration error
or adoption of saved configuration by an absent worker. `configurationPending`
and retained operation phases describe intent/evidence, not executing work.
For example, a lost native receipt can leave settings unresolved without keeping
the host on an old application release. The target must have no mounted worker,
preparation, running turn or child work, executing refresh, unsettled management task or
runtime operation. Cancellation requested is not settled work. The app owner
rechecks held-owner, execution/native target and Stop bindings across validation
waits and holds the absent native writer boundary through activation. A changed
candidate or intent must be qualified again before replacement.

This is update quiescence, not configuration success or renewed configuration
authority: the original error, request, payload, settings, authority, attempts,
native seal and input fence remain unchanged. It never resumes, reconciles,
mounts, cancels or rewrites that operation, and does not enqueue target model
input. Missing, contradictory, partial or unknown historical evidence stays
unresolved; replacement does not upgrade it to a known outcome. Status reads do
not rescan native receipts or saved configuration to decide app quiescence.
Component activation retains its existing stronger configuration gate; explicit
user-only force-restart semantics are unchanged. Real active work can still
require waiting or deliberate interruption; an idle recovery hold alone cannot.

At ordinary error settlement and startup, the configuration owner can observe
an authenticated terminal no-effect receipt for the exact native attempt without
renewing its permission. Startup holds exclusive absent-writer ownership, retains
the original request, attempts and history, and persists an old-input cutoff before
releasing that operation's local fence. It neither warms the old chat nor drains
its queued input. Missing, conflicting, partial and saved-but-unloaded outcomes
remain local fences; no consumer recovery action is required for an unrelated
application replacement. This bookkeeping is not a second recovery service.

### Partial failure does not abort independent progress

- A failed component update retains its last working version. Continue unrelated
  component updates, compatible application updates and their necessary restarts.
- Hold back only the components that actually require the failed component or
  must change together for compatibility. Do not activate an incompatible mixture
  or make one dependency conflict a global failure.
- Report updated, failed, dependency-held and awaiting-adoption components
  separately, including useful failure guidance. A partial result is not
  "everything is up to date"; successfully completed work is not rolled back
  merely because an independent component failed.

### Interrupt only the affected lifetime

- Fetching, resolving and preparing updates must not require all sessions or the
  main service to be idle. Wait only where replacing an affected process or
  resource actually requires it.
- Component-only updates do not restart the main service unless its loaded code
  changes. Unrelated UX or service changes must not wait for unrelated sessions
  solely because they share an installation.
- Running sessions may retain their older component generation through the
  current turn. At a safe end-of-turn boundary, retire and restart the affected
  session worker to adopt the update while preserving its native history. New
  work can use the updated generation without waiting for every older worker.
- If the main service really needs replacement, coordinate only its affected
  lifetime and preserve unaffected work. Process-lifetime coupling that currently
  prevents this is an implementation gap, not permission to terminate active
  tools, ignore Stop, discard uncertain outcomes or replay input.
- Show preparation, pending adoption and verified running adoption distinctly.
  Downloaded or installed files alone do not establish that a session is using
  the new components.

## Partial source failure — bounded source-acquisition slice

This implements only the source-acquisition boundary of the accepted direction.
The focused synthetic qualification uses distinct local Git repositories, a
real qualified baseline, the ordinary manager/Collector/installer/freeze/profile
path, atomic promotion, a second branch advance, refusal and rollback. It uses
prepared/repacked dependencies and a no-model fixture provider; it is not public
registry provenance, native Windows or busy-worker qualification. Existing
worker adoption remains a separate qualified boundary. This does not complete
the broader failure-isolation and process-lifetime requirements.

This slice handles settled pre-install source acquisition failures: an eligible
source's availability lookup has a known, settled failure, or a legacy app-owned
Git cache fetch has exited with a known nonzero status. Retention requires a
verified working generation, matching source identity and exact revision, clean
owned copies and qualified package evidence. It does not treat a timeout,
cancellation, unsettled command or unknown write as a settled fetch failure.

For example, if fetching A's new source fails, keep verified A-old. If C-new
requires A-new, retain verified C-old as **dependency-held**. Independent B-new
can advance only if the resulting A-old/C-old/B-new union passes the ordinary
offered-profile dependency resolution, installed-source verification and runtime
qualification. There is one qualified union and one atomic generation promotion,
not independently promoted package fragments. All copies and packages of the
same repository/ref remain coupled; a successful sibling copy cannot silently
advance that identity around a failed copy.

The hold check includes every static optional dependency declared by a selected
changed root, because another selected root can enable that extra and an exact
source override must not erase its version constraint. This is deliberately
conservative: it can hold a source for an optional constraint without proving
that the extra is active. Dynamic required or optional dependency metadata
cannot establish independence and refuses before installation.

The retained revision is an exact candidate constraint. The configured moving
branch remains separate policy and is still followed by later update checks.
Retention does not replace a user's source, branch or pin, or turn a moving ref
into a permanent pin. Local edits, conflicting identities and missing verified
baselines remain protected.

Shared-store sources may retain their verified old binding after an availability
failure. A shared-store fetch failure does **not** establish the same settled
legacy-fetch boundary; an unknown store mutation fails closed. This slice does
not partition arbitrary resolver or installer conflicts, infer independence from
dynamic dependency metadata, or detach component work from a pending application
replacement or restart. Existing application, Smart Tool, activation and
unknown-operation guards still apply.

The public `componentResults` receipt reports each source ID, exact revision and
one of **failed**, **dependency-held**, **awaiting-adoption** or **updated**.
The UI resolves names from the public source inventory, including grouped members;
it does not expose private source URLs. Retained rows remain `check_failed`, with
`sourceUpdate` and sanitized detail distinguishing failure from dependency hold.
Promoted rows become `current` with `sourceUpdate: updated`. **Awaiting adoption**
is not promotion; **updated** confirms promotion for new work, not adoption by
every running worker. The existing worker-adoption status remains separate.

`includedCanProceed` permits an independent eligible installation despite included
source issues. It does not clear a pending replacement, restart or uncertain
operation, and does not make the failed tier “Up to date.” Neither a manual nor
an automatic Check retries unchanged retained failures. A deliberate Install
can request a new guarded attempt; a genuinely changed desired revision can
also make new work eligible.

Automatic availability freshness is not deliberate retry intent. Unchanged
retained outcomes survive later unrelated tier promotions through the existing
generation receipt and reverified source identities. A fresh unknown failure
cannot borrow an older settled outcome. Cancellation during later preparation
retains the inspection fence even if the visible diagnostic still names the
earlier fetch failure. Changes to source identity, desired revision or relevant
configuration can make new work eligible; a retention-only status projection
does not.

## Application availability — bounded independence slice

Focused qualification exercises the actual manager, release-metadata parser,
local Git discovery, source copying, staging and activation-pointer behavior.
Worker validation is an explicit test seam, not installed-graph or native-worker
qualification. UI checks cover progress, pending-adoption controls and truthful
application guidance; synthetic owner state does not establish backend execution.
Publication and live adoption remain separate evidence.

A completed application availability check returning `current`, `check_failed`
or `release_channel_needed` permits the existing included-component check path.
Eligible components can prepare against the running application's declarations
without a successful release lookup. A known available application update still
uses the app-first path; other unknown application states do not grant permission
to proceed. Existing component source, selection, qualification and adoption
checks remain required.

The application keeps its own status and sanitized failure guidance. Component
progress and pending adoption remain visible, with duplicate installation controls
held while work is pending. A component operation failure takes priority over an
unavailable application check; completed component work does not make that check
successful or establish that the application is current. Historical failure
receipts remain unchanged. While components progress or are available, the app
check warning stays in the overall status even when Settings navigation collapses
the technical details. Once eligible component work is exhausted, a failed
application check remains actionable through **Check for updates**.

This slice does not detach work from a pending application replacement or restart,
partition arbitrary dependency or installer conflicts, or change process-lifetime
coupling. It does not weaken Stop, cancellation, uncertain-operation inspection
or unchanged-input retry guards. Application availability is not authorization to
repeat an unknown installation.

## Current implementation and operational behavior

Available updates are listed immediately at the top of Settings. The application has its own card showing the installed version even before the first check, plus the latest published release after a successful check. The complete source inventory is behind **Show all sources**; failed checks are called out separately. Successfully installed application releases are removed from the pending count on restart. An installation newer than the published release is identified explicitly; application updates follow stable GitHub Releases, not every commit on main.

Automatic update checks default to every **4 hours**. Under **Update preferences**, choose every **1 hour**, **4 hours**, **8 hours**, or **daily**. Existing saved schedules remain unchanged on upgrade, including the previous daily default and older 6-hour or weekly choices. Older choices are shown with an explanation until replaced. Resetting app settings restores the 4-hour check interval while leaving automatic installation off.

A host start adds one check. When the host starts and its last check, or its last attempt to check, is **1 hour** old or more, the update owner checks soon after the start and does not wait for the rest of the interval. This check asks again; it does not reuse saved availability results. That holds for every such start, also when the timed interval has passed as well. Everything else is as for a timed check: it follows **Keep Amplifier up to date**, it interrupts no running work, and it retries no failed update. The first check that is admitted after the start ends this reason: the owner's own check, a person's check, and also a check that fails. The owner then follows the timed interval again, counted as before from the newest saved check or attempt. A check saves the time of its attempt before it looks. So a start check that fails, for example because the network is not up yet, is not repeated; the next automatic check is one interval later, as after any failed check. When a part of the old interval remained at the start, that next automatic check is later than it was before this rule, and no completed check lies between: without the start check, the timed check ran when the rest of the old interval had passed. A person's **Check for updates** does not wait for the interval. Like every check, it is refused in some states, for example while a prepared component update waits for its activation or while a restart is pending. A clock that is set back after the start brings no second start check. A start with a younger last check or attempt starts no check. With automatic checks off, the owner starts neither a timed check nor a check at a host start; an update sequence that has already begun still continues with its next check. The two numbers are `DEFAULT_CHECK_INTERVAL_HOURS` and `HOST_START_CHECK_AFTER_HOURS` in `amplifier_web/updates.py`. `UpdateManager.check_due` is the one rule for a check that time causes: the timed check and the check at a host start. A check that continues an update sequence, and the new check of a selection that changed, are not caused by time and do not use it. When a host starts while an update sequence waits for its next component check, and the sequence is not stopped at an error or an interruption, the owner asks for that check before a check that time causes, unless a state that the owner's pass handles earlier is present (for example a staged application or an unresolved preparation). When that check is admitted, it ends the start reason, although it looks only at components. The owner's next check that time causes, with its look for an application release, then follows the timed interval.

Settings also provides manual checks, installation, and ecosystem rollback. Scheduled checks run while the local host is open; this is not a separate OS daemon. The Settings icon shows an update indicator. These controls use the shared action registry; forced interruption requires an explicit user confirmation.

Application updates download the selected release's prebuilt wheel and checksum
manifest, verify the bytes and embedded source revision, and install that local
wheel in the existing private candidate environment. They do not build the UI
or require Node. Missing or inconsistent artifacts fail preparation without
replacing the running app; there is no silent fallback to building Git source.
The candidate retains its wheel URL, checksum and local input alongside the
existing qualification receipt. Installed metadata and the observed environment
bind that proof to the running app; a source stamp alone does not establish
artifact provenance or optional-feature eligibility.

A prepared app update that is waiting for active work continues checking the
published release channel at the configured automatic-check interval. **Check
for newer release** refreshes availability without installing or replacing the
qualified candidate. The existing installation request then prepares a strictly
newer published version in isolation; only successful validation replaces the
waiting candidate. Before an ordinary idle restart, the updater refreshes again
unless it has checked within the last minute. This final check belongs to the
already requested installation, even if periodic checks are disabled.

An unavailable release channel leaves the prepared update usable and shows a
nonfatal warning. A newer candidate that fails validation retains the earlier
receipt but stops automatic adoption until an explicit retry. Older metadata,
a moved tag at the same version, or a changed commit without a newer version
cannot replace a qualified candidate or discard a newer known target. The final
activation lock also rejects an obsolete candidate if a concurrent check found
a newer release while activation waited. These checks cannot guarantee knowledge
of a release published after the last lookup. An already started replacement or
restart, an explicit optional-feature selection, and a confirmed force restart
retain their exact target; none is silently retargeted.

Superseded app staging directories and their qualification receipts remain on
disk. The existing automatic retention owner covers ecosystem generations, not
app-installation generations; this path does not infer that an older app directory
is safe to delete.

An explicit `updates.reconcileManualHandoff` verifies an already completed manual
Windows handoff after the exact retained pre-effect `restart-capability` refusal.
It is not automatic Windows activation or a historical restart acknowledgement.
The host independently verifies the current official release wheel, installed
payload, uv launcher, owner/data identity, dependencies/extras and pinned local
listener, then rechecks the pending tuple before retiring only that stale intent.
Missing proof, changed identity or any unfinished replacement remains a refusal.
The old candidate and failed attempt remain inspectable. Exact retries read
`manualHandoffResults`; interrupted verification is not replayed. This does not
grant optional-feature eligibility or promote an inactive component generation.

Before that explicit verification, a native Windows host already running at or
above its exact settled manual-refusal target retains that target across newer
release discovery (the same version must have the same revision). Newer release
availability stays visible, but does not supersede the target awaiting proof.
This is only a scheduling hold: it neither verifies adoption nor admits component
work. It does not recover an older target that was already superseded before this
behavior was installed, and does not retire a newer pending candidate.

When a qualified application update is waiting, expand **View active work** to
find **Force restart…**. Its confirmation explains that running chats, workers,
tools and calls will be interrupted, saved history and drafts are retained, and
interrupted work is not replayed automatically. An unresolved checkout handoff
is labeled separately: restarting does not reconcile it or clear its chat fence.
Ordinary automatic updates wait for idle work. An owner can explicitly yield its
active objective at a supported native turn boundary as described below.

`updates.activation` describes the prepared app: `waiting` while work blocks it,
or `starting` when it can continue by itself. When no work runs, the update starts
by itself and the page offers no interruption. The saved phase stays `app-staged`
until the replacement marker is written. `updates.forceRestartTarget` stays in
the saved state in both cases, because `task.waitForUpdate` names it. Only the
bundled page's view omits it: when no work delays the update, and on Windows.

`updates.forceRestart` requires `confirm: true` and the exact
`hostInstanceId`, staged `revision`, `generation`, and `attemptId` from
`updates.forceRestartTarget`. Only user/UI origins may confirm; an agent cannot
supply its own confirmation. A changed host or candidate rejects the request
before effects. The action returns a request ID; a queued rejection is reported
in `updates.forceRestartResult` for that request, without an automatic retry.
Force is available only for an immutable qualified application generation, not
an in-place legacy installation or an ecosystem-only update. On Windows a forced
restart is not available. `update_restart.require` refuses it: at dispatch,
before the request is recorded, and again before activation reaches its own
Windows check. The update state stays as it is.

Candidate and serving-component validation still run. At final admission the
normal durable replacement fence blocks new work, then the owned runtime is
closed and checked for remaining workers, preparations or pending operations
before promotion. Failed interruption retains the fence and does not promote.
Promotion, installed-package verification, managed host restart and new-host
health confirmation use the normal update path. Remaining host services use
the normal graceful shutdown; no conversation input is replayed. A failed or
uncertain replacement remains fenced for inspection rather than reopening work.

Checks compare the actual commit in all eligible app-owned Foundation Git caches against their remote branches, including unregistered transitive modules and skill copies. Usage is a separate classification, never an eligibility gate: `usage: configured` has positive repository/ref evidence from default and saved-session bundle selections, known workspaces' existing settings, enabled app bundles, module source overrides or explicit Git inputs in module configuration (including skill sources). `usageEvidence` reports those categories without exposing workspace paths or private source URLs. Shared global, project, local and native-session settings use the same merge rules and session identity as runtime configuration, but checks never import settings, load keys or prepare bundles. Bundle aliases resolve through shared settings; retained registry entries are not selection authority or evidence of use. Different refs, including main and master, are never assumed equivalent.

Caches without that evidence have `usage: unknown`. They may be transitive dependencies, dependencies of local bundles, dynamically loaded skills, shared-session checkpoint selections, or old leftovers; none are declared unused. Their **Current**, **Update available**, **Check failed**, **Local changes**, or **Pinned** status remains independent and checks and eligible installations continue normally. The UI separates configured problems under **Needs attention** from **Other cached sources**. The latter shows counts by condition and its failures/local changes even with the full inventory collapsed; unknown updates remain in the installable list. All failures still contribute to attention. Usage reporting is not complete reachability analysis. Unknown or modified sources are retained. New component generations can adopt verified clean source copies into the shared store described below.

Shared remote/ref lookups join across application and worker checks, with bounded concurrency/timeouts. Sibling refs for the same repository use one Git request. Manual checks bypass saved availability results; automatic checks can reuse successful results within the configured interval. Credential/access identity changes invalidate reuse, and a failed fresh lookup invalidates its earlier success. A failed source stays **Check failed**, never **Current**, regardless of usage. Missing workspace directories retained for history are skipped without creating a warning; existing directories are checked even if a saved availability flag is stale. Unresolvable selected names or unreadable configuration produce a separate classification warning without hiding cache failures or blocking otherwise eligible updates. Installation checks source identity, revision and local changes again in staging, without gating on usage. Local edits and SHA/version pins are not automatically replaced. The check distinguishes real source edits from tracked Python bytecode rewritten on import and symlinks flattened by an earlier cache migration. Only verified generated changes are exempted; normalization happens in the isolated staging copy, never the active cache. Tracked source changes carry an explanatory diagnostic: builds can also modify tracked files, so their origin is not assumed to be a person. A version filename or generated-file comment alone never exempts an edit. The exact wiki-weaver version output is recognized only with its reviewed build-hook hash, matching build configuration, unchanged index and a consistent commit identity; unknown hooks and genuine edits remain protected. New migrations preserve symlinks. Missing, not-yet-prepared bundles are outside the cached-source inventory; their normal first preparation still resolves them through Foundation.

Installation stages changed source bindings and prepares the **currently offered app profiles** with the enabled app behaviors. Saved chats and historical workspace/bundle combinations do not expand this qualification set. A single dependency batch collects selected modules and bundle-root packages, resolves their union once in an isolated environment, freezes the graph, and mounts each offered profile in a fresh process. These checks deny approval requests, verify required host capabilities and absence of CLI libraries, and never execute a model turn. Module initialization hooks still run.

A component update qualifies the selected conversation profile, and a profile
with no provider cannot qualify. While the app's own profile is selected and no
AI connection is set up, the update owner starts no component candidate and
records no attempt. Settings says that components update after an AI connection
is set up, and that nothing failed. When the application release check failed,
the text says that instead of "nothing failed", and that the component update
waits for another reason. The published status is `waiting` with
`action: connect`; it requires no attention. This holds for the automatic update of a new
installation and for a person's own Update request, on every platform. The
update starts after the first connection: a connection changes the checked
configuration, so the owner checks once more and installs that checked
selection. An installation that never gets a connection never updates its
components; it also cannot hold a conversation. An application update does not
wait, and neither does a check that finds only Smart Tool updates. The other
tier is still checked after the included tier, so it waits behind an included
component update that waits.

The rule (`profile_catalog.connection`) reads the same global settings as the
stage, and it merges and drops provider rows by the same steps as composition
(`host.config.merge`, `host.config.provider_dropped`). It makes a claim only for the app's own
profiles from their own source with the app's own behaviors, which declare no
provider. A profile from another source, a bundle source override or another
app behavior can declare a provider itself: there the stage runs and the probe
decides. Its refusal of an incomplete selected profile carries a reason,
`ai-connection-needed` when only a provider is missing and the loop is
supported, and `incomplete-conversation-profile` otherwise.

Update health does not reclassify an earlier failure record. Releases before
this rule started the component update of a new installation by themselves and
recorded its refusal as a failed update. That record named no reason, and the
refusal covered every incomplete selected profile, so the record cannot prove
that only a connection was missing. It keeps its priority as component attention
until a later component update completes; a separate application success does
not resolve that attempt. The
existing rule for an older preparation failure then makes it history. While no
AI connection is set up, the record's text also names the missing connection,
because a retry cannot start without one; a record of `ai-connection-needed`
says that already. On such
an installation the person sets up an AI connection; the update then starts
when a component update is available
(by itself, or at the person's Update request when automatic installation is
off). A new installation never gets such a record, because no attempt starts.

The owner's pass and a person's Update request share one step for a selection
that changed after its check (`UpdateManager.install_checked`): the refusal
before anything is prepared leads to one new check, and that checked selection
is installed under the caller's own admission. A second refusal, and a check
that fails, are reported as before; nothing is repeated.

A new installation has a prepared worker environment and no component
generation until its first component update. Until then a conversation fetches
each declared source and installs its modules at its first use. A check there
does not offer that first use as an update. A declared source that nothing has
fetched yet is not listed "to install": the count would be withdrawn by the
next check. A module that a chat installed follows the branch of the binding
that the chat wrote below the data folder's `foundation/cache/.source-bindings`.
It is not a fixed version, and it starts no "Refresh included components"
update. A fetched source and an installed module are still checked like every
other component: when their branch has a newer revision, the check lists a
real update. The rule for an absent source holds only with a worker environment
and no selected generation; the rule for a module only with no selected
generation. A generation is immutable, so there an absent declared source stays
an update, and a module's branch comes only from that generation's own
bindings. With no worker environment the first component update still lists
the environment and every declared source. The bindings must name exactly one
ref for a module's source and commit; with none, or with two different refs,
the commit stays a fixed version as before.

The first real component update of such an installation makes its first
generation. Qualification asks the branch of each chat-installed module again
(`runtime_source_policy.qualify_cache_policies`). A module at the head of its
branch is recorded with that branch. The sequence installs included components
first and checks the other components after that. So a chat-installed module of
the other components that is behind its branch is still behind when an update
of included components makes the first generation, and the existing rule
records it as a moved reference. The check of the other components follows as
the continuation of the same sequence. It lists the module's source as an
update, and that second update moves the module to its branch and records the
branch again. Such an installation therefore runs two component updates, one
after the other, where one was enough before this rule. Settings says "Updates
available" between them, not "You're up to date". A full check between the two
lists the moved reference as "Refresh included components"; that update settles
it in the same way. Before this rule a new installation ran one component
update after its first chat, also when no module was behind and nothing
changed. With this rule it runs none there.

A new installation with no AI connection waits for a connection only when a
check found a component update. When the check found none, Settings says that
the installation is up to date.

A proven catalog-data-only change reuses an already qualified runtime. This requires an exact tree comparison, an allowlisted data contract and unchanged app-profile inputs. Unknown files, code, dependency or profile changes take the qualification path. Failed qualification leaves the active pointer unchanged.

Qualified component generations use per-worker activation. Promotion atomically changes the generation selected by new starts. Running workers retain their source generation, environment and native writer, including while idle. The existing retirement handshake saves, rechecks pending work/resources and exits before releasing ownership. The next explicit work starts a fresh worker on the current generation and resumes native history. Active voice protects its worker. The updater does not interrupt a turn, automatically replay an operation, or add another checkpoint format. Application replacements, incompatible worker protocols and legacy generations retain the coordinated restart boundary.

Custom workspace/session dependency overrides qualify a separate environment on explicit execution, coalesced by effective configuration and source content. Browsing a chat does not prepare it. Identical qualified graphs share immutable environments; provider keys, connection IDs, model selection and prompt text do not define a dependency graph. Conflicting distribution sources in an offered union fail candidate qualification instead of silently choosing one. Local overrides retain their explicit source semantics.

The logical roles are candidate, current and previous. Physical generations remain while live workers, custom profiles, recorded environments or explicit source references need them. Private worker leases protect admission and process lifetime. Cleanup retires only proven, unreferenced owned generations and environments; uncertain data and shared source objects are retained. A busy worker on one generation does not block cleanup of an unrelated unused generation. Rollback promotes the previous qualified generation through the same adoption boundary and disables automatic installation.

Availability checks reuse a private inventory index, invalidated by generation, registry and configuration changes, with periodic full reconciliation. Remote-ref requests retain coalescing and bounded cached availability; a manual refresh refreshes remote availability. Every installation repeats exact source preflight, so cached availability cannot authorize overwriting an intervening local edit. Unified owns its registry, source store and runtimes. CLI caches and registries remain separate; shared settings, credentials and native projects/history authorities are unchanged.

Core follows published PyPI releases using the app's wheel-only policy. Foundation's host library, the streaming engine and loop-live track their configured `@main` sources. Provider/tool modules also follow their configured mutable sources. Candidate preparation resolves exact revisions in an isolated runtime and records them as validation evidence; these receipts do not replace branch-tracking configuration with commit pins. Activation still requires the candidate runtime checks described above. Application releases come from the private `microsoft/amplifier-unified` GitHub repository. Checks use the signed-in GitHub CLI and resolve the release tag to an exact commit. Installation first builds a separate uv tool environment and checks the host import, packaged frontend assets and package version. This validates packaging, not every provider account or live audio connection. Once idle, the host installs that exact revision into its normal uv tool location and restarts on the same port, workspace and data directory. Restart output is retained privately in `updates/restart.log`; the prior Git install reference is recorded in `updates/previous-app.json` for manual recovery. Ecosystem rollback does not roll back application code. If there is no accessible release or GitHub sign-in, the UI reports a failed check rather than claiming the application is current.

App-authored direct Git dependencies also retain their update policy independently
of frozen revisions. Before recording a new generation, an untyped Git requirement
must resolve unambiguously to a branch at the exact installed commit. For an older
generation whose app dependency was recorded as an unresolved full-SHA pin,
prospective checking additionally requires matching current and stored app
declarations, unchanged recorded manifest/lock/installed graph, and the exact
stored source identity and origin. A moving branch may now be newer than that
verified old installation; the ordinary checked-candidate resolution guard still
binds the next revision. This proof changes only the prospective inventory and
new candidate's policy, never the old generation's receipts. Explicit pins, tags
(including same-name branch/tag ambiguity), custom sources, local edits and
external resolver policy do not acquire branch authority from an `originalRef`
spelling or matching commit.

A provider module has one reference in the app's own files. A profile composes a
provider that has no source of its own from `amplifier_web/provider_sources.json`.
The worker environment declares the same repository in
`amplifier_web/runtime_deps/pyproject.toml`; a decision support pack adds rows
from `runtime_deps/features.toml`. A component update that prepares a worker
environment compares the two references of each provider that a profile
composes, and it refuses the update when they differ, also when both name the
same commit (`runtime_source_alignment.verify_checked_selection`, reason
`protected-runtime-source`). Releases 0.108.0 and 0.109.0 named a commit for the
OpenAI module in the list and the branch in the manifest. On an installation
whose "OpenAI API" connection takes its source from the list, no component
update that prepares a worker environment could complete. An installation whose
settings name the module's branch themselves was not affected: its profile does
not take that module's source from the list. Change the two files together;
`tests/test_provider_source_alignment.py` fails when they differ and names both.
A commit in both files does not repair an installed PC: a new candidate keeps
the reference that the installation already has
(`runtime_environment.augmented_manifest`), so the worker manifest's sources are
branches (`tests/test_standalone.py`). The list is also an input of the web
client: the selected client records its hash, and the built client holds its
text. A change of the list needs a client revision whose
`build-inputs.lock.json` has the new hash, that selection in
`components/web-client.source.json`, and the normal client build. One more file
depends on the module's commit: `amplifier_web/host/media_sources.json` holds
the hashes of the module files that the reviewed image route of ordinary Work
accepts (`host/media_assembly.select_root`). While the list follows a branch,
that route can be on only while the branch has exactly those files. On Windows
it is off in every case (`host/media.bind_media`).

The worker's transitive Foundation override names the distribution without
independently declaring a URL/ref. The effective `tool.uv.sources` choice owns
that identity, including an installed fork or pin carried into a new candidate.
The naming hook is a separate distribution: sharing a repository does not make
its explicitly selected revision follow the Foundation root. Memory's existing
app-source override remains necessary for its old pinned-hook counterexample.
This removes a redundant policy declaration; the local resolver comparison also
honors the effective fork under the previous declaration, so it is not evidence
of the historical Mac resolver failure whose stderr was unavailable.

Local data: `updates/inventory.json` (private source details), `updates/releases/<id>/` (staged sources/configuration), `updates/active.json` (current/previous pointer), and per-generation runtime environments. Previous and referenced generations remain available for rollback and active workers; unreferenced, verified owned generations and environments are reclaimed. Shared source objects are retained pending complete proof that no references remain. State shown to the UI/agent contains credential-safe source labels and commit IDs, not credential-bearing remote URLs.

A stage holds no copy of the app's own `config` or `routing` folder. `config`
holds the sign-in secrets and the TLS key of the installation, and a stage reads
neither folder: its settings and routing are in `shared-config`, with the
temporary copy of the keys file that the next paragraph describes.
An earlier version of the app made such copies. Idle maintenance removes the
entries `config` and `routing` from every stage that still has them, whether
the stage settled or failed, and at every age. It does that before it looks for
keys files (next paragraph), with the same admission: after an activation,
after a start of the app and then about each hour, never while an update
operation runs or waits, and not in the stage of an unresolved preparation.
This removal has its own bounds, and it changes no rule of a keys file. One
pass handles at most eight stages with such a copy and at most 64 entries
(files, links and folders): an entry that it removes, and an entry that it must
leave. A larger copy goes on when a later pass comes to its stage again, after
the other stages. An entry that cannot be removed stays, and a later pass tries
again: no update, retry or restore fails or waits because of it. A pass lists
each folder from its start again. So when the first 64 listed entries of a
folder all stay, the entries behind them stay too. A link is
never followed: an entry `config` or `routing` that is a link, a junction or no
folder stays, and a link inside a copy is removed as a link (on Windows it
stays). This removal reads the list of stages, and it looks at each stage, through
an open folder and not by a path: at a link or a junction that has the name of
the folder of the stages, or of a stage, it lists nothing and handles nothing.
On Windows a folder of a copy that another program has open in a way that does
not permit its deletion, for example as its working folder, stays with
everything below it until that program lets it go.

Component qualification uses a temporary `shared-config/keys.env` copy.
The manager removes only that file after qualification succeeds or a handled
preparation failure. Settings and routing stay. Cancellation keeps the copy.
Later activation or rollback qualification uses fresh credentials from the live
shared home, then removes the copy again unless interrupted. That qualification
never reads a copy that its refresh did not write: an old copy never stands in
for credentials that the person removed or changed. When the live shared home
has no keys file, the stage's copy is removed. When the refresh is refused for
any reason, the stage's copy is removed too, and the qualification runs without
a keys file in the stage; the refused refresh is not an update failure. When
the copy can be neither replaced nor removed, the qualification does not run.
No version is switched and no failed attempt is recorded; the page says "Update
needs attention" with the reason, for an update and for a rollback. The next
activation or rollback request tries again (the owner's pass does that by
itself for a prepared update) and takes the text back when the refusal is over.
After a rollback request nothing tries the rollback again by itself. Its text
goes when a later activation or rollback request gets past this refusal or
switches versions, when a check for updates runs while no update is prepared,
or when a prepared app update starts to install.
A qualification that runs there and fails is recorded and shown like any other
failed update step; the old copy is gone then too.
Time alone does not repeat a qualification at activation that failed. The
update owner's pass runs it again for a prepared update only when something
that can change its result has changed: another prepared update, the runtime
settings or the component selection (the same witness that holds a failed
preparation), or the live keys file (its time and size; its content is not
read for this). An Install request of a person runs it again in every case.
So does the first pass after a start of the app: the hold is in memory and is
never saved. While the hold is in force the update stays prepared, the page
says "Update needs attention" with the failure, and the count of failed
attempts does not grow. As before, no check for updates runs while an update
is prepared. A refused keys refresh is not such a failure: the owner's pass
still tries that again by itself. It also ends a hold: a request that stops
at that refusal validated nothing, so the owner's pass comes back and
validates when the refusal is over. A rollback has no hold: only a request
starts one, and it neither makes nor ends the hold of a prepared update.
Idle maintenance also scrubs at most eight stage copies per pass, even when
unused-code cleanup is off. A validated stage qualifies immediately. An
unvalidated stage that holds no `profile-attempt.json` qualifies only when both
its folder and `shared-config` are older than 24 hours and it is not an
unresolved preparation candidate.
A chat's own setup makes the same copy in a stage of its own and records the
attempt there in `profile-attempt.json`. When that preparation fails or is
stopped, it removes the copy itself; settings and routing stay. A successful
preparation keeps the copy for idle maintenance: its stage is validated. For an
unvalidated stage that holds such a record, idle maintenance goes by the record
alone, never by age. A record that says `failed` or `cancelled` qualifies
immediately, so a removal that was refused is tried again. Any other record
keeps the copy: age does not prove that the preparation ended. So an app that
ended during a preparation leaves a record that says `preparing`, and that
stage keeps its copy. So does a preparation that could neither record its end
nor remove its copy; its error says that the copy stays.
Unsafe paths or removal refusals keep the copy for a later pass.
Failed stage folders stay; this credential cleanup deletes no folder.

If a worker dependency resolves differently from the checked update, activation
stops. The diagnostic receipt identifies the component, checked/resolved commit
IDs when available, and which fields differ: repository, ref, subdirectory,
revision, or a missing Git source. It does not include the source URLs, ref values
or paths. A successful resolver command alone does not prove a matching candidate,
and this failure does not by itself prove that a remote branch moved.

Fresh worker preparation first collects the offered profiles without installing.
It aligns eligible candidate copies and bindings to their checked commits, then
composes a fresh batch and verifies its selected package identities before
installation. Explicit pins and local selections are not permission to refresh
their sources. Final installed-source verification remains mandatory; a failed
comparison reports bounded checked and observed commit IDs when available.

The Foundation package batch uses `uv pip install --no-sources` without
`--group`. Its source comparison therefore excludes contributor dependency
groups (including default/included groups and legacy uv dev dependencies) before
interpreting their pins, markers or extras. Project dependencies, optional
dependencies and explicit source restrictions still participate; no manifest or
checked selection is rewritten. Generic project sync/freeze policy keeps its
existing group rules.

Application activation also verifies that the running host and the executable on PATH belong to the same uv tool installation. A development checkout or a different launcher cannot replace another installed tool. Candidate and installed-package probes use isolated Python imports, reject packages outside that environment, and cannot accidentally validate a checkout from the launch directory or PYTHONPATH. The installed package is checked again after replacement; a failed check leaves the running host up for recovery. The restart helper uses the separate validated environment, and new work remains blocked until restart. It preserves the effective bind addresses, public origins, TLS certificate/key, session lifetime, port, workspace and data directory without writing CLI overrides into saved server settings. The replacement host acknowledges the pending restart only after an authenticated HTTP request to its live listener confirms the expected version, exact installed source revision, data directory identity, and a different process instance. Package probes and application construction alone cannot acknowledge a restart. Smart Tool servers are reported disconnected after restart and can be reconnected explicitly. The recovery record stores the actual prior installation source and available artifact proof, rather than assuming an older release tag exists. Repeated installation requests reuse a pending validated generation. Cache symlinks remain symlinks while staging; external workspace trees are not copied into the update.

## Optional app features

When an older bootstrap requires reinstallation, the updater requests every
package in the qualified graph as well as constraining its source/version.
Constraints alone do not retain dependencies introduced by optional transitive
extras. Installed-graph verification still checks the exact qualified result.

Before replacing an active app installation, the updater durably records
`pendingReplacement`: the source process identity and exact qualified app,
optional extras and dependency digest. This marker closes admission independently
of the displayed error phase. Cancellation, failed replacement verification and
process exits retain it; a package appearing on disk cannot satisfy installation
or permit another feature request. Updates shows **Installation needs
verification** and disables additional installs while this outcome is unknown.
It does not show that warning for this host's running installation:
`updates.activation` is `installing` only while this process executes the exact
replacement attempt. The browser shows **Updating Amplifier** instead. The saved
marker and the complete state read by agents remain unchanged. The field is a
copy for readers and is never the evidence: the page's view and update health
also require that this process executes that attempt at this moment (a set in
memory that is never saved). The owner withdraws from that set first, before
any wait, when it ends. So an error, a cancellation, a restart handoff, a failed
state write or a cancelled cleanup each leave the marker with its warning, and
a new host or a restored state never inherits the claim.

A failed probe after immutable generation promotion can recover without a second
installation or restart. For an ordinary app update (not a forced shutdown or
optional-feature installation), the still-running host must prove its unchanged
identity, previous interpreter, isolated package/import probe, full installed
component graph and optional extras. Only then may it restore the exact prior
launch pointer, under the same pointer-write lock used for promotion. The current
promotion and bootstrap binding must still match; another promotion or a manual
bootstrap replacement is not overwritten. With no previous generation, recovery
returns to the verified bootstrap. The candidate and original failure receipts
remain retained, the update remains failed, and ordinary drain settlement can
release positively unsubmitted inputs once. A failed/cancelled recovery or
unconfirmed state publication retains the work fence; pointer restoration alone
is not installation success or permission to replay work.

Successful replacement verification atomically converts the marker to the normal
pending restart. If the process exits earlier, a healthy new host can reconcile a
complete marker only by proving the exact app source/revision, extras, dependency
graph and authenticated listener/data identity, and repeating the isolated
package/import verification in that new host's interpreter. Managed-browser
recovery uses the same prepared browser cache as candidate and activation checks,
even when the restarted service has no browser-cache environment setting. A
failed browser probe retains the admission fence. The receipt and
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

For a receipt-backed wheel installation, feature additions retain its exact
release URL and checksum even when a newer release exists. Historical Git
installations retain their exact Git revision. The initial installer's local
wheel has no update-generation receipt: its source stamp is visible, but the
existing feature-install limitation remains until an ordinary owned update
establishes the qualification. No enrollment record or separate artifact registry
is introduced.

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

Updates includes an offline changelog packaged from `amplifier_web/release-notes.json`. The full archive begins at 0.11.0 in Git and the immutable GitHub releases; earlier releases are not reconstructed. The packaged and cached changelog is a bounded displayed history: it retains every notice-bearing release, regardless of age, plus the newest 256 ordinary releases. It removes the oldest ordinary entries first if the unchanged 512-entry or 512,000-UTF-8-byte hard limit needs more room. This does not delete conversation history, published releases or notice acknowledgements.

Update checks fetch the same history from the exact published Git commit. Cached notes survive failed checks and restarts. For a duplicate version, the whole installed package entry still takes precedence over cached wording; superseded cached notices are not revived or combined with the installed entry. Fetched future entries retain the existing latest-version ceiling. After that merge, the shared retention rule protects notices before selecting ordinary entries; old cached ordinary entries cannot displace guidance.

Add an entry for every release with `version`, a concise `title`, a nonempty `changes` list, and `notices` (empty for ordinary changes). Add a notice for configuration migrations, compatibility changes, changes to existing workflows, or required action. Each notice needs a stable `id`, `title`, `detail` explaining the impact, and `action` explaining what to check or do. Do not use HTML or invent notices from commit titles. Retention selects complete records without rewriting their fields, notice identities, wording or `previousTitles` aliases. The full older ordinary history remains in Git and immutable GitHub releases.

The release owner follows the [version and notes checklist](validation/release-qualification.md#version-choice-and-release-notes), including maintenance-only releases. Historical omissions are backfilled under their actual release versions and delivered in the next package; published tags and assets remain immutable.

Updates shows a release summary above its actions without expanding a disclosure: the available release before installation, the update owner's exact target during preparation/restart, and the verified running release afterward. Newer discovery cannot relabel an in-flight target. While components finish, the app summary stays on the installed release; unknown target identity and missing notes are labeled rather than inferred. The summary uses the existing bounded release history and never marks high-impact notices read. Important changes and the full Changelog remain expandable below the actions, outside Advanced details. Ordinary release entries do not create attention badges. High-impact notices contribute to the shared Settings / Updates attention badges. `attention.read` marks the current notice wording reviewed across devices when its content is visible. Reviewing is acknowledgment, not confirmation that an action was performed, and never gates installation. Notices remain in history after review; changed wording becomes unread again. Fresh installations begin attention at their installed version, while older history remains browsable; legacy state without that boundary retains its existing notices.

The release validator requires an entry for the package version, rejects future or duplicate versions and malformed notices, and checks that the wheel and source archive contain the validated history. Release preparation and runtime merging use the same retention policy. All rows are validated before selection, including ordinary entries that would age out. The unchanged format supports up to 512 releases, five notices per release and 512,000 UTF-8 bytes, including serialization overhead. If protected notices alone cannot fit, or a newly prepared current release cannot fit beside them, the operation fails explicitly instead of dropping guidance or returning an empty-success history. Preparation fails before writing metadata. Oversized input histories likewise report a capacity error rather than silently clearing notices; missing or malformed history retains the damaged-app fallback. None of this marks a notice reviewed or changes its acknowledgement fingerprint.

The publisher generates GitHub release text from the current entry. Published tags and assets remain immutable. Historical immutable releases through 0.11.2 can still be rerun without a changelog; new releases require one.

### Release workflow

The **Publish application release** GitHub Actions workflow runs when the dedicated version owner `amplifier_web/__init__.py` changes on main, and can also be started manually on main. Dependency or release-tooling changes alone do not start publication. A release requires the same stable `X.Y.Z` version in `pyproject.toml` and `amplifier_web/__init__.py`, with the lockfile updated. Build the frontend and commit the resulting `amplifier_web/static` assets before merging; Git-based installs consume those checked-in files.

The workflow follows the [short publication graph](validation/release-qualification.md): one read-only runner prepares and qualifies the package, frontend and native runtime; an isolated write-enabled runner publishes. It verifies the wheel/source identity, canonical assets and required runtime behavior, then creates the immutable `vX.Y.Z` tag and a draft release, attaches both distributions and SHA-256 checksums, and publishes. One published-package smoke follows; broad verification runs nightly or by explicit request. Publication happens only after qualification and asset upload, so incomplete drafts are invisible to the updater. The workflow uses the repository's GitHub Actions token with contents-write permission; no additional provider credentials or paid model calls are needed.

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

Each application update has an attempt ID and separate receipts for candidate installation, candidate probe, installed-tool discovery, runtime shutdown, tool replacement, installed-package probe/version comparison, and restart. Ecosystem copying, fetching, checkout, compatibility probes and activation use the same receipt format. Failure history remains available independently of present installation health. Staging, checking, or dismissing a notice does not establish recovery. The user and agent read the same state.

The update owner publishes one derived `currentStatus` from its current operation,
desired sources and verified active generation. Earlier diagnostic failures are
history, not an independent health registry. For an older component preparation
failure, positive current configuration, recorded/actual graph and exact activation
evidence can establish current health without matching old requested URL labels
or update-tier batch counts. This does not rewrite or resolve the historical
attempt. A missing package, changed configuration/source, newer failure, incomplete
desired check, partial or uncertain activation remains a current limitation.
A package that the generation's record does not name does not end the health
proof: a connection check can add one. A recorded package that is missing or
changed ends it. A preparation still compares the installed packages with the
record exactly.
After a switch to another generation, the owner publishes the new selection
before it has read that generation's proof. Until that read ends, the page shows
"Checking components" in place of a result from an earlier component failure
record; an error of the switch itself is shown at once. When the switch is
cancelled before it has published the result of that read, or its own error,
the update owner's next pass that no hold stops makes the read and
publishes what it found.
No installation or model call runs just to clear old attention.
An exact verified app restart also supersedes older candidate failures only when
their requested feature scope is known and present. Intentional local source
changes remain protected, informational customization rather than repair debt.
If bounded activation history has aged out, a matching selected graph can be
reported as retained with activation verification unavailable, not falsely healthy
or a request to reinstall. Newer failures, failed activation and unrelated source
scope remain actionable. Settings revalidation uses a neutral pending state.
When a verified application restart is newer than unresolved component attention,
the headline names the successful application update and the earlier component
issue separately, including the attempt's UTC date. Attention and source/retry
guards remain unchanged; an application success is not component recovery.
Current health identifies its selected failure through the sanitized diagnostic
allowlist, independently of the last failure pointer and recent event window.
The support report uses that same selector and includes at most 32 retained
failure summaries, total/omitted counts, and activation/configuration proof
limitations. It does not rename the selected record as the last failure or
discard history. A missing older activation receipt alone remains neutral,
does not request reinstallation, and does not claim full component verification.
If the selected installation's graph and lock still verify, and its activation
receipt verifies or is no longer kept, but its source-configuration key no
longer matches, that alone is no failure and requires nothing of the person.
A saved AI connection for all folders can give this
state: a second connection, a connection of another kind, or another order of
connections of different kinds changes the key; another model for the same
connection does not. A new app version that computes another key for the same
settings gives it too, and health cannot tell the two apart. So the text says
only that the record of the component check is not for the current settings.
After a save that moved `configurationRevision`, the next publication of
changes marks health for another read and wakes the update owner
(`UpdateManager.configuration_changed`). The owner makes that read in its
first pass that reaches it: `tick` returns before the
read while the outcome of a manual hand-over commit is not known, and while
`awaiting_restart()` holds. Until a health read for the new revision ends,
health gives "Checking current configuration" when the proof that it holds,
with this limit taken out, gives a quiet value (`projection`). When that
read finds this state, an earlier rule of `current()` can still decide the
value, for example an
operation that runs or waits, a failure that the proof does not cover, a
listed update or a failed application check. When none does, and either the
check sequence is complete with the application current or a local source
change is listed, the value has the quiet status `unchecked`, with no
attention item for it, so the Settings entry gets no unread mark from it: "The components
need a new check for your current settings" / "The components that Amplifier
recorded are installed as recorded. Amplifier has no record that it checked
them with your current AI connections and other settings. A new AI connection
is one cause. The next update check looks for the components that your current
settings need. To check now, select Check for updates." The first sentence
speaks of the recorded components because the health proof accepts a package
that the record does not name. When no earlier rule decides and neither of
those two holds, the value is "Check for updates" / "The full desired
component state has not been checked.", also with the status `unchecked`. The
bundled page shows a quiet health value when no update operation runs or
waits; while one does, the page shows its own text of that operation. The
text of this state ends when a check lists an update, or when the settings
again have the key of the record. For the other key a check's
inventory adds the row "Qualify current component configuration", unless it
already lists a row for the worker environment; each such row is an update.
A health read is not an update check, and no rule for a check reads this
status. The text is the same with automatic checks on and off, and it promises
no result of the next check. It is not a renewed claim that an older source
conflict is occurring. On Windows the launcher step
(`windows_launcher_follow.project`) reads this status as a quiet one. With no
open launcher request, in the phases `waiting`, `ready`, `failed` and
`failed-unchanged` of that step, the published value holds the launcher's text
in place of this text, in the last three with its button; with no phase, and
in the phase `done`, this text stays.
Only covered failures predating that installation remain historical; newer
failures, missing source scope and uncertain operations retain their priority.
This explanatory proof cannot authorize partial-source retention or declare the
changed configuration qualified. No settings or qualification receipts are rewritten.
A new installation has no selected generation until its first component update.
When setup recorded the worker dependencies it installed, and no generation was
ever selected, health verifies the installed lock, manifest, recorded packages
and installer policy against that record, as a conversation-worker start does.
A complete check that finds nothing newer is then reported as up to date, in
words that name setup's record and do not call those components qualified.
Packages are compared by name, version and source; packages that a conversation
added after setup are not part of the record and are not compared. This record
has no activation time: it cannot supersede a recorded failure and cannot
authorize preparation. It never describes a selected generation: a held setup
proof is checked against the generation pointer each time it is shown or
reported, so it ends when an activation writes the pointer, also when the
promotion after that write fails. A missing or changed record, and an
installation that went back from its first generation, keep the
incomplete-verification report.
An ordinary Check offers an included runtime qualification when app-level source
declarations differ, even if every Git branch is unchanged. Install still uses
the checked selection and existing unchanged-input, source-policy and activation
guards. A successful new qualification can establish current health; Check alone
does not prepare or activate it.

Preparation remembers its input witness and disposition independently of the
displayed application or Smart Tool batch. A settled failure performs no duplicate
copy, resolver or installer work for unchanged inputs. This includes failures
whose cause is unknown: an elapsed deadline or attempt counter does not prove
them transient. Changed source/configuration/application inputs or an explicit
Install request can re-evaluate through the existing install owner. Check is
availability discovery, not retry intent. Saved legacy backoff records retain
the same unchanged-input hold; no timer replays the whole failed preparation.
After a failed component preparation, Try update again checks sources again before
installing what the new check finds. If that check fails or finds nothing to
install, no installation starts. `updates.retryCheck.result` is `retried` (with
the ID of the component attempt that this request started), `not-started`
(checked, and no attempt started, for example while no AI connection is set up),
`other-update` (the request started an update of another kind first, for example
the application),
`nothing-to-install`, or `check-failed` (the new check ended with an error or
could not run). The page identifies the rechecked
attempt. When the earlier failure record keeps its priority, its text says that
the sources were checked again.
Interrupted or unknown effects retain their separate inspection fence.
Pinned and exact checked-revision mismatches retain a typed source-policy reason
and sanitized package identity; they are not transient transport failures.
The preparation witness includes the canonical source-configuration key, checked
source rows, application version and active generation. It also includes complete
module settings/identities, overrides, configurator choices, routing/decision
configuration, effective active profile and the canonical offered-profile set
consumed by qualification. Environment values do not affect unrelated source-policy
holds. For an actual missing-environment failure, the qualification probe retains
only the encountered reference in a private candidate receipt, bound to that
candidate and attempt. This includes references authored inside bundles without
loading/composing those bundles on every tick. Retry checks only whether that
missing value is now available, using the same declared private path, key-file
parser and fresh-worker precedence as qualification. Absent, stale or malformed
evidence grants no retry; unknown outcomes still take precedence. No environment
value is persisted in this receipt or public diagnostics, and checking never
loads keys into the host process.
The dependency graph key alone omits values that can repair a failed profile.
It excludes the unrelated settings publication counter. Reading, ordinary rechecks and non-runtime preferences do not
authorize another identical preparation. Explicit retry still runs every guard.
A fresh install request can reconcile only a recorded, settled candidate-only
snapshot copy with the same durable active pointer and no activation/rollback
handoff. It creates a new candidate, never resumes the interrupted copy. Unknown
or legacy operations, unsettled threads, fetch/installer/native initialization
and activation effects remain fenced; cancellation alone proves none of them
safe to replay.
The unresolved component operation is retained separately from the current
diagnostic display batch; starting an app, tool or rollback batch cannot erase
its fence. Its exact interrupted receipt, not an unrelated latest event, anchors
reconciliation. Automatic waiting blocks component work only: health, storage,
worker adoption and independent app/tool owners can continue without clearing it.
These are preparation scheduling decisions, never authority to bypass source,
qualification, activation or restart guards.

The bundled client consumes that same current status for the headline and
action-required badge. Historical attempts have a separate disclosure, not red
chores with no action. Reading or acknowledging history does not change health.
Rendering performs no source scans or installation checks. The update owner
revalidates retained activation evidence on startup and relevant check/activation
boundaries; an unavailable proof is labeled rather than inferred. Application
readiness and pending replacement fences keep their existing exact-identity rules.

Release-note presentation distinguishes absent details from an empty history or a
failed refresh. A compact initial view has a five-second arrival grace, then says
that details have not arrived and points to the exact published release or reopening
Updates. It does not claim an outstanding network request, trigger an update, or
infer transport failure. Late valid details render without reload; a new release
target gets its own grace. Malformed detail and saved refresh warnings stay explicit.

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

Failed update commands also save a **host-local private explanation**, separately
from those receipts, in `updates/command-details/00.json` through `15.json` under
the app data folder. The application-install error identifies its slot. Match the
file's command and attempt IDs to the receipt: later failures reuse slots, so
an old receipt is not a promise that its explanation remains available. Files
are owner-private (0600 on POSIX, private data-root ACLs on Windows). Storage
failure does not replace the installer result or cause a retry.
`amplifier-unified doctor --verbose` identifies this folder locally, including
when a custom app data folder is selected; it does not read or print the excerpts.

These files contain at most two physical attempts, with best-effort redacted
stderr excerpts of at most 8 KiB each. URLs, paths, recognized credential lines
and their following text, known secret environment values and terminal escapes
are removed **before** excerpt truncation. Redaction is not a guarantee that
arbitrary installer prose is safe to share: review these files locally before
copying any text. They never enter app state, diagnostic capture destinations,
the support report or feedback by default. No new browser exposure is implied.
Both pipes are drained concurrently with bounded retained memory (16 MiB stdout,
128 KiB stderr); oversized stdout fails explicitly rather than returning corrupt
partial command data. Captured byte counts cover observed output, not just the
retained prefix; timeout/cancellation counts may be partial. Incomplete stderr
lines at the capture boundary are omitted. Truncation is explicit.

An allowed anonymous GitHub retry preserves the first failure and the second
attempt in order, including when the retry fails, times out or is cancelled.
Top-level command exit/count/duration facts still describe the final attempt;
each physical attempt has its own elapsed time.
Eligibility, the shared deadline, isolation, active-host retention and the
at-most-one anonymous retry rule are unchanged. These explanations do not infer
the cause of earlier installer failures whose stderr was discarded.

When the app diagnostic collector is available it owns durable storage and configured routing of these events. Otherwise the updater writes a private `updates/diagnostics.jsonl` fallback (0600), rotated at approximately 1MB with one previous file. App state retains the latest 50 events. There is no automatic replay after an interrupted install, paid call, or uncertain result. Historical failures without command receipts cannot be reconstructed from successful later probes; a successful retry does not establish the original cause.

## Managed restart handoff and recovery

For a systemd user service, the host queues `systemctl --user --no-block restart amplifier-unified.service`. Systemd owns the replacement process; Unified does not launch a second unmanaged host. A successful command response means the job was accepted, not that the new server is ready. If systemd terminates the requesting child before it receives that response, the request remains unconfirmed. The attempt ID, expected version/revision, and pending restart survive signals, cancellation, timeouts, and rejected requests. New work stays paused until a successor is verified. Genuine request rejection shows service-restart guidance and retains its diagnostic receipt.

Before a managed restart or service start/stop/restart, legacy generated Linux
units with no explicit kill policy receive `KillMode=mixed` and a private backup.
Systemd then signals the host first, allowing it to settle its workers through
the normal shutdown path; timeout escalation still covers the whole service.
The installed command, environment, data root and explicit operator policies
are preserved. A failed daemon reload prevents restart dispatch; retry reloads
the corrected unit without rewriting it or creating another backup. This does
not replay conversation inputs or clear genuine worker failures.

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

uv may represent a checked moving ref as `rev=<full checked commit>#<same commit>`.
That spelling is equivalent only when repository, subdirectory and exact commit
also match; abbreviated commits, other refs and branch movement still fail.
The checked ref policy is carried separately into qualification and bound again
to the installed source identity. Frozen installs retain exact commits while the
next update check continues following the configured branch.

After the uv-managed launcher supports generation activation, an already tested
application environment becomes the live launch target through an atomic private
pointer. This avoids installing its exact packages a second time. Older
launchers make one normal uv replacement first. A manual uv reinstall/upgrade
changes the bootstrap identity and takes precedence; source checkouts and
unrelated development venvs are never redirected. Package graph and restarted
HTTP readiness are still separately verified before work resumes.
For wheel candidates, the actual bootstrap interpreter must prove it can read
that qualification before promotion. An older reader uses the same verified
wheel through the existing replacement path; an invalid candidate is an error,
not a compatibility fallback. Force restart remains unavailable when that
immutable generation cannot be promoted.
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


### App-managed Core registry updates

Included-component checks also compare app-managed Core with the latest stable
PyPI release. Eligibility requires the app's open-ended registry dependency and
wheel-only policy, an exact PyPI lock entry, and matching installed provenance.
Explicit fixed versions, constraints, alternate indexes, Git/local sources, or
unexplained changes to the recorded installation remain protected.

Preparation pins the checked Core version in an isolated candidate and verifies
both the resolved registry version and the post-preparation installed package.
Other update tiers retain the current Core version. Frozen generation receipts
remain exact for rollback and revalidation; they do not become standing version
pins for future update checks. The Core source-build prohibition remains enabled.

### Work continues while an application update is pending

Checking, staging and validating an application update do not pause conversations,
block new messages or freeze delegation. A qualified update waits for the normal
idle readiness check. Active tools, children, calls and unresolved operations
postpone the update, not unrelated work. Continuously busy hosts can leave an
update pending; the existing explicit **Restart now** control is the separate
operator choice to interrupt work for a qualified restart.

An agent with an active saved objective can instead use `task.waitForUpdate` with
its own `sessionId`, exact task `expectedRevision`, and `target` copied from
`updates.forceRestartTarget` (the existing qualified target identity, not restart
consent). Use a stable dispatch ID. This action does **not** install, force
restart, pause the objective or stop unrelated work. Finish the current safe turn;
do not call `task.block` or `task.pause` merely to yield for the update.

The owner-local wait uses the existing native goal boundary. It preserves the
task ID, continuation epoch, objective, limits and used turns. Existing tools
and children finish before positive checkpoint evidence permits replacement.
Unsubmitted returns wait only for that owner; independent admitted work can
still report on its original request. Other conversations remain usable.

Routine idle configuration refresh retains that exact enrolled worker while its
authorized update wait remains current, including after its checkpoint is ready.
The updater may then use the ordinary acknowledged idle-retirement handshake
for that exact owner when only saved configuration freshness is pending, before
requiring writer absence at the application gate. A refused or unknown retirement
does not prove release; active resources and unresolved configuration operations
remain held. Retirement is not configuration adoption or a continuation input.
Routine refresh must not destroy the checkpoint owner or manufacture a settings failure. New
settings, Stop or task changes still supersede the wait; an absent or replacement
worker is not retained by an older owner's boundary.

After exact healthy `restart-ack`, matching running revision, and released
replacement drain, the host admits one guarded continuation. An already queued
eligible result takes precedence over a redundant update notification. Duplicate
callbacks and reconnects do not replay inputs. A newer Stop, user pause, task
revision/replacement, changed authority/settings or fresh input suppresses that
saved continuation. Failed, unknown, withdrawn or different updates do not
establish successful adoption. Inspect `updates.drain` for the current target
and owner boundary. The returned `task.receipt` reference retains this exact
wait after later updates replace that view, including unknown/suppressed outcomes
and the continuation's `coordination.result` reference when queued. Adoption is
not proof that the continuation was admitted or completed. Do not submit another
wait to recover an uncertain one. No blocked tasks are resumed in bulk.

If the staged target changes or is withdrawn before replacement, the host waits
for the enrolled owner's positive native checkpoint, then cancels that exact
boundary. Confirmed cancellation releases one continuation of the same active
objective, with its used allowance intact. An eligible queued result still takes
precedence. This is recorded as `update-withdrawn`, not successful adoption; it
does not follow the replacement target or require a human nudge. Missing
cancellation acknowledgement and unresolved replacement remain retained. The
same Stop, task, authority and fresh-input checks apply again at admission.
The confirmed checkpoint and cancellation remain saved until the continuation
is durably queued. A storage failure or full inbox retries from that checkpoint,
including after restart, without repeating native cancellation or minting another
continuation.

Another update may proceed while that withdrawn continuation waits for inbox
space. Its release must preserve the original recovery receipt and retry it after
replacement settles, including across restart or another update. That recovery
remains a withdrawal, not adoption of the newer target, and does not cancel the
already cancelled native boundary again.

When fresh input supersedes a wait, confirmation from the exact original worker
that its boundary is cancelled frees that owner's active wait slot. The original
command receipt remains suppressed, including any earlier unknown admission. A
new explicit wait can then use the current task and staged target, even while
other owners are waiting. Missing or uncertain cancellation does not free the
slot; a late observation cannot replace a newer wait or rewrite the old receipt.

After final candidate validation, activation rechecks readiness under the same
host lock that commits replacement. Work arriving during validation takes priority
and postpones that replacement. Only the actual replacement/restart window holds
ordinary new human messages. They are saved under their original input IDs,
acknowledged as saved, and sent in order after the exact restarted installation
is healthy. Stop after saving holds the message without starting work.
Native-accepted or uncertain inputs are never automatically replayed.

Older pre-replacement cooperative holds are withdrawn through their existing
boundary and message receipts. A successful withdrawal releases known unsubmitted
messages and any exact yielded ongoing objective with its original task and used
allowance. An unconfirmed boundary cancellation remains unresolved; it is not
assumed successful. Replacement uncertainty remains fenced and is never treated
as a pre-replacement withdrawal. No new automatic worker-yield boundary is created
merely because an update is staged.
