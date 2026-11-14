# Managed test runner

This extends the existing [task environments](test-environments.md) and
[queued checks](../QUEUED-TESTS.md). The host owns admission, durable jobs, results
and cleanup; a browser tab or model turn is not an accepted check's lifetime.
User and agent callers use the same actions and existing authority.

## Docker: trust the installed Smart Tool

The default local backend is the maintained Compose-based **DTU Smart Tool**:
`digital-twin-universe` from
`microsoft/amplifier-smart-tool-digital-twin-universe`, imported as
`digital_twin_universe`. It is not the legacy Incus bundle.

Docker has no per-operation whole-venv hash, bytecode census, historical commit
allowlist or target-local qualify/promote gate. Source revision, package version
and image provenance remain ordinary installation evidence. Existing package
references stay retained. This trust choice does not make containers an OS sandbox.

Setup reuses a maintained installed Smart Tool, including its chosen extras, or
installs it normally. It observes the actual daemon, Compose and base image;
it does not create a disposable certification guest. Readiness carries its
observation time, not a claim that every project's dependencies are prepared.
Setup never installs a daemon, changes system services, firmware or provider
configuration, or adopts another owner's environment.

Setup is a separate host effect. Existing direct-user authority, Ask-mode
`test_host_setup` scope and standing Full access remain unchanged. Routine
owned testing needs no additional approval. Root ownership, actual Stop,
placement, access revision and configured operator limits are checked before
effects. Setup saves its intent before effects; uncertain operations are inspected,
never replayed by repeating a request.

## One Compose flow

`linux-python`, `python-project`, `browser-test` and `app-preview` are resource
presets for new Docker work. Pass a trusted `composeFile` relative to the selected
source for an ordinary editable Compose YAML/JSON profile. Images, build contexts,
normal network/dependency commands and Docker's normal build cache are available.
There is no new profile language, scheduler, registry or cache manager.

Build contexts, Dockerfiles and explicit file inputs come from copied source,
never writable original-source mounts. Profiles do not implicitly receive
credentials, a Docker socket, privileged services, host namespaces, external
volumes/networks or inherited foreign-container mounts. File references must be
literal copied-source paths, not host-path interpolation. The twin must provide
Python 3 and permit a root supervisor with CHOWN, SETUID, SETGID and KILL;
workload commands run unprivileged and cannot write supervisor control records.
Other services may use their ordinary application users.

Networking is normal by default. `offline: true` explicitly selects internal
networks; network modes that bypass that selection are refused before launch.
Resource presets and saved operator caps still govern physical admission. An
unavailable private dependency is a failure, not permission to borrow an unrelated
host environment or inject credentials.

The normal journey is one queued check: freeze source, start, execute the existing
durable guest job, retain its result, stop, export selected evidence and remove
only that environment. Manual operations use the same owner for a retained
interactive preview; they do not introduce another lifecycle framework.

Keep logs, reports, patches and chosen build outputs in `/artifacts`. New
explicitly disposable Docker copies retain that selection and the verified
original source snapshot. `preserveSource: true` additionally exports the full
guest workspace; multi-GB source export is not mandatory for a disposable copy.
Old owners retain their original preservation contract. Failed selected export
blocks destruction. Test result and cleanup outcome remain separate: removal
cannot turn an unknown or failed test into a pass.

## Local Git and testing as installed

DTU's maintained snapshot path includes current tracked working-tree bytes,
nonignored untracked files, deletions, executable modes and symlink text, without
host `.git`, hooks, configuration or credentials. It does not follow source links.
A changing source, conflicted index or unsupported source entry fails before launch.
Copied-source submodules are refused rather than silently exporting empty content.
The existing repository-delivery client-input exception remains owned by the
source exporter; selected ignored client inputs must be prepared, not guessed.

For Git-derived version builds, use DTU's explicit repository-publishing path or
an honest version override. Repository publishing retains history in a private
snapshot and serves selected references as if released. A synthetic dirty commit
is not the candidate SHA; old tags and original SHAs still mean their original
bytes. Select the actual rewritten feature ref or build an artifact, then install
it into a fresh environment outside the source with PYTHONPATH unset. Verify its
installed import location and dirty sentinel. Colliding repository basenames and
unsupported submodule layouts require correction, not silent HEAD fallback.

## Preview and Reality Check

`app-preview` publishes loopback port 8000. A custom Compose profile may declare
its own loopback ports and `x-dtu.urls`. Start the app with its normal command and
use the actual returned reachable URL. Keeping a preview leaves lifecycle and
cleanup responsibility with its original host/task owner.

Reality Check can consume
`check_software(request=..., target=<reachable URL>, keep_running=True)`.
Cancellation of that check is not DTU teardown. A private DTU ID alone cannot
attach Reality Check's separate interpreter/state root. Callable-signature and
endpoint interoperability are deterministic evidence, not a model/browser
campaign. Matching semver alone does not establish matching installed revisions.

## Updates and recovery

Docker uses ordinary Smart Tool package updates for future work. A resolved
commit is installation identity, not a standing update pin. Explicit source pins
stay pinned; migrating an app-generated pin requires its owned setup provenance.
Updating code does not rerun host setup, change a daemon/network or reset limits.
The ordinary selected update lineage handles rollback without rebinding old owners.

Accepted environments, admitted jobs, unknown effects and rollback references
retain their exact original installation, configuration and backend identity.
A retained old Docker certification attempt cannot restart its probe or promote
its candidate; old evidence and unsettled owners remain untouched. Removing a
package cannot strand its current selection or an existing owner.

Known local refusal before helper/backend dispatch is failed/not-started, not an
unknown backend effect. Once dispatch may have occurred, a lost acknowledgement
observes the same guest/job and never repeats its command. A failed observer or
cancellation dispatch does not overwrite the admitted command's result or clear
its ownership. Later preparation cleanup has its own outcome; an earlier launch
refusal is not proof that cleanup did not dispatch.

## Explicit Incus and connected machines

The Docker default never converts saved Incus configurations or registered
machines. Those remain explicit Incus-only routes with their existing exact
controller/engine/source/bytecode bindings, offline guests and prepared profiles.
[Registered test machines](../registered-test-machines.md) owns connection,
enrollment, pause, refresh, target receipts and artifact-download recovery.

Incus core/engine candidates retain their target-owned lifecycle/artifact proof,
composed exact core-engine receipts and original rollback references. Capacity
edits preserve source policy; actual machine/account/daemon/project identity
still governs. Known no-effect capacity refusals can wait; unknown launch or
cleanup never moves to another target. Missing artifacts fail qualification even
when physical absence permits releasing capacity. Selected/unknown Incus engine
generations remain retained; finite historical-engine retirement is not added.

Remote origins may use macOS, Windows, WSL or Linux with trusted SSH and Git,
without a local Docker daemon. Remote Docker registration is unsupported. The
bundled source exporter preserves safe POSIX descriptor reads and Windows pinned
handles, rejects redirected parents, and verifies private owner/ACL records;
Git-for-Windows link-text and executable index modes remain supported. Refresh or
Pause cannot erase an admitted owner's original controller or accept a changed
SSH host key. Incomplete evidence download leaves the target export authoritative.

## Evidence boundaries

Focused source checks, actual owned containers, installed artifacts, rendered
browser behavior and live adoption are distinct. Reuse unchanged evidence;
retiring certification does not create a new broad per-change campaign. Historical
results remain in [validation evidence](../validation/managed-test-runner.md).
Linux proof does not establish native macOS/Windows/WSL setup, remote Docker,
physical input or a provider/model campaign. Shared resource observations do not
reserve capacity against unrelated host activity. Failed and skipped checks stay
failed or skipped; no old unknown owner is silently retired.
