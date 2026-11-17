# Contributor operating guidance

This codebase is yours: what it's for and how it's built are your call, and you'll keep building on it.

Before broad source reads or searches, use the [source and ownership map](docs/source-context.md).
`python scripts/source_context.py` summarizes tracked inputs without reading
their bodies; use `--format paths` with `--under` to select relevant owners,
contracts and tests. Ordinary `rg` does not apply these context classifications;
use the map's selective-search examples, and expand to retained evidence or
generated output when the task needs it.

## Design for everyday users

Use the [shared-client vision](docs/clients/VISION.md#2-common-meaning-native-interaction)
and [CX8](contracts/client-experience.v1.md#the-promises) for all user-facing work,
not only appearance changes. The baseline is ordinary information-worker,
end-user and consumer usability: plain familiar language, useful defaults and low
cognitive load. UX is golf: every click or stroke counts; fewer steps wins without
sacrificing accessibility, authority, meaningful feedback or recovery. Measure the
whole journey, including entry, repeated setup and keyboard/focus behavior, rather
than optimizing one control in isolation.

## Web client: public source selection

Author browser source, client tests and client build tools in
[`amplifier-client-web`](https://github.com/microsoft/amplifier-client-web), the
sole client authoring owner. Unified selects an exact accepted public revision
and source digest in `components/web-client.source.json`; normal preparation
fetches that selection into ignored `.build/web-client`.
Follow [component ownership and assembly](components/README.md#select-public-client-source).
Do not restore a committed source copy or hand-edit built assets.

A client-only PR merge is not a delivered Unified change. Carry the selected
source through paired qualification and Unified release, or name the integration
owner and hand off exact client and Unified revisions with remaining work pending.
Coordinate with that owner; do not duplicate ports or build campaigns. Report
source acceptance, selection, build/paired qualification and release separately.

Neither disposable `.build/web-client` nor generated `amplifier_web/static/` is
another authoring owner. Unified owns the assembler in `scripts/` and
host/browser/producer checks in [`tests/web`](tests/web/README.md), with a separate
test dependency lock; do not recreate a frontend wrapper package, generated source
view or duplicate client tests.
The normal build replaces static output from the selected client. Verify
source/artifact provenance and served routes after rebuilding; a source-only
login check does not prove packaged browser entry.

## Assess scope and design before taking on issues or PRs

User direction, October 13, 2026: an issue or PR is a proposal, not an automatic
commitment. Before implementing, sponsoring an upstream change, or calling it a
release prerequisite, verify that it affects Amplifier Unified's actual runtime
and dependencies. Shared naming is not evidence of dependency. In particular,
Amplifier Agent is separate from Unified's Core/Foundation runtime.

- Check the problem against `docs/architecture/VISION.md`, the applicable client
  vision, and the relevant contracts. Identify the canonical owner and evidence.
- For direction or contract changes, use [vision and contract guidance](docs/vision-and-contracts.md):
  keep observable promises separate from design notes and implementation evidence.
- Confirmed defects in promised behavior normally warrant straightforward fixes.
- New features and behavior or architecture changes require judgment about whether
  they are desirable, their general client needs, and the right design; do not
  accept the issue's proposed implementation by default.
- Any intentional change in direction must update the governing vision/contracts
  explicitly as part of the proposal before implementing the divergent behavior.
- Keep unrelated upstream improvements separate from Unified release gates.
  Close out-of-scope tracking and correct misleading reviews when discovered.

The user waived the 12 optional IC-09/IC-10 readiness checks on November 10, 2026.
Follow the [recorded scope and reactivation rule](docs/validation/integration-coverage.md#ic-09-and-ic-10-readiness-waiver).
Do not reopen source requests or readiness/release blockers solely because these
checks remain skipped or their dependencies are unavailable. Waived is not passed;
other integration, physical-device and approval requirements are unchanged.

Dependency security changes must cover both the host project and the separately
packaged worker manifest in `amplifier_web/runtime_deps/`. A root lock update alone
does not upgrade a worker. Check actual resolved versions, fresh preparation and
the ordinary new-generation update path without rewriting old runtime receipts.

## Make performance changes reviewable

For changes to repeated reads, publication, persistence, runtime preparation or
provider requests/cache reporting,
follow [performance validation](docs/validation/performance.md). Identify the
canonical owner, triggering operation and inputs that grow before choosing an
optimization. Review work done before filtering or sending a response, including
history reads, copies, scans and encoding. Retain a focused regression check for
the demonstrated cost and the affected history, authority and recovery semantics.
Use measured workload budgets; this does not add a full-suite release gate.

## Keep the development release loop fast

For native runner checks, use the ordinary manual `python-checks.yml` suite
`prepared-runtime-native` on an explicit source revision; `candidate_ref` can
select a separate immutable candidate. A standing scoped task includes dispatch,
inspection, ordinary repairs and corrected runs after known terminal failures,
without nomination variables or another approval round. Inspect uncertain
original operations before new effects; preserve Stop, ownership and cleanup
receipts. Test workflows are convenience shortcuts, not exclusive execution paths.

Follow the [Fast Release Guide](docs/validation/release-qualification.md):
**one PR → review while required CI runs → merge → automatic publication and
installed-package smoke**. Qualify affected behavior first; reuse independent
review and passing tests when their relevant inputs are unchanged.

Coordinate one release owner, selected version and publisher before preparation;
a free tag check alone is not cross-session coordination. Every release needs an
authored What's new entry and a high-impact-notice assessment based on the combined
unpublished changes. Prefer including version, notes and assets in the change PR;
a separate release PR is for batching, not a required extra step.

Use `scripts/release.py prepare --version X.Y.Z --notes-file /path/to/entry.json
--build` and the locked dependency dry-run in an owned, prepared isolated
environment. Reuse matching dependencies and the supported source-staging/build
path. Do not provision a new environment, create a one-off harness or large evidence
archive, add another reviewer, or repeat unchanged feature checks by default.
Review only remaining integration/version/notes/output risks while CI runs.
Merge the exact reviewed head through authorized controls with required checks
and reviews satisfied; never bypass failures or change protections for speed.

The **total routine release target is five minutes or less**, from version/notes
preparation through published-package smoke, including required CI and queues.
Record setup, orchestration, queue and required-check overruns honestly; this
target is not a waiver. Published, smoke-verified and broadly verified are distinct.
Never replace published tags or assets. After an uncertain operation, inspect its
saved result before retrying.

Broad Python, readiness, browser and integration coverage belongs to the single
nightly/manual owner, not a per-release campaign or model monitor. Preserve failures
and skips; a smoke pass cannot close a broad failure. Routine release completion is
published and smoke-verified. Live adoption is a separate assigned operation; keep
the overall task open when adoption is part of the request.

## Bundled client and server evolve together

User direction, October 14, 2026: the web client ships with the server. Change
bundled interfaces together and remove superseded bundled paths; do not add
old-bundle compatibility fallbacks or capability negotiation. Independently
released clients, custom components, and explicit public APIs have their own
contracts and version policies. Preserve or deliberately revise those contracts
with their owners instead of silently changing their promised behavior.

Appearance work follows the [appearance contract](docs/shell/appearance-contract.md)
and the shared-client consumer UX baseline. The approved production redesign uses
Reference deltas, strict format-2 packages and complete Global / This browser
profiles. Keep ordinary choices direct, without Saved/Undo rituals; preserve
Preview Cancel, explicit accent reset and Archive Undo. Old complete themes,
`.078` profiles/libraries and trial records remain inert and undeleted, never
automatic migration/recovery inputs. Preserve independent component placements
and views. See the contract's persistence/default-impact boundary before changes.

## Interactive archive boundary

Keep archive feedback/navigation in the bundled interactive controls, not the
public `session.archive` action. Qualify late-response navigation races, the
noncurrent-chat path, Undo, and the raw action's selection-preserving behavior.
Release the interactive mutation lock on its confirmed receipt, independently of
navigation. Qualify stalled navigation with usable Undo and superseding intent.

## Native Windows work

Native Windows backend support is not yet qualified. Follow the
[setup design](docs/design/native-windows-installation.md) and keep WSL evidence
separate. Never use Windows `os.kill(pid, 0)` for passive liveness; it can terminate
the process. `tests/test_generation_leases.py` includes an unmocked sentinel.
The focused platform workflow selects passive liveness, shared/exclusive repair
locks, bounded UTF-8 worker pipes, private files, owned-process containment and
offline installer fixtures, not full Windows installation. Windows preflight
requires ordinary x64 CPython 3.13 and both PowerShell 5.1 and 7. A separate
focused job checks private configuration, TLS, read-only TokenUser identity and
service policy, plus the real CLI-to-service root binding through a fake native
transport. It uses pytest, cryptography, PyYAML, filelock and binary-only aiohttp;
it explicitly excludes synthetic Task Scheduler registration. Inspect named native
cases and skip reasons in the candidate's CI logs; partial passes do not qualify the host.
Keep its
worker subprocess probes isolated with `-I -S`: installed application packages
must not hide import dependencies in this standard-library check. A cancelled
Windows stdin read stops delivery; its daemon thread/descriptor may remain until
EOF or worker-process exit. Do not join it or close its buffered stream from the
event loop.

The foreground owner-browser reader uses the existing-only native private
credential boundary after qualified cold startup. Qualify the exact installed
public entry and real foreground CLI before presenting a usable trial; source
clearance alone is not acceptance. Keep installer, default/background launcher
and Task Scheduler activation closed pending their separate native acceptance.
Launcher HTTP health JSON is not listener authentication: require the private
local leaf pin and HTTPS before reading or sending a control credential. The standalone process
guard supports ordinary CPython 3.13 only and requires explicit
`sys._base_executable`; do not weaken PID checks to accept a venv redirector.
The windowless app host attaches one console without a window at start
(`windows_hidden_console.py`) so that default-flag console children open no
terminal. Keep explicit `CREATE_NO_WINDOW` at known call sites. After a late
`AttachConsole`, Python signal settings do not protect the host from a child's
Ctrl+C; keep the explicit console control handler and the standard-handle
restore. The stable launcher keeps the code that setup installed; an in-app
update cannot change it. `amplifier_web.windows_launcher_refresh` is the only
supported replacement: it runs with the active generation's Python from outside
the host's Job, and it rolls back fully on failure.
A live conversation worker holds Foundation's session ownership below
`<installation folder>\ownership`. A worker whose Foundation is older than
`e37a946b` makes Windows refuse a rename, a move or a replacement of an entry
directly in the installation folder (error 32); the private writer publishes
by a rename, so it fails there too. From `e37a946b` those changes work. Windows
still refuses a rename of a pinned folder itself (the installation folder,
each folder above it, and Foundation's own), and a rename or a move into
`ownership` or a pinned folder below it
(`tests/test_windows_live_worker_folders.py`). A running worker keeps the
Foundation that it started with. Maintenance code must still expect the
refusal from a worker that started before an update; `app\config\services`
still accepts writes. Launcher maintenance stops the app before such a write
and removes the person's Start-menu and Apps-list entries last.
Uninstall deletes the installation folder only on the person's choice, made
twice, and only exact top-level names (`windows_uninstall`). It writes
`unified-uninstall.json` before the first deletion. While that file exists the
launcher does not start the app, and setup must never install into a folder
that holds it. Add a new top-level name to `windows_uninstall` together with
the code that creates it. A running program cannot delete the files that it
runs from: uninstall moves them through their handles into the folder
`unified-uninstall-last`, and the step after exit deletes only there. That
step works with paths. Its paths are safe only because it inherits two open
files that the uninstall program created through handles; do not give it a
tree walk, a file that it creates or opens by a path, or a rule that rests on
its current folder.
`setup-tls force` on a key-less identity makes a new root, and Windows can
still hold the earlier one for the person. The command records each root that
it replaces in `config\tls\replaced-roots.pem`; only that command writes the
file. The launcher (after the person's Yes to the new root) and uninstall ask
Windows to delete a recorded root only when it has the fixed shape of the
product's root and the store holds exactly that certificate
(`windows_trust.replaced`). Never find a root to delete by its name, in code
or in a guide: another installation's root has the same name. The record
never loses a root that Windows may still hold; a full or unreadable record
stops `force` with nothing changed. Windows asks the person once for each
deletion, and no code answers it; say the number of questions before them.
Windows has no exec: `os.execv` starts a new process there and ends the
caller at once with exit status 0, before the command's output is complete.
The launcher hands a command to the active app generation with
`application_generations.hand_over`, which waits on Windows and ends with the
app's status. Do not use `os.execv` on Windows where a caller reads the
output or the status. A launcher from a setup file before that change still
returns 0 at once, so the confirmation line of `open` stays the launcher's
proof that the browser opened. The Start-menu start shows `windows_start.sign`
and holds `windows_start.only`; both must end before the launcher's own last
message, which sends the person back to the Start menu. While the start holds
that lock, a second click does nothing and shows nothing.
Task Scheduler expands percent-bearing executable/working-directory paths.
Reject those paths rather than recording a literal identity that launches a
different program. Task registration/state is not authenticated host health.
Task Scheduler stores the sign-in trigger's user as a name (`COMPUTER\user`),
even when registration gave the SID, and never rewrites it; the principal stays
a SID. After a computer rename Windows still starts the task, and that name
maps to nothing. `_same_account` in `windows_service.py` is the one identity
equivalence in the ownership check: keep "Windows says no such name" apart from a lookup
that failed, and keep every other field an exact comparison. The launcher runs
that check before any app version does, so a fix to it reaches an installed PC
only with a new launcher. `windows-task-after-rename.yml` registers the real
task on a hosted runner and renames that runner's account domain. Its fixture
changes the machine's security policy: it runs only with the workflow's opt-in
in a hosted runner's environment. That guard stops accidental use and does not
prove the machine; never run the fixture on a machine that is kept.
Python on Windows accepts only root certificates that the Windows store holds
at that moment, and aiohttp builds its default list once at import. A new PC's
store lacks many public roots (seen in Windows Sandbox: the package registry's
root entered only after a Windows program asked for that site). An outbound request
that must work there verifies against the system store plus the bundled roots,
built per request (`update_checks.registry_trust`). Never turn verification off.
A new installation has no component generation until its first component
update; update health then verifies setup's record (`update_health.setup_evidence`),
which is weaker than a qualified generation and never authorizes preparation.
On every platform a component update waits for the first AI connection
(`profile_catalog.connection`): the update owner starts no candidate and records
no attempt, because a profile with no provider cannot qualify. The rule and
bundle composition share `host.config.provider_dropped`; keep them on one rule.
It makes a claim only for the app's own profiles. A connection changes the
checked selection, so the owner checks again before it installs. Update health
never reclassifies an earlier refusal record as waiting: its form does not
prove that only a connection was missing. A later completed update settles it.
The installed-package reader runs Git for a package from a local checkout, so
a caller without the installation's Git folder on PATH reads "not prepared".
Until that first component update the Foundation home is the data folder
itself (`updates.foundation_home`), not setup's receipt folder
`updates/baseline-runtime`. A conversation there fetches a declared source and
installs its modules at first use; the module's build view carries its commit
as its ref, and the branch is in the binding below
`<data>/foundation/cache/.source-bindings`. A reader that looks below the
receipt folder finds no binding, takes the commit for an unexplained fixed
version, and offers "Refresh included components": a full component update
that changes no component (`runtime_environment.shared_build_policies`,
`tests/test_update_new_installation.py`). For the same reason an absent
declared source is no update there (`update_sequence.classify`). An update
must add it in two other states: with a selected generation, which is
immutable, and with no worker environment, where the first update prepares
everything. The first real component update then makes the first generation.
A chat-installed module of the other tier that is behind its branch at that
moment gets the existing moved-reference record
(`runtime_source_policy.qualify_cache_policies`). The sequence's next check, of
the other tier, lists its source as an update, and that second stage moves the
module and records its branch again: two stages where one was enough before.
A full check between them lists the record as "Refresh included components".
`UpdateManager.check_due` is the one rule for a check that time causes: the
timed check, and one check at a host start when the last check or attempt was
at least 1 hour old. A one-time reason must end by a fact that the owner keeps
(`_start_check_open`, ended when a check passes its admission), never by
comparing a saved time with the start time: a clock that was ahead at the start
and is corrected later would name the reason again on every pass.
A reader of a private file can hold an object that another process replaced
(`publish_private(replace=True)`). Windows then reports 0 links and a path below
`$Extend\$Deleted` for it. That is `PrivateFileUnlinked` in `windows_files.py`,
never a hardlink or an alias of the named file, and never accepted:
`_existing` opens the name again and validates everything again, bounded in
count and time. A handle that `_existing` returned can lose its name later; do
not read a later `info()` of it as a refusal of the named file.
A folder below the installation folder that the app itself uses as a session
workspace gets a CLI project name that repeats the installation path, so its
session paths pass the Windows limit with long paths off (the first chat of
0.94.1, the update stage's compatibility probe of 0.98.5). Give such a folder a
short project name in `session_files.project_slug`, add its rows to
`tests/test_windows_path_budget.py`, and run its real writer in
`tests/test_windows_long_paths_off.py`. A folder that a person chose keeps its
name, with one exception: in a process that runs with an update stage as its
app home and that stage's `shared-config` as its shared home, every folder has
the stage's name (`session_files.stage_project_name`).
The session of such a check is not a saved conversation. A chat's own setup is
checked there with the chat's folder, and `tests/test_windows_chat_setup.py`
runs that whole preparation on a hosted runner and lists every path that it
writes. A path may pass the limit only in the places of `NOT_NEEDED` in the
budget test. When the path of a person's folder is
too long for the chat's files, `session_files.chat_folder_too_long` gives the
answer; do not give that folder another project name without a change of the
[history contract](docs/workspaces/contracts/history.v1.md).
A hosted runner's account is elevated, so a folder from an ordinary `mkdir`
belongs to the Administrators group and the private-folder check refuses it. A
native test that runs product code with such folders sets its process's default
owner to the account (`token_user_default_owner` in
`tests/fixtures/manual_app_handoff_native.py`), as a person's PC has it.
A module can build a file path from the person's home folder and ignore
`AMPLIFIER_HOME`. On an installation from the setup file that file is outside
the installation folder, and uninstall leaves it (the Anthropic provider's
`rate-limit-state.json`, seen in `%USERPROFILE%\.amplifier`).
`provider_state_files` gives such a module paths in the shared Amplifier home
when Core loads it, and in the probes that construct a provider for a model
call. Never write such a path into a mount plan: a plan is saved, hashed,
compared and exported, so a schedule or a queued change of an earlier version
would then be refused. Add a module to `STATE_FILES` only after reading its
source; `tests/test_provider_state_files.py` runs the real module and fails
when a later version adds a file or changes a name.

## Keep agent context and coordination useful

Workspace starter instructions and the coordinate-work skill must support one
manager coordinating many independent workers through complete deliverables.
Prefer current task briefs, exact outcome links and on-demand details over
repeated whole-history reads or routine progress/approval exchanges. Preserve
actual Stop, authority, ownership and unknown-effect recovery boundaries.

Automatically loaded scratch is a short current context/index, not task status,
a release ledger, event history or new authority. Keep detailed evidence with its
owner; replace stale current entries and preserve useful history outside automatic
includes. Changes to these templates should verify rendered prompts omit linked
archives while current notes still reload, and preserve existing user files.

Apply the [context contract](docs/workspaces/contracts/context.v1.md): keep the
always-on host guide short, put specialist recipes behind existing skill/schema
discovery, and retire superseded workarounds with their code. Reuse repository
guidance until its relevant source or scope changes. Validate actual composed
requests and installed companion discovery; fewer bytes alone do not establish
better model behavior or authorize removing user-selected capabilities.

## Ecosystem maps

Keep this repository's README and product-page maps aligned with the
[Constellation map guide](https://github.com/microsoft/amplifier-constellation/blob/main/docs/ecosystem-maps.md)
and its canonical registry; start with this repository's
`docs/ecosystem-map.json`. When creating, renaming, splitting, retiring, or
changing a role, interface, parent, or dependency, inspect incoming and outgoing
relationships and update every affected parent/consumer map and link, including
the centralized product pages. Regenerate from source data and synchronize local
records/images rather than hand-editing generated images. Describe actual
boundaries and optional choices; a role highlight is not a compatibility claim.
Documentation work does not authorize deployment or resume paused product work.
