# Managed test runner

This extends the existing task environment and queued-check contracts. The host
owns setup, admission, preparation, execution and evidence-preserving cleanup.
The user and agent use the same actions. A browser tab or model turn is not the
lifetime of an accepted check.

## Setup and readiness

`workspace.environments.setup` defaults to an existing Docker service and
reuses an installed maintained DTU Smart Tool, or installs it through ordinary
Smart Tool package handling. This is the Compose-based **Smart Tool DTU** library, not the legacy
Incus bundle or a raw CLI replacement for host controls. Choose `backend: incus`
explicitly for specialized native workflows. Registered remote machines remain
Incus-only; remote Docker registration is not supported. A default never converts
an existing backend, resource, request or unknown outcome.

Setup accepts a backend choice and stable request identity, never an
arbitrary executable, socket, command or credentials. Setup does not
install a container daemon, alter firmware, restart shared services or configure
a model provider. Missing platform or backend requirements are reported before
an environment is launched.

Setup is a separate host effect: a direct user action authorizes it, while an
agent needs `test_host_setup` in global scope. This does not grant authority to
change unrelated configuration. Routine task-owned testing needs no separate `test_environments` approval.
Root ownership, Stop and current backend identity still govern checks. Passive
discovery and result reads do not request configuration authority.

Setup retains its identity and phase before effects. Repeating a request returns
its retained result; interrupted effects are observed, never blindly repeated.
Ready requires an actual daemon/Compose and image observation, not a disposable
Docker certification guest. Readiness includes its observation time
and does not claim every project's dependencies are prepared. Upgrading a backend
does not replace the retained backend of an existing environment.

## Runner update policy

Docker trusts the maintained `digital-twin-universe` distribution from
`microsoft/amplifier-smart-tool-digital-twin-universe`, imported as
`digital_twin_universe`. There is no per-operation whole-venv hash, runtime
bytecode census, historical commit allowlist or target-local qualify/promote
gate on this path. Source revision, package and image provenance remain ordinary
installation evidence. This trust choice does not make containers an OS sandbox.

App-managed runner packages follow their declared moving upstream source under
the global update preference. A resolved commit is an installation identity,
not a standing update pin. Explicit user pins remain explicit. Existing
app-generated pins can be migrated only with an owned setup receipt establishing
their origin; matching a package name or a familiar commit is insufficient.

Docker uses ordinary Smart Tool installation/update lineage for future checks,
not another scheduler or certification journal. Updating code must not rerun host
setup, alter a daemon/project/network, or reset operator limits. A retained old
Docker qualification attempt cannot restart its probe or promote its candidate;
its previous results, resources and unknown effects stay inspectable.

Selection is an atomic change for future accepted checks. Existing jobs,
environments, unknown outcomes and rollback references retain their exact
installation, controller, configuration and execution target. A lost reply or
restart reconciles the saved operation; it does not repeat a check or replace an
unknown environment. Old packages remain available until all references are
settled. Explicit Incus and registered-machine enrollment retain their existing
qualification and exact controller/engine bindings. A Docker default never
converts those routes or claims fresh native-platform acceptance.

### Incus engine generations

The Incus engine is a separate update source from the deterministic runner core.
For an app-managed local or registered native Linux target, availability checks
inspect the installed engine and its retained source policy on that actual
machine. Explicit pins and installations without setup provenance remain
protected. Passive discovery does not install a controller or change host setup.

A candidate installs into a private target-owned generation and must finish a
fixed source-copy, offline command, retained artifact and owned-removal check.
Only then can the target atomically select it for future tests. An unchanged
dynamic core can retain its previous proof through a composed receipt recording
the exact core proof and new engine proof; changed code or unrelated settings
cannot borrow those proofs. Original core rollback and accepted-work references
remain valid. Capacity-only edits do not erase a declared moving source policy.

Confirmed no-effect capacity refusals may make a new admission attempt. Unknown
launch or removal outcomes retain the exact original owner and are observed
before continuing. A missing retained artifact fails qualification even when
native absence is confirmed; the proven absent owner releases capacity.

The first implementation removes known settled, unpublished failed or skipped
private runtimes and retains their receipts. Prior selected generations and
unknown installer/guest outcomes remain in the private generation store. Finite
reference-aware retirement of prior Incus generations is not implemented yet.
This differs from core-package retirement and must be reported as retained
storage, not completed cleanup. Native Windows automatic Incus updates are
unsupported; passive Updates inventory still works without POSIX modules.

## Prepared profiles

For Docker, `linux-python`, `python-project`, `browser-test` and `app-preview`
are resource presets over ordinary Compose. Pass `composeFile` relative to the
selected source for an editable trusted Compose profile, including custom images,
build contexts and loopback published ports. Build inputs are copied source,
not writable original mounts. The twin must provide Python 3 and run its
supervisor as root; test commands run as an unprivileged user. Browser dependencies
and app launch use the profile's normal image/build/dependency commands rather
than a second host-owned recipe language. Incus retains its prepared profiles.

The normal path is one queued check. When admitted, the host freezes the Git
working area and starts the test environment. It does
not require a user or agent to hand-author private backend configuration or
manually build an image. Docker preserves normal network access and build cache.
`offline: true` explicitly requests internal networks; it is not an implicit
credential or host-access grant. There are no inherited provider credentials. A
private dependency that needs unavailable authentication is a preparation failure,
not permission to reuse an unrelated host environment.

Keep useful logs, reports, generated patches and build outputs in `/artifacts`.
New Docker copies retain that selected evidence and the original source snapshot.
`preserveSource: true` explicitly adds the complete guest workspace export; a
multi-GB source export is not a mandatory teardown prerequisite for a disposable
copy. Old owners keep their original preservation contract. Cleanup failure is
reported separately from the test result and cannot become a passing check.

## Preview and Reality Check

The same environment may be retained for interactive preview instead of removed
after a queued check. `app-preview` publishes a loopback port 8000; a custom
Compose profile may declare its own `x-dtu.urls`. Start the app with its normal
command, then use the actual returned reachable URL. Stop and remove remain
explicit owner-scoped actions.

Reality Check's `check_software(request=..., target=<reachable URL>,
keep_running=True)` can inspect that endpoint. Unified keeps DTU lifecycle
ownership; Reality Check cancellation is not teardown. A private DTU ID alone
does not attach Reality Check's separate interpreter/state root. Signature and
endpoint interoperability are deterministic evidence, not a model/browser
campaign or an equivalent package revision merely because semver matches.

Dirty local Git and linked worktrees use DTU's maintained snapshot/export path.
It copies current tracked bytes, nonignored untracked files, deletions, modes and
link text without host `.git`, hooks, configuration or credentials. Git-derived
version builds instead need DTU's explicit repository-publishing path or an
honest version override. A synthetic dirty snapshot is not the candidate SHA:
select the actual rewritten ref when installing by Git URL, or use an artifact
build and verify the installed sentinel outside the source/PYTHONPATH. Old tags
and original SHAs still mean their original bytes. Unsupported submodules or
colliding explicit repository basenames require correction, not HEAD fallback.

## Durable execution

New queued checks have a guest job identity bound to the existing root, launch,
source and command. The guest supervisor retains process identity, bounded output,
deadline and terminal result independently of the host connection. Commands run
without write access to the supervisor's control records. Lost acknowledgements
and host restarts observe that exact job; they do not run its command again.

Cancellation is a retained request, followed by observed termination of the owned
job or preparation process. A cancelled UI wait is not evidence that the process
stopped. Unverifiable process or backend identity leaves the result unknown and
preserves ownership. Cleanup begins only at a confirmed terminal boundary.
Old operation receipts retain their prior semantics and are never converted into
new executions during recovery.

Two root chats can make independent progress subject to physical resource
admission. Waiting for capacity belongs to the host queue, not repeated model
polling or peer-message exchanges. Completion notifications use the existing
saved subscription and current conversation guard; notification delivery failure
does not rerun a completed test.

## Qualification

Source tests, real container checks, browser controls, and installed adoption are
separate evidence. Qualification includes setup through shared actions, Python
and frontend workloads, the complete long coexistence workload, two roots,
cancellation, a host restart while checking, retained output, notification and
owned resource cleanup. Linux qualification does not imply native macOS,
Windows, WSL or a hosted-runner path is supported. Interactive access to an app
inside the test environment requires a separately qualified preview route;
headless browser tests inside the environment do not establish that capability.

For explicit Incus qualification, executable bytecode is part of a dynamic runtime's exact identity, including
cached and sourceless Python modules. Managed helper and snapshot exports disable
bytecode writes so normal use does not alter the qualified generation. The
registered controller runs only the source bytes in its retained verified
manifest; adjacent bytecode cannot substitute for those sources, including when
an older controller bootstraps into its qualified interpreter. These checks bind
application-owned execution evidence; they are not an OS security boundary.


## Remote testing from Mac, Windows, WSL or Linux

A registered Linux machine can be the first and only testing backend. In global
Settings → Testing → Connected test machines, choose an existing SSH connection
to a machine whose native Incus runner is already configured. The origin needs
Git and an SSH client with non-interactive authentication and a trusted host key.
It does not need Docker, Incus, a Linux runtime or a managed DTU installation.
Registration installs only the app's fixed, verified controller in the target
account; it does not initialize a daemon, network, image or project. There is no
new per-check permission prompt.

The origin creates a deterministic snapshot of the selected Git working area,
including dirty tracked and nonignored untracked files. Git metadata, ignored
files, host configuration and credentials are not injected into the guest.
Tracked source remains source: this is not a secret scanner. Source links are
copied as link text without reading their destinations; submodule entries do not
recursively copy other working areas. Snapshot output stays outside the source;
no index, refs, hooks or source files are modified. A changing source, unmerged
index or unsupported file fails before environment creation.

The snapshot exporter is bundled with the app. Native Windows uses pinned Win32
handles, rejects parent junctions/reparse points, and verifies retained authority
records' owner and ACL. The Windows token's user or verified default administrator owner must own those
records. OWNER RIGHTS grants refer only to that verified owner; inheritance-only
entries do not grant access to the current object. Ordinary other users cannot
have write access (SYSTEM and Administrators retain
normal OS authority). POSIX reads and writes remain relative to pinned directory
descriptors, so replacing a parent cannot redirect a source read or receipt.
Git for Windows link-text checkouts and executable index modes are preserved.

Accepted checks retain their original controller, exact target, resource limits,
source hash and cleanup identity. Pausing a machine prevents new selection;
existing work and lost responses retain their original route. A waiting check
may choose another of its already accepted targets before admission. It never
moves after an uncertain effect. Remote capacity, execution and cleanup use the
existing per-machine account authority; no origin-wide pool replaces it.

The origin retains source and downloads verified evidence after target cleanup.
If a download is interrupted, the authoritative target export remains available.
Removing or pausing a current registration does not remove an admitted owner's
retained recovery descriptor. A target whose SSH identity changes must be repaired
through normal host trust; the app never silently accepts a new host key.
