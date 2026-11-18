# Native Windows installation

Status: implementation design and qualification record. The README offers the
consumer setup file for Windows 11 as an unsigned preview. This document is
**not a native Windows support claim**.

## Consumer lifecycle completion

The current user goal is a normal downloadable Windows application, not a
developer bootstrap command. Installation must supply its private prerequisites;
an information worker must not need WSL, Python, uv, Git, PowerShell 7, a compiler,
or a command-pasting procedure. Normal launch uses a shortcut and a windowless
owner-logon host. The selected lifetime is unchanged: survive browser/terminal
closure while signed in, start at the next sign-in, stop at sign-out.

The retained prototype and foreground qualification do not satisfy that promise.
Acceptance also requires official download/installer identity, the actual ordinary
user entry and browser trust/onboarding, managed cross-version update and rollback,
restart after sign-in, and removal that preserves user data. Signing and download
reputation must be reported separately; a publisher checksum is not a signature.
No public support gate opens merely because a source fixture passes.

The consumer candidate selects the installed `pythonw.exe` sibling for its
Scheduled Task; it does not hide or keep a PowerShell process. Older exact
`python.exe` task definitions remain inspectable for explicit replacement and
rollback. Private runtime receipts still identify the actual host PID, exact
creation time, process image and listening identity. Native windowless worker
containment and full background lifetime remain affected qualification boundaries.

### One console without a window for the host's child programs

`pythonw.exe` has no console. Windows creates a new, visible console for every
console program that such a process starts with default creation flags. A
no-window flag at each known call site cannot cover code that the application
does not own.

The app host therefore attaches a console that has no window as its first step
(`amplifier_web/windows_hidden_console.py`). A console program started with
default flags inherits that console, and so do the console programs it starts.
The mechanism lives in the app generation, so an in-app update delivers it. The
stable launcher is not changed by it: a launcher keeps the code that setup
installed.

Rules that the mechanism depends on (measured on native Windows 11, CPython 3.13):

- A console attached after process start resets the process's console control
  handlers. Python's signal settings then give no protection: one Ctrl+C event
  from a child on the shared console ends the host with `0xC000013A`. The host
  registers an explicit handler for Ctrl+C and Ctrl+Break.
- The host's standard handles are restored to their earlier values after the
  attach. A child must not inherit console input that nobody can type.
- Both steps follow the attach at once, before the helper process is released.
  If either fails, the host releases the console and continues without it.
- A failure of this mechanism does not stop host startup, with one exception.
  If the host stays attached to a console that it could neither protect nor
  release, it does not start. The release is confirmed by state. Continuing
  would leave a host that one child's Ctrl+C ends later, without notice.
- Known call sites keep their explicit no-window flag. Such a child gets its own
  console and does not share the host's.

Normal Stop is unchanged. The host stops through its private stop request and
never through a console event. No host code sends a console control event.

Sign-out is unchanged in effect. Before, Windows ended the windowless host at
sign-out with no notice. Now the host is attached to a console, so Windows can
send it the sign-out console event first. The handler does not handle that
event (or close or shutdown), so Windows ends the process at once. In both
cases the host ends without its normal stop sequence, and the launcher's
lifetime job closes its descendants. This case is not measured yet.

Limits. This is a default, not a sandbox:

- A program that requests a new console still gets a window.
- A windowed (GUI) child and a detached child have no console. Their console
  children get a new window unless that child prevents it.
- Children that share the host's console also share its control events, code
  page and input. Measured: a Ctrl+C event that one child sent to the whole
  console ended a sibling child that had default handling; the host continued.
  This applies to the host's own default-flag children only. The worker tree
  is not affected: the launch guard starts each payload with the no-window
  flag, so each worker has a console of its own, and its tools (the shell
  tool's bash, Git, MCP servers) inherit that one.
- A child that asks a question on its console (a Git or SSH credential prompt)
  now waits unseen until its caller's time limit. Before, an unflagged child
  showed a console window. Host-side Git calls that can reach a remote should
  set `GIT_TERMINAL_PROMPT=0` or a time limit.
- Ending the hidden console's `conhost.exe` ends the host.

### Stable launcher and update recovery

The supervised candidate keeps the owner-logon task's `pythonw.exe` as a stable
launcher. Each app generation runs as a contained child, not a detached helper.
An ordinary service installation command may itself be delegated to the active
app interpreter. Registration must still select the exact original bootstrap's
windowless sibling after rechecking both candidate and bootstrap identity; it
must not make that disposable app generation the new stable launcher. A changed
bootstrap or candidate refuses registration rather than relaxing launch identity.
A non-inheritable lifetime job belongs to the launcher; closing its lifetime
closes descendants. The actual app publishes its private run/PID/FILETIME/image
receipt. A venv redirector's PID and Scheduler EnginePID are not accepted as the
app's identity. Each app launch reuses the canonical finite owned-process guard;
the actual host must be a native member with the exact birth in that retained
job. Even an app that fails before publishing its runtime identity needs the
guard's confirmed root/descendant exit and finalized I/O before recovery.

An ordinary update still uses canonical preparation, official wheel identity,
installed graph verification, idle/drain guards and application-generation
promotion. Windows does not fall back to in-place replacement. Before the old
host requests normal shutdown, it records the exact promoted/previous pointer,
bootstrap and old installation identity/graph, and target revision/generation/
attempt with its current run. An explicit stop wins over the restart request.
The launcher retains exact old-host and failed-successor Stop identities through
candidate verification and rollback selection. A Stop during cleanup must not
launch another app. An intentional later owner start does not inherit that old
run's Stop. Readiness-timeout cleanup uses a separate, closed private recovery
stop signal; it still asks the exact app to close normally, but cannot impersonate
an owner Stop and thereby suppress the required recovery. Unknown shutdown
continues to refuse both recovery and another launch.
The successor must obtain the existing private-pinned authenticated readiness
acknowledgement. A launch or a listening socket alone is not update success.

If that exact successor exits before acceptance, or fails bounded readiness and
then completes its normal shutdown, the launcher may verify the untouched old
installation and restore only the unchanged pointer it replaced. A changed
target, graph, bootstrap, run or uncertain shutdown refuses recovery. A separate
old-host readiness check must prove the original graph and authenticated data/
process identity before reopening admission. It records `restart-rollback`, not
a fabricated `restart-ack`, retains the failed update, and suppresses automatic
retry of that same revision. Explicit installation or a changed published
revision remains a separate ordinary request. User auto-update preferences are
not silently rewritten. Unknown effects and saved work are not replayed.

The supervised update admission is enabled for the exact owned listening host,
not for foreground launches or merely registered tasks. Bounded native evidence
covers successor acceptance, early-failed successor recovery, the real readiness
deadline followed by normal shutdown/rollback, explicit owner Stop and
stable-bootstrap fresh start. Source review and deterministic portable
interleaving controls cover late Stop during replacement/recovery. A separate
native caller-level one-second Stop observation timed out while the same host
retained custody and later exited naturally; it is not every supervisor
unconfirmed-Stop path. Changed expected birth is an argument-refusal control,
not actual PID reuse. These results do not establish every failure interleaving,
browser trust or sign-in/out.

### Why the background app stopped

When the app host or the launcher ends with an error, it saves one private
record (`config/services/task-failure.json`): the time, the error class, and a
sentence, only when that sentence is fixed text that this product wrote. Any
other error keeps its class name alone, so no path, address or argument text
is copied. The CLI's own exit text is not saved, because it names the data
folder, the address and the port. A busy address gets one fixed sentence.

`service status`, and the Start-menu status entry, add the record to the
not-ready message in one sentence when the task is not running, for example:
"Amplifier's background app stopped on November 14 at 9:05 AM: Another program
is using the address or port that Amplifier saved for this app." A task that
runs without a listening host yet is a host that is starting; an older failure
is not shown there. The record is also not shown after a later host start.

Saving and reading the record never changes a start, a stop or a status
result. Limits: a host that ends without running any of its own code (for
example, it was ended from outside) has only the launcher's general record
with the exit code, and a launcher from before this change leaves none. A
later start that leaves neither a receipt nor a record keeps the earlier
reason visible, with its time.

The human waived the clean-machine check for the current delivery and selected
the existing Windows test host only. That coverage is **waived / not executed**,
not a prerequisite-free installation pass. Signing is deferred separately.

### Launcher refresh

An in-app update never replaces the stable launcher, so launcher code stays at
the version that setup installed. A defect there cannot be repaired by an
update: one measured installation kept a 0.87.0 launcher under a 0.92.5 app,
and that launcher opened a terminal window at every start. The Start-menu
entries run launcher code too.

`amplifier_web.windows_launcher_refresh` replaces the launcher with the version
of the active app generation:

```text
<data>\updates\a\<generation>\tools\amplifier-unified\Scripts\python.exe -I -m amplifier_web.windows_launcher_refresh --data-dir <data> [--check]
```

It is keyed on the owned task, not on setup's files: the task names the
launcher folder and the data folder. It therefore also fits an installation
that setup did not make. `--check` reports the plan and changes nothing.
`--recover` only settles a refresh that did not finish.

**It must run outside the app.** The normal stop ends every process in the
host's Job. The command runs with the active generation's Python (never the
launcher's own), and refuses when it is a descendant of the running host. A
terminal and a remote shell qualify. The app itself starts it through a
second task, which Windows starts outside that Job (see "The launcher follows
the app").

Steps, in order:

1. Decide, changing nothing. Refuse unless the launch record names exactly the
   launcher that the task starts, no update handoff is in flight, the launcher
   is not newer than the app, and the generation still passes its own checks
   (installation identity, package list, release file by digest).
2. Take the refresh lock (one refresh per installation). Save one private
   record, `config/services/launcher-refresh-journal.json`, with everything a
   rollback needs: the launcher path, the planned name of the set-aside
   folder, the old launcher identity, the launch record's bytes, setup's
   receipts, the start-program paths and whether the app was running.
3. Stop the app with the normal owner stop. Decide again with the app stopped.
4. Copy the generation's verified release file beside the installation.
   Confirm that the app is stopped, then rename the launcher folder aside. It
   is kept. Every change in the installation folder waits up to 30 seconds for
   a program that is still closing: a live worker program whose Foundation is
   older than `e37a946b` makes Windows refuse a rename into that folder, and
   the workers end a moment after the task reports that it stopped (measured
   in the clean room; see "Why the app stops first").
5. Install the launcher at the same path with the generation's exact package
   list and the launcher's own interpreter. The task, the shortcuts and the
   private Python do not change.
6. Check the new launcher: its probe, its package list against the
   generation's, and its own reader against the active generation.
7. Confirm that the app is stopped, then bind the launch record to the new
   launcher in one private write (`application_generations.rebind`). The write
   refuses unless the stored identity is exactly the replaced launcher's and
   the folder is the same.
8. For a setup-made installation, rewrite setup's receipt and rerun setup's
   audit with the new launcher.
9. Start the app and require the normal readiness result, with one more wait
   while the task still runs. Mark the saved record as done, then remove it. A
   record that says done is never rolled back, even if it could not be removed.

It does not touch `config/server.yaml`, the port, the TLS files, the trusted
certificate, the task definition, settings, chats or app generations.

**Rollback and recovery.** Any failure after step 2 runs one rollback. It works
only from the saved record and from what is on disk, so it can run again after
it was itself ended:

- One gate decides whether anything is left to put back. It reads only the
  disk: the set-aside folders, setup's receipts, the launcher's identity, the
  launch record's launcher binding and the start programs. The saved record is
  removed only when that gate finds nothing, so a rollback that was ended after
  one of its steps is never taken for finished.
- It stops whatever runs from the new launcher.
- It puts the launch record's launcher binding back first, under the pointer
  lock (`application_generations.restore_binding`). A record that already
  names the previous launcher needs nothing, even if an app update changed the
  rest meanwhile. A record that names another launcher and also changed in
  another way belongs to a later update and is never overwritten. This also
  covers a rebind whose write landed and whose own check then failed.
- It puts back setup's receipts, the previous launcher folder at its original
  path, and the start programs (copies of the launcher's own), and removes
  start programs that only the new launcher added. The set-aside new launcher
  goes last: until then its record is the list of those start programs.
- It then restores the app's running state.

One case has no rollback: the app updated itself after the new launcher was
installed and bound. The result says so, and names the saved record to delete
if the new launcher shall stay. A failure to write the launch record is
reported as such, not as a change by something else.

If the command is ended part-way (a closed window, a lost connection, a
restart of the PC), the saved record remains. The next run of the command, or
`--recover`, runs the same rollback first. The active generation's Python is
never replaced, so the command can always run. The launcher itself never
rebinds the record to itself at start: an unchecked launcher must not claim
the app.

**The result says what is true.** It reports "running" only for an app that
answers; an app that runs but does not answer, an app that is not running, and
a state that could not be read are separate statements. An incomplete rollback
reports `changed: true`, names what could not be restored and where the
previous launcher is, and points to `--recover`.
`config/services/launcher-refresh.json` records the outcome and the failed
step, never output or paths from child programs.

Evidence and limits. The `Windows launcher refresh` workflow installs a
published earlier release (0.93.2) as the launcher and the candidate as the
active generation with real uv installations, in setup's layout. It runs the
command's own code through: a refused installation; an app that does not
become ready (full rollback after the rebind and after setup's audit); a run
that is ended after the real installation and the rebind, with none of its own
rollback, followed by the next run's recovery; a successful refresh; and a
second run. After the refresh the new launcher's own code accepts the active
generation and setup's records, and a later update's rollback check passes
with the rebound record. A runner registers no Scheduled Task: stop, start and
readiness are a recorded stand-in there. The real task on a setup-made
installation, a restart of the PC afterwards, and the installation that setup
did not make are clean-room and owner checks, and are not claimed here.

Known limits. A refresh in the seconds between an update's rollback and the
old host's own confirmation of that rollback is not detected. A person who
starts the app from the Start menu during the refresh meets a missing or
unbound launcher; the refresh then stops with "the app is running" and rolls
back. The launcher's refusal of a launch record that names another launcher
is still silent at start (it writes `task-launcher-error.json`); this command
never leaves that state without saying so, but another cause still would.

A launcher that is older than the running app also answered "not ready" to
its own status question (the Start-menu status entry), because it compared the
host's version with its own. It now expects the app generation that it starts
(`installation_health.started_identity`); the identity check itself is
unchanged. When the shortcut cannot start the app, it now says one cause and
one next step: before the start-at-sign-in task exists the cause is the first
start's download, and the next step is the internet connection and the Start
menu; nothing is installed again.

The refresh does not change the Apps-list version; the app host writes it
(next section). Setup's Repair and Update will call this command and refresh
the private tools; that is separate work.

### The launcher follows the app

After an app update, the app host asks for the launcher refresh by itself.
No person, terminal, console window or elevation is needed. The decision is
app code (`windows_launcher_follow`), so it reaches an installed PC with an
ordinary update; the launcher that the PC has needs no change for it.

**When.** One call in the update owner's loop, after each pass. All of these
must hold:

- This host is the verified update owner of a supervised task, and it runs the
  active app generation.
- The app is newer than the launcher, and the launcher's files differ from the
  app's. `windows_launcher_files.py` lists the package files that a launcher
  process can load (`FILES`) and the distributions that they import
  (`PACKAGES`). The host compares the bytes of each listed file, the installed
  version of each listed distribution, and `entry_points.txt`. A file that one
  side lacks, or that cannot be read, is a difference. Nothing is stored in
  the launcher, so a launcher from before this change is compared the same
  way. Measured at the release tags: a listed file changed in 5 of the last
  20 releases. An update that changes no listed file causes no refresh.
- The active app version is confirmed: the update's own record says that the
  new app started (`acknowledged`) for exactly this generation. A new task
  registration has no such record until the next app update. After an update
  the new host writes that confirmation itself, and it does so after it
  listens. So a record that still says `prepared` for the running generation
  is no answer: the host reads it again at each pass. Every other state of
  the record is read once for one host run.
- Nothing runs: no chat turn, no worker, no update check, stage, installation
  or activation, and the update state is the same as at the pass before.
- "Keep Amplifier up to date" is on, or the person selected the install button
  of the Updates page while that page offered the launcher for this app
  version.
- No counted attempt exists for this app version, or the button was selected.
- No request is open and no refresh is unfinished.

**Who runs it.** The host registers a second task of the same account,
`AmplifierUnifiedLauncher-<SID>` (`windows_launcher_task`), and starts it.
Its action is the refresh command with the active generation's windowless
Python and `--automatic`: "the helper". The task has the same principal as the
main task (the person's own sign-in, least privilege) and the same ownership
rule: its description holds its own specification, and the whole definition
must equal the definition that this specification generates. A task of that
name that fails the check is never changed, and no refresh starts. Its
triggers have no end: at sign-in, every 5 minutes for one day, then once a
day. The host removes the task when the request is settled; the helper removes
it when the account has no owned main task for this data folder any more; the
uninstall of a launcher with this change removes it too.

The helper's working folder is the app generation's `Scripts` folder. That
folder holds a `uv.exe` of its own, because the app depends on a package that
brings one. Windows program lookup tries the current folder before the path,
and on a Windows runner the helper ran that program and not setup's. The
refresh command now turns that lookup off for its process, as the host does
(`NoDefaultCurrentDirectoryInExePath`), and refuses a uv whose path is not
absolute. So the task's own path decides, in the helper and in a person's
terminal.

**Records**, private, in `config\services`: `launcher-refresh-request.json`
(hosts write it: the request, the main task's registration, the launcher's
identity, the target generation, `open` or `closed`, and the hold),
`launcher-refresh.json` (the helper's state and result) and the journal of the
refresh. Every journal names the main task's registration; the journal of an
automatic refresh also names its request.

**The rules.** No clock decides safety; time limits decide only when a side
gives up.

- *Update fence.* While a host knows an open request, `awaiting_restart()` is
  true: no update check, stage, installation, activation or storage cleanup
  starts. The host sets the fence in the same step in which it tested that
  nothing runs. So a launcher request and an app-update hand-over never run
  together, and the helper's Python is not deleted.
- *Hold.* A host names its own run in the request only after it closed
  admission for new work. It does so at "go" (the helper said that it is ready
  for exactly this run, the app is still idle, and the setting or the button
  still allows it), or when it starts while a refresh has already started.
- *Stop.* The helper asks the app to stop only while it holds the refresh
  lock, only the exact run whose hold it read under that lock, and never while
  an update hand-over is in flight. This covers the first stop and every stop
  of a rollback or a recovery.
- *Reopen.* A host clears its hold and admits work again only when it holds
  the refresh lock itself and no stop record names its run.
- *Registration.* A journal that names another registration than the main
  task has now belongs to an installation before Amplifier was installed
  again. Nothing is ever rolled back from it: the manual command refuses and
  names the file; a host or a helper renames it
  (`launcher-refresh-journal-stale-<time>.json`) and goes on.

So a stop of this mechanism cannot reach a host that has work, and with the
setting off no refresh starts. A person's own "Stop Amplifier" is not part of
this: it stops the app at once, as before.

**What brings the app back.** The helper that sees its own failure rolls back
and starts the app in the same process. For that it stops the app only with
the hold of the host that runs then (the stop rule). A host that started on
the new launcher gives that hold before it listens. A host that hangs before
that point gives none: the helper then stops nothing, and each trigger waits
again, until a person selects "Stop Amplifier"; the next trigger then puts
the earlier launcher back. A helper that was ended leaves its
journal and the task; the timed trigger starts the helper again, and it puts
the earlier launcher back from the journal and starts the app. A sign-out, a
restart of the PC or a power loss is settled at the next sign-in. The recovery
always rolls back; it never finishes a refresh forward. A host that waited 10
minutes with admission closed reopens as soon as it has the lock, says that
one part of the update still needs a repair, and tries that repair about once
an hour when no work runs. Until then Amplifier checks for no updates. That
is for a refresh that started. The repair's own "go" needs no setting, so a
host gives it only for such a refresh. A request whose refresh did not start
changed nothing: the host closes it, as one counted attempt. A hold that a
host could not save ends after the same 10 minutes, in one of these two ways.
When the helper finishes before that, its own result closes the request at the
host's next pass.

**Bounded.** One counted automatic attempt for one app version: a refusal, a
check that could not run three times in one host run (the host tries it again
after 5 minutes), a helper that did not become ready, or any settled result
except "withdrawn"
(the app was busy at "go", or the setting was turned off). The next automatic
attempt comes with the next app update. The page's install button tries once
more for each press.

**What a person sees.** The server puts the text into the update status
(`updates.launcherRefresh`, shown through `currentStatus`): before the
restart "Amplifier needs one more restart", or with the setting off "One part
of the update is ready" with the button; then "Amplifier is restarting";
afterwards a sentence with the new version, or "Amplifier is running. One
part of the update did not finish." with the earlier version and the button.
While a request is open its text replaces the health text, because no update
runs then. The texts name the install button as the bundled page labels it for
such a status: "Retry installation". The page also adds its own sentence about
"retained components" there, which does not fit the launcher; a label and a
sentence of its own for this state are web-client work. During the outage the
page shows the web client's own reconnect text. The helper never shows a
message box: a waiting box would hold the task and block the timed trigger.

**The Apps list.** It shows the version of the app that is installed and
confirmed, which is the version that the Updates page shows. The app host
writes it once per run (`windows_consumer_shell.set_version`), only for a
setup-made installation and only after the app version is confirmed. It
requires that the entry record and every value of the key are as setup wrote
them, changes `DisplayVersion` alone in one registry transaction, and puts
the record back when the key step fails. A private note lets the next host
start settle a write that was interrupted. The result of each run is saved in
`updates.launcherRefresh.appsList`. The refresh itself still rewrites only
setup's install receipt. A version that is not of the form `d.d.d` is not
written.

**The kept launcher folder.** An automatic refresh keeps the launcher that it
replaced, as the manual command does. After a later automatic refresh is
settled, the helper deletes the folder that the previous automatic refresh
set aside: only the path in its own record, only a real folder of that name
form beside the installation's `tools` folder, never a path that an
unfinished journal names. Folders from manual runs are never touched. So the
first automatic refresh on a PC deletes nothing.

**Not weakened.** The main task's ownership check, the launcher identity in
the launch record, the generation's own checks and the release file's digest
are unchanged and run in the helper. The host itself never moves, installs or
binds anything: it writes private records, registers and starts one task of
its own account, and writes one registry value. A refresh never changes the
main task or its registration.

Evidence and limits. `windows-launcher-follow.yml` runs on a Windows runner
with a published earlier release (0.105.0) as the launcher in setup's layout
and with the real Task Scheduler, supervisor, host, helper task, helper, uv
and Apps-list key. It requires, with no skipped check: nothing starts before
the app version is confirmed; an app that started while the record still said
`prepared` writes the Apps list after the confirmation comes; a
forced installation failure is rolled back, the app runs on the earlier
launcher and saves the failure; an app that starts afterwards makes no second
attempt; a helper that is ended after the launcher folder was moved aside is
repaired by Task Scheduler's timed trigger with no other action; then the
launcher follows, at the same path and on the same port, and the listed files
do not differ any more; the new launcher starts and uninstalls; a helper task
that was left with no main task removes itself. The fixture writes the records
that an app update leaves, and it writes the confirmation in the place of the
app's own readiness check; the update hand-over has its own workflow.
Measured there (run 44162025757): 75 seconds from the app's request to the
settled rollback of the forced failure; 241 seconds from the end of the helper
to the repair by the timed trigger; 134 seconds from the request to the
settled refresh. Not shown there: the setup file, a standard account, a sign-out or a restart of
the PC, the Updates page in a browser and its button, a message that is sent
while Amplifier restarts, a count of console windows, a helper that is ended
after it started the app, a real update hand-over that the refresh follows,
and an installation that setup did not make.
Portable cases cover the decision, each rule, the helper's cases, the button
and the texts.

Known limits. While the launcher folder is aside, the Start-menu entries do
not work, and nothing on the screen reports a recovery that fails while the
app is down. The "needs a repair" text names no step for the person, because
none is true today: setup does not repair an installation, and `--recover`
needs a terminal. A launcher from before this change cannot remove the helper
task at uninstall; when the folder was deleted too, the task stays and can
start nothing. If Amplifier gets a new task registration while the launcher
folder is still aside, nothing automatic brings the app back.

### Consumer release asset

`scripts/windows_consumer_release.py` is a build-machine producer. The normal
release workflow passes it the original application wheel and an isolated
Windows installation of that wheel. It verifies the installed archive/RECORD
payload and observes the exact canonical Windows graph before embedding the
closed consumer plan in the native WinExe. The publisher verifies the retained
EXE, plan, source/compiler-input hashes and checksums against the same wheel.
It publishes `AmplifierUnifiedSetup-<version>-windows-x64.exe`, its plan/build
receipt and `WINDOWS_SHA256SUMS` through the existing single release owner.
It also publishes the identical bytes as `AmplifierUnifiedSetup-windows-x64.exe`,
so that one link always gives the current setup file:
`https://github.com/microsoft/amplifier-unified/releases/latest/download/AmplifierUnifiedSetup-windows-x64.exe`.
That file is a copy, not another build: it embeds exactly its own release's
plan, the publisher refuses a copy whose bytes differ, and the checksum file
lists both names. A release that is not marked latest (a repair of an older
line) does not move the link. The link depends on one rule: only the
application publisher marks a release as latest. Component releases in this
repository (`scripts/scheduling_release.py`) publish with `make_latest=false`;
a component release marked latest would break the link, because it carries no
setup file. The link gives the current file at download
time; a file that a person saved earlier still installs its own release, and
the installed app then updates itself.

Weighed and not done: a version-independent setup file that reads the latest
release plan at run time. Its bytes would stay the same across releases, which
might build download reputation while the file is unsigned; that effect is not
verified. It would give up the one integrity property that the unsigned file
has: the downloaded bytes fix exactly what is installed, and the published
receipt attests that chain (setup digest, plan digest, wheel digest). Every
saved copy would also follow the moving latest pointer for ever, and an old
copy would have to understand every later plan. A signed plan would need a
signing key in CI and its custody, and an unsigned file's embedded public key
proves little. Two independent reviews gave the same answer. Revisit only with
evidence that reputation attaches to an unchanged unsigned file at this
project's download volume.
No target-side compiler, PowerShell invocation or extra agent publisher is used.
Historical selected sources without the consumer producer retain their original
Python-only asset set.

This asset is explicitly an **unsigned preview**. An executable checksum is not
Authenticode or SmartScreen reputation. Consumer browser entry separately checks
Windows ROOT trust and the private leaf fingerprint before requesting a browser
grant. It never bypasses browser policy or a browser warning. Declining trust
keeps the background app running and sends no browser-entry command. A successful
system TLS check is not proof that every browser's policy accepts the connection.

Exact installed-package checks cover real Start-menu entry, refusal of a live
untrusted listener, certificate-review decline and rendered data-preserving
uninstall. Public artifact availability, a new installation's human trust
decision/authenticated browser onboarding, and real sign-out/sign-in remain
distinct acceptance boundaries; source admission does not label them passed.

### A start that a person can see, and a command's real exit status

Two defects of the launcher from the first clean-room runs, and their repair
(pull request 2270). Both are launcher code: a new installation has the
repair, and an existing installation keeps the launcher of its setup file.

**A command that the active app version takes over.** The launcher hands each
`amplifier-unified` command to the active app generation
(`application_generations.delegate`). It did that with `os.execv`. Windows has
no exec: `os.execv` starts a new process there and ends the calling one at once
with exit status 0. So the caller of a command went on while the command still
ran, and never learned that it failed. Measured in a clean room: a command
"returned after 430 ms with exit code 0; output characters at return: 0", and
its 166 characters came 4 seconds later. Measured on a Windows runner before
the repair (run 44124470533): a caller that waits for a command of a real
launcher with a real active generation has none of its three output lines at
the return, and the launcher's own reader of its commands gets no failure from
a command that ended with status 3. For that reason a failed `service start`
or `config set` of the launcher was silent.

`application_generations.hand_over` is exec where exec exists. On Windows the
launcher starts the app's interpreter, waits for its end and ends with its
exit status:

- The app gets the launcher's own standard handles and its console. A
  launcher process without a console gives the app a console that has no
  window (`CREATE_NO_WINDOW`); Windows would otherwise open a console window
  for it. The rule "no console window" holds in both cases.
- Ctrl+C and Ctrl+Break reach both processes. The app decides what they do.
  The launcher ignores them and reports the app's end.
- A caller that ends the command at a time limit ends the launcher's process
  and not the app's, as before. The launcher's open step still says "may not
  have opened" after its limit, and `open` can still open the browser.
- Two processes exist for the time of a command, where one existed. The task
  that starts the background app does not use this hand-over.

The Start-menu launcher now gets a failure status from its own commands.
`service start` waits up to a minute for the host and ends with a failure
status when the host is not healthy then. The launcher goes on to its status
step, as it did when it could not see that status: the status step decides,
so a host that needs longer keeps its time. `service status` ends with a
failure status when the host is not healthy. The launcher treats that as a
host that is not ready: it first asks whether another program holds the saved
address, and names that; otherwise the start fails with its usual message.
(Read from source: before, a launcher with no active app generation got that
failure status too, and then did not ask about the address.) Every other command that fails
ends the start at its step, as it did for a launcher with no active app
generation: `config set`, `setup-tls`, the preparation, and `service
install`. `service install` registers the task and does not wait for the
host, so a taken address cannot make it fail.

Shown on a Windows runner (lane `windows-local-connection`, a skip fails it):
the complete output and the exit status through the real hand-over, into a
file and into pipes, in a folder with a space; the launcher's reader gets the
failure; no console window with a shared console and with no console (with
and without standard handles), beside a control that has a window;
Ctrl+Break; and the first start with the three launchers that exist on PCs.
Not shown by a runner: the launcher program `amplifier-unified.exe` in front
of the launcher's Python, and a person's terminal. Those are clean-room steps.

**What a person sees during a start.** The Start-menu entry's program has no
window. A start takes seconds; a first start can take minutes. Until the
browser opened, the person saw nothing (about 1 second after Yes to the
certificate question in the clean room; 15 seconds when the app updated itself
first). A second click on the entry in that time started Amplifier a second
time and gave two browser tabs.

- For the time of a start from the Start menu, a small window in the corner
  above the clock says "Amplifier is starting." (`windows_start.sign`). It has
  no button, needs no click and never takes the keyboard focus. It stays in
  front of other windows. While the step that downloads runs, it also says:
  "It downloads the remaining parts of the app from the internet. This can
  take a few minutes." While the launcher shows a message of its own (the
  certificate explanation, a question, a failure), the sign is hidden: the
  person reads one thing at a time. It goes away when the start ends, also
  when the start fails. The window is of a class that Windows provides; there
  is no new dependency and no console.
- One start from the Start menu runs at a time, for one installation in one
  sign-in session (`windows_start.only`, a named lock of this person with the
  rules of the installation's lock). A second start while the first runs does
  nothing and shows nothing: the first one's sign or message is on the screen.
  So no second tab opens. The lock and the sign end before the message "may
  not have opened", because that message sends the person to the Start menu.
  A message of the start step or of the certificate step (the question about
  a taken address, the certificate explanation, "Open Amplifier again ...")
  is shown while the start still runs. It is in front of other windows. A
  second click does nothing until the person has answered it; then the lock
  is free. A lock that Windows does not give never keeps the person from
  Amplifier: the start goes on, and the only loss is a second tab.
- While a start waits for the browser, an uninstall can begin to delete the
  folder. The message is then not "may not have opened" with a next start or
  the setup file: it is the text of a start on a folder whose removal began.
- Limits. Another sign-in session of the same account has its own lock; the
  installation's lock still lets one start step run at a time. `amplifier-unified
  open` from a terminal takes no lock and opens its own tab. The sign does
  not know which process shows the Windows certificate question, so it can
  stay in its corner beside that question. A start at sign-in shows nothing,
  as before: it opens no browser.

Shown on a Windows runner: the real window (its text for each step, its
styles, its place inside the screen without the taskbar, that it is not the
active window, that it hides beside a message of its own program and comes
back, that it is gone after the start), the real lock across two processes
(also one that a program left behind), and the launcher's start with both: a
second start during the first runs no start step, no certificate step and no
`open`. Only a desktop with a person shows: that the sign is noticed, how it
looks, its place beside a real taskbar, and a real second click on the
Start-menu entry.

### Local HTTPS trust without a manual import

A new consumer installation gets a **key-less identity**
(`windows_tls.create_identity`). The launcher makes one root certificate and one
server certificate in one call, in its own process. The root's signing key is
never written and is dropped when that call returns, so no key that can sign
under the root exists afterwards. That is the main protection: a root that
Windows trusts cannot be used to impersonate another site when nobody can sign
with it. The private store holds `ca.crt`, `leaf.crt` and `leaf.key`, and no
`ca.key`.

| Certificate | Shape |
| --- | --- |
| Root | `Amplifier Unified (this PC only)`, path length 0, server-authentication use only, 825 days. Critical name constraints permit only `localhost`, `127.0.0.1`, `::1` and addresses the owner configured, as exact hosts and full-length address masks. The constraints are a second barrier. |
| Server | No subject name. A critical subject alternative name with the same names, so every name lies inside the root's constraints. Valid exactly as long as the root. Its key stays on disk because the host needs it. The computer name is left out: nothing opens the app by that name, and a renamed PC must not need a certificate that cannot be issued. |

`windows_trust.py` owns the Windows step. When the launcher's check finds the
connection untrusted, it:

1. Shows one short message: Windows will ask one question about a certificate
   with this name; Amplifier uses it only for the connection between this PC's
   browser and Amplifier on this PC; select Yes. It shows the thumbprint that
   Windows will show. This is one extra click in the first-run journey.
   Every launcher message is a system-modal message box, which Windows creates
   in front of other windows. The person's last click has usually gone to
   another window by then (about 40 seconds pass after setup ends), and Windows
   keeps an ordinary new window of the launcher behind it: in the first
   clean-room run the message waited behind the browser, with only a taskbar
   button to show for it. The foreground and topmost message-box flags did not
   prevent that, and raising the window afterwards did not either.
2. Adds the root to the person's **Current User** trusted roots with
   `CertAddCertificateContextToStore`. Windows shows its one standard
   confirmation. For a new identity the launcher passes the exact in-memory
   certificate it created, not a copy read back from a file. The confirmation
   follows the person's OK on the launcher's message, so Windows lets it open in
   front. The launcher also asks Windows to bring it forward and never answers
   it; that request has no effect when the person has moved to another window
   in between.
3. Requires that the store really holds the root, then repeats its own check: a
   TLS handshake against the Windows roots must return the pinned server
   certificate. Only then does it request a browser grant.

The check has three answers, not two. A listener that answers and is not
verified is **untrusted**. A listener that does not answer is **unreachable**,
and that says nothing about trust. The host can restart while the two dialogs
are open: a new installation finds a newer release at its first start and
restarts into it. So the launcher waits up to 60 seconds for the listener,
before the step and after the Windows confirmation. If the host is still away,
the message says that Amplifier is restarting and to open it again in a minute.
It never says that the PC does not trust the connection. (In a clean-room run
of 0.94.1 that updated itself to 0.95.0 at first start, the earlier two-answer
check showed the "this PC still does not trust the connection" message two
seconds after Yes, although the root was in the store.)

The browser step waits for the same restart. After trust is established, the
launcher runs `amplifier-unified open` once. Windows hands that command to the
active app generation, which after an update at first start is the new version,
while the earlier host can still serve for some seconds and is then away. `open`
accepts only a host of its own package version. So `open` waits up to 60
seconds for that host (`browser_entry.HOST_WAIT`): it repeats only the health
request, sends one grant request to the first host that matches, and starts the
browser once. A refused or unconfirmed grant request ends the command; it is
never sent again. After the first round of health requests, none starts at or
after the end of the wait; one that started can take 5 seconds and the grant
request 5 more. The typed-code command for another device
(`amplifier-unified browsers code`) uses the same owner request without a
wait: it asks each local address once.

The launcher learns the result from one line of the command's output
(`browser_entry.OPENED`), not from its exit status. With a launcher from a
setup file before pull request 2270, a command that a generation takes over
ends on Windows with exit status 0 for its caller, whatever it did: `os.execv`
starts a new process there and ends the calling one. A launcher from that pull
request on gets the real status (see "A start that a person can see, and a
command's real exit status"). It still reads the line: the line says that the
browser accepted the address, and a status 0 says only that the command ended.
The launcher allows the command 120 seconds: the wait, 10 seconds for the last two
requests, and 50 for the start of the command and the browser call (the check
of the generation that takes the command over has its own limit of 30
seconds). Without the confirmation line the launcher knows only that
the browser was not confirmed: after its limit, a command that a generation
took over still runs and can open the browser. So its one message says that
Amplifier may not have opened in the browser, to look for an Amplifier tab
first, and otherwise to open it from the Start menu: Amplifier Unified. When
the Start-menu entry does not exist, the message names the setup file instead,
by the same two facts as the launcher's failure message. The message does not
say that Amplifier is running; the launcher made no new check of that.
The launcher is the code that setup
installed and an in-app update does not change it, so the line's text is fixed.
For the same reason the wait is in `open`: a launcher from an earlier setup file
gets it from the new generation. Such a launcher allows the command 30 seconds
and reads the exit status; if the restart takes longer, it shows its earlier
message. `open` goes on to the end of its own wait, so the browser can still
open after that message: when the new host answers within the wait and gives
the grant.

The hand-over also keeps each argument whole. The Windows C runtime joins an
`os.execv` list with spaces and quotes nothing, so the installation folder of a
person whose user name has a space would reach the app as two arguments.
From pull request 2169 the launcher quoted each argument of that list on
Windows. From pull request 2270 it starts the app with an argument list that
`subprocess` quotes (`application_generations.hand_over`).

**Limit, not repaired here:** the hand-over is made by the launcher, and an
installation keeps the launcher of its setup file. An installation that a
setup file before pull request 2169 made, in a folder with a space, keeps the
plain list. From its first app update, the `amplifier-unified` commands that are
handed to the app generation do not reach the app. Read from source, for the
two places where a person meets it: the Start-menu entry gets no status from
its `service status` command and shows "Amplifier could not start", and a
first start that updates the app opens no browser. The task that starts the
background app does not use this hand-over (also read from source). Such an
installation needs its launcher replaced (`windows_launcher_refresh`). The
app starts that refresh by itself after an app update that changed a file that
the launcher runs (see "The launcher follows the app"); such an installation
is not run there. Measured on a
Windows runner: a plain list splits an argument with a space, and with an
interpreter under a folder with a space the command does not arrive. Not run:
a real installation of an earlier setup file under such a user name.

(In a clean-room run of 0.98.1 that updated itself to 0.98.4 at first start,
`open` ran in 0.98.4 three seconds before the host restarted, found the 0.98.1
host, and ended after 0.7 seconds with no browser. The launcher received exit
status 0 and showed nothing.)

Outcomes: **No** keeps the app running, says so in one plain message, and the
next open (always started by the person) explains and asks again. A refusal by
Windows, device policy that disables user roots (read first, so no pointless
question appears), or trust that has no effect gives one plain message that
names the PC's security settings and its IT support, and saves the public
certificate in `export/local-ca.cer`. There is no prompt loop, no elevation, no
Local Machine store and no policy change.

What this does **not** protect against: changed launcher code, or any other
program that runs as the same person, can ask Windows for the same confirmation
itself. A later retry reads the root through the owner-only store and adds it
only if it is exactly this shape and issued the pinned server certificate. That
guards against a defective identity, not a hostile one: a certificate cannot
show that nobody kept its key.

Consequences and limits:

- An installation from an earlier setup keeps its identity, which has a signing
  key on disk. Amplifier **never** adds such a root; the person can still review
  it in the Windows certificate viewer as before. Moving it to a key-less
  identity is a later, explicit owner action.
- No key can reissue a key-less identity's server certificate. A new configured
  address makes the host refuse to start, and a missing server certificate
  refuses, both with existing material unchanged. The owner's explicit
  `setup-tls force` replaces the whole identity with a new one for the
  currently configured names (root published last, so an interrupted renewal
  never serves and can be repeated). The next open then shows the Amplifier
  message and the one Windows confirmation for the new root, and every other
  device must import the new root. Run it while the host is stopped. Windows
  can still hold the earlier root; see "Roots that `setup-tls force` replaced"
  below. Renewal before day 825 is this same command; nothing reminds the owner
  yet.
- The step runs in the launcher that setup installs. An in-app update does not
  replace that launcher, so only a setup file built from this change has it.
- `windows_trust.remove_root` is the removal call. Windows asks again there,
  and its default button is No, so "declined" is an ordinary outcome: the root
  stays and is inert.
- The launcher's check uses OpenSSL with the Windows roots. It does not prove
  that a browser accepts the certificate. The `windows-local-connection` job
  of the focused Windows workflow asks the Windows chain builder about the
  shape and about a name outside the constraints, and asks Edge the same two
  questions on a disposable runner (with the test root in the machine store,
  which the product never uses). A person's own answer to the Windows
  confirmation, and Edge on an ordinary PC with the root in the person's own
  store, are clean-room acceptance. The standard-account case (see "A standard
  Windows account") runs this step as a standard user: it records the text of
  the Windows confirmation, answers it in the test, and requires that the
  launcher's check then reads the connection as trusted.
- The retained prototype launcher is unchanged: it keeps the earlier CA with a
  key on disk and the person's own certificate-viewer action.

#### Roots that `setup-tls force` replaced

Seen in clean-room Run G: three uses of `setup-tls force` left three roots
named "Amplifier Unified (this PC only)" in the person's store, and an
uninstall removed only the current one. Nothing knew the earlier roots: the
store can be searched by name, but a name does not show which installation made
a certificate, and the earlier certificate's bytes were gone from disk.

- **The record.** Before `setup-tls force` writes the first file of a new
  key-less identity, it adds the root that it replaces to
  `config/tls/replaced-roots.pem` (`windows_tls._remember_replaced`), under the
  TLS lock and through the private-file writer. The file holds public
  certificates only, oldest first. If the record cannot be written, `force`
  stops and the identity is as it was. Only this command writes the file. A
  `force` that stopped part-way records the root that is still in use; a
  repeated `force` records it once.
- **The record never loses a root that Windows may still hold.** Without its
  exact bytes nothing finds that root again. The record must fit in one
  bounded read (64 KiB, about 50 certificates). When one more does not fit,
  `force` asks Windows, read-only, which recorded roots it still holds for the
  person, and drops the others. Only Windows' own answer "not found"
  (`CRYPT_E_NOT_FOUND`) drops a root. Windows returns nothing also for a
  search that failed, so `windows_trust.contains` raises for every other
  result; a failed search and a store that cannot be opened both count as
  "Windows may hold it". `remove_root` uses the same search (`_search`): a
  failed search is `blocked`, not `absent`. For `blocked` an uninstall says
  that Windows can still hold the certificate: after a failed search the
  program does not know whether Windows holds it, and one text serves every
  case of `blocked`. Only for `declined`, when Windows reported the person's
  No, does it say that Windows kept the certificate. With several certificates
  neither outcome tells which one stays, so the text then says "at least one"
  or "one or more" of their number. These sentences are part of the last
  message of an uninstall that keeps the folder, of the confirmation of an
  uninstall that deletes it, and of the further message for either. An
  uninstall with the delete choice does its certificate step before it writes
  its journal. When a step after that one fails before the confirmation, the
  text of the failed step has no sentence about a certificate. `add_root`
  keeps its plain first search: a failure there leads to the add, which
  Windows decides and the check after it confirms. If the record is still
  full, `force` stops with a plain text and changes nothing
  (`windows_tls.ReplacedRecord`). A record that is too long to read stops
  `force` in the same way: Amplifier never writes one of that length, and it
  does not replace a file that it cannot read. The readers get an empty list
  from such a file. When `force` writes the record again, it writes only the
  whole certificates; other text in the file is not kept.
- **What the person can read.** `setup-tls status` on such a PC prints the
  SHA-1 thumbprint of the root in use (the form that Windows shows) and the
  thumbprint of each replaced root that Windows still holds. When it cannot
  read its certificate, the record or the store, it says so; it never shows
  that as "none". The guide's steps delete by hand only a certificate whose
  thumbprint the command lists as an earlier one. Every other certificate of
  the name stays, also when the command shows nothing: the steps never find a
  certificate by its name, and never by exclusion.
- **What counts.** `windows_trust.replaced` returns a recorded root only when
  it has the fixed shape of this product's root (`_shape`: the name,
  self-signed, path length 0, server-authentication use only, critical name
  constraints with no exclusion), when the store holds exactly that
  certificate, and when it is not the current root. The period of validity and
  the names are not compared: a replaced root can be expired, and it names an
  earlier configuration. The record names candidates; it is no proof. A
  program of the same account can write the file, and it can also ask Windows
  for the same deletion itself. The shape rule keeps a changed record from
  making Amplifier ask about a certificate that is not of this product.
- **At the next open.** The launcher's trust step reads the list before its
  message. With no replaced root the message and the one Windows question are
  as before. With N replaced roots the one message says that Windows asks N+1
  questions, that the deletions come after the person's Yes to the first, and
  gives the thumbprint of each certificate. After Windows added the new root
  (the person's Yes), the launcher asks Windows to delete each replaced root
  (`windows_trust.forget_roots`): one Windows question for each, default No.
  A No to the new root or a refusal asks about no replaced root. When Windows
  already holds the new root it asks nothing, so the message announces no
  deletion and none is asked. The normal case is two questions after one
  `force`: install the new root, delete the earlier one.
- **After a No.** A replaced root that the person keeps is not asked about at
  an ordinary later open: the trust step runs only when the connection is not
  trusted. Nothing saves a No, so the open after the next `force` asks about
  it again, and so does an uninstall. The message lists at most five
  thumbprints of replaced roots and gives the number of the others.
- **At an uninstall.** `windows_trust.held_roots` lists the current root and
  each replaced root that the store holds. Uninstall asks Windows once for
  each (`_trusted_root` and `_forget_root` in `windows_consumer.py`), and its
  first question says how many questions Windows asks. The normal case is one.
  The result is `declined` when the step for at least one certificate ended
  with Windows' report of the person's No. Otherwise it is `blocked` when at
  least one was not confirmed as deleted for another reason: the store could
  not be opened, a search failed, Windows refused a deletion, the store still
  held a certificate afterwards, a check afterwards failed, or the step ended
  with another error, also after such a report. In some of those cases the
  program does not know whether Windows still holds that one.

Limits. The record is written by app code, which an in-app update brings. The
deletion is launcher code: an installation keeps the launcher of its setup file
until a launcher refresh. So a PC with an earlier launcher records a replaced
root and does not delete it; a later launcher deletes it at an uninstall, or at
the open after the next `force`. A root that was replaced before this change is
on no record and stays; nothing tells it from another installation's root of
the same name, so the guide tells the person to leave it. A delete uninstall
removes the record with the folder `app`. No launcher-side code, of any
version, lists the TLS folder, so the new file does not disturb an earlier
launcher (searched in the history of the TLS, trust, setup and launcher
modules). Not shown: the Windows deletion question itself on a person's
own store; a hosted runner cannot answer it. The store calls run there on a
private store that is not a trust store, and the list and its rule run on the
real private file store.

### Saved local address

The consumer installation no longer uses the fixed port 8942. First setup reads
this computer's dynamic TCP range and its excluded ranges
(`netsh int ipv4 show dynamicport tcp` and `show excludedportrange`), and selects
a random free loopback port outside both, from 20000 to 39999 when that block is
available. Hyper-V and WSL reserve blocks inside the dynamic range, and those
blocks move at each restart; some computers set the dynamic range to start at
1024, so the range is read, not assumed. Random selection keeps a second Windows
account on the same PC on a different port even while the first app is stopped.
`amplifier_web/windows_ports.py` owns selection.

Setup saves the port and its `https://127.0.0.1:<port>` origin together, in one
write, in the existing private `config/server.yaml`. A port counts as saved when
that origin is present, or when the port is not the default and app-owned HTTPS
is configured, so an owner who later lists only a remote address keeps it.
Restart, the sign-in task,
an in-app update and every shortcut read that file; nothing selects again. The
browser origin includes the port, so a changed port means a new site for an
installed app window and its unsent drafts. An installation that already saved a
port (including 8942 from an earlier setup) keeps it and its other settings when
its task is registered again.

A test bind filters candidates. It is not ownership: the host's own listener
claims the port when it starts. Before a later start, and after a start that did
not become ready, the launcher classifies the saved port:

- **Another program listens there.** The launcher never adopts or stops it.
- **Windows refuses the bind with access denied (10013) and nothing listens.**
  A Windows feature reserved the port. There is no program to close, and the
  message says so.

Both cases offer the same repair, only on the person's confirmation: select and
save a new port while this installation's host is stopped. Origins that named
the old port move with it. The message states the cost: chats, settings and
accounts stay; an installed app window must be installed again; typed but unsent
messages are not carried over. The certificate names no port, so its trust is
unchanged. A start without a browser (`--no-open`) receives the refusal and
shows no dialog. This installation's own recorded host is never treated as
another program.

Limits: the retained prototype launcher keeps 8942. Two installations under one
Windows account share the browser's cookies for `127.0.0.1`, because cookies are
not scoped by port; signing in to one signs the other out. The focused Windows
workflow's `windows-local-connection` job checks selection against the runner's
real ranges, a real listening first installation, a real Windows port
reservation and a real listed exclusion.
Two real setups on one PC and the port across a real update remain clean-room
acceptance.

Windows app preparation uses a compact `updates/a/<generation>` directory.
Qualification and pointers retain the full source revision and attempt. The
older `updates/applications/<revision>/<generation>` candidates remain in place
and readable; ambiguous locations refuse rather than merging evidence. The
bootstrap preflight must prove it resolves the same folder before promotion.
This addresses Windows DLL loading's filename-length refusal, which can occur
even after a successful Git checkout and Python package installation. It changes
no registry long-path policy, package origins, source pins or user data roots.

### Private Git and the agent's shell

The agent's shell tool needs `bash.exe` and core utilities. MinGit has neither:
on a PC without developer tools every shell call failed, and on a PC with WSL
the tool took System32's `bash.exe`, which only starts WSL. Consumer setup
therefore acquires the complete official **Git for Windows 2.56.0.windows.4**
(`Git-2.56.0.4-64-bit.tar.bz2`, about 119 MB, about 425 MB unpacked) in place of
MinGit. `scripts/windows_private_git.py` owns its pinned publisher digest and
its unpacker. The retained prototype entry and `install.ps1` keep MinGit.

The complete distribution has no ZIP, and the in-box .NET Framework reads no
tar.bz2. Setup verifies the archive's SHA-256, installs its private Python, and
runs the embedded standard-library unpacker with `-I -S`. No other tool is
acquired. The unpacker creates every entry exclusively below setup's private
directory and refuses unsafe names, repeated entries, special files and any link
other than the archive's five known runtime links. It writes those as the MSYS2
runtime's own system-file links, never as reparse points that Windows follows,
and writes archive hard links as separate copies. Git's own license comes from
the exact tagged source commit by digest. Setup then starts the private
`bin\bash.exe` and requires that its `git` is this installation's Git, by
location and pinned version, so a shell that cannot start stops setup instead
of failing in a first chat. Setup children do not inherit `BASH_ENV` or exported
shell functions. Setup records the result in `private-git.json` and removes the
unpacked archive.

Each host start puts this installation's `git\bin` first on PATH; workers
inherit it. The order is derived at start, not only read from the saved task
definition. Each Windows host start also sets `NoDefaultCurrentDirectoryInExePath`.
Python's program lookup on Windows otherwise tries the current folder first, and
the shell tool's folder is a workspace: a file named `bash` in a cloned
repository would have been chosen and run. The shell tool's own `AMPLIFIER_BASH_WINDOWS_SHELL=wsl` choice still
selects WSL for a person who wants it. An installation made by an earlier setup
file keeps MinGit, and nothing is added to its PATH, until setup refreshes its
private tools; an in-app update does not replace them.

The `Windows private shell` workflow runs on a native runner with a new PC's
PATH (Windows folders only). It unpacks the pinned archive with the same
isolated invocation, checks the runtime links and the app's own file reader,
and requires the real shell tool to resolve the private bash and run `echo`, a
pipe, `git --version` and a file write, with no model request. A decoy
`bash.cmd` in the workspace must be selected without the guard and never with
it. The unpacker runs with a uv-managed CPython, as in setup. It also compiles
consumer setup with the embedded pin and unpacker. It does not run setup:
setup refuses administrator and server accounts. The real setup file on a clean
ordinary account, the task's actual PATH, and windows during shell use remain
clean-room acceptance and are not claimed here.

### One installation folder and plain stops

Consumer setup installs into one fixed folder, `%LOCALAPPDATA%\Amplifier`.
Earlier setup files created a new `amplifier-install-<32 hex>` folder at every
run. That name cost 50 characters, and the app repeats the installation path
inside its own data paths. Windows refuses a path of 260 characters or more
(248 for a folder) unless long paths are enabled, which is not the default. With
the long name no setup-made installation could chat on a default PC.

The longest supported installation folder path is **59 characters**
(`MaxRootLength` in `scripts/windows_bootstrap_native.cs`). The fixed folder is
33 characters plus the account's profile-folder name, so a name of up to 26
characters fits. The path-length work in the app owns this number. It measured
the whole tree below the folder in a clean-room installation: 59 is the root
that its design supports under both of its variants, and it may raise the
number in its own change when its budget test proves a larger one. Worst
observed depths below the folder: frozen worker environment 194 characters,
uv cache 189, first worker environment 187, app generation 171, private Python
142, tools 124, private Git 90. Those fit at 59 today: the longest is 253
characters, with 6 to spare. The numbers are from a clean-room installation of
release 0.105.0 after an in-app update and a chat, and the budget test holds
the same numbers. The frozen worker environment
(`app\runtime\q-<32 hex digits>`) is the one that an update or the setup of
a chat makes. Its deepest file is bytecode; its deepest source file is 180.
While the app prepares such an environment, and after a preparation that was
stopped or that failed, the same files are in
`app\runtime\prepare-<32 hex digits>`. That name is 6 characters longer than
the frozen one. Its deepest source file is then 186 (245 at 59). A bytecode
file as deep as the deepest one of the frozen environment is 200 there: 259
at 59, the limit itself. Python uses the source file when it cannot write
bytecode.
Earlier text here gave 162 for the first worker environment (its deepest
source file) and 68 for the private Git (the earlier, smaller Git).

The short folder is necessary and not sufficient. The app must also keep what
it adds below the folder inside the budget of the next section. In a new
installation every row of that budget fits at 59; the next section also names
the cases that the budget does not cover. The tightest rows are in the
source store (see the source-store items below): module source is 253
characters at 59, and the deepest other file that counts as needed is 255.
They fit only because the store folder is named `s`. A change to
the folder name must respect this number, and
`tests/test_windows_setup_folder.py` fails when code and document disagree.
Setup stops before it asks anything when the path would be longer.

### Path budget below the installation folder

With long paths off Windows accepts a file path of at most 259 characters and
creates a folder of at most 247. `tests/test_windows_path_budget.py` is the one
table of what each store adds below the installation folder. It computes every
path shape for the longest supported folder path and reads that number from
setup. For the session, capture and ownership stores it reads the temporary and
sibling names from their owners, and it fails when a sum no longer fits. For the
source store it uses measured repository depths, which are constants in the
test: it does not notice a deeper file in a later bundle revision. Print the
table with `python tests/test_windows_path_budget.py`.

- **The app's own chat folders use a short project name on Windows.** The shared
  CLI name of `<folder>\app\chats\<chat id>\files` is the folder path plus 53
  characters, so the folder path appears twice in every session path. Release
  0.94.1 failed its first chat on this: a temporary file beside the saved view
  was 277 characters with an 18-character account name. The name is now
  `chat-<16 hex digits>` (21 characters). The rule and its limits are in the
  [history contract](../workspaces/contracts/history.v1.md#what-it-is).
- **Root sessions fit in the session, capture and ownership stores** at 59: the
  longest path is 245.
- **Worker (sub-agent) sessions have longer IDs** (34 characters plus the agent
  name). At 59 every path shape holds an ID of 50 characters. Longer IDs lose
  files in this order: the request observation (50, root sessions only), the
  oversized-request diagnostic copy (51; the app records "unavailable" and the
  turn continues), background-job records (68), the saved view (93). A shorter
  folder path adds the same number of characters to each limit.
- **A folder that a person chose** keeps the CLI name, which is as long as the
  folder's own path. For the files that keeping a chat needs (its history, its
  saved view and its capture), the installation folder path plus the path of
  that folder may be at most 137 characters. So the folder may have a path of
  78 characters at 59, and of 103 at 34. When it is longer, the app answers
  before it makes anything, when a chat is created and when a conversation
  worker starts: "This chat's folder has a path that is too long for Windows:
  `<folder>` (`<length>` characters; at most `<number>` fit here). Move the
  folder to a shorter path, or start a chat in another folder." Before, the
  app could not create the chat ("unexpected response (500)"), or the worker
  stopped at its first write. The app gives this answer only while Windows
  long paths are off. It reads that setting when it answers, because a
  conversation worker is a new process and gets the setting of that moment.
  The app itself keeps the setting that it started with. After long paths are
  turned on, the app's own writes for such a chat can still fail until the app
  starts again. After long paths are turned off, the app answers for a folder
  that it could still write itself; a new worker could not.
  Two kinds of files have less room than a kept chat: background-job records
  (112 in place of 137) and request diagnostics (94). The default workspace
  folder (`<folder>\workspace`) is the folder path plus 10 characters. A chat
  there is always kept. Its background-job records fit only up to a folder
  path of 51, and its request diagnostics up to 42.
  A helper (sub-agent) session has less room too. Its ID is 34 characters plus
  the agent's name, and its history is saved under the same project name as
  the chat's. With the longest folder that the app accepts, the history of a
  helper fits up to an agent name of 15 characters. In the default workspace
  folder it fits up to a name of 24 characters at a folder path of 59, of 40
  at 51 and of 74 at 34. In an app chat folder it fits up to 72 at 59. The
  app's answer measures the chat and not a helper. This is arithmetic from
  the names. What a person sees when the history of a helper does not fit has
  not been run.
- **Not covered:** an installation made by an earlier setup file (folder
  path of 74 characters plus the account name) has no budget; new chats there
  get the short name, which is necessary and not always sufficient. A chat
  there with saved history under the CLI name stays under it, including history
  longer than the limit that was written while long paths were on. The app
  finds such history through the extended-length path form and does not start
  a second history for that chat.
- **Update stage:** an in-app component update, and a setup run on a PC that
  already has an AI connection, prepare the components in
  `<folder>\app\updates\releases\<32 hex digits>` (folder path + 54). The
  compatibility probe runs with that stage as its app home and
  `<stage>\shared-config` as its shared home, and it mounts a worker in
  `<stage>\qualification-workspace`. So the stage path is inside every session
  path of the probe. The CLI name of that folder is the folder path plus 78
  characters; under it the probe's session store folder is twice the folder
  path plus 165, which Windows creates only up to a folder path of 41. No
  consumer installation has one (the shortest is 34, and a session folder
  below it fits only up to 22). Release 0.98.5 failed the component stage of
  its first in-app update on this (258 characters at a folder path of 51; the
  page showed "ENOENT · FileNotFoundError · Probe: prepare"). On Windows, a
  process that runs with a stage as its app home and that stage's
  `shared-config` as its shared home now gives every folder the project name
  `update-<first 12 hex digits of the stage ID>` (19 characters). With any
  other shared home every folder keeps its name, so the stage's name is never
  used where a person's history is.
  At 59 the longest path that a stage and its probe create is 248, with
  11 characters to spare; the probe's session folder is 202. The probe's
  capture files go to the installation's capture folder, as before, under the
  same name (folder 154 at 59). Each stage has its own shared home, so two
  stages never share a session folder. Two session-store
  shapes do not fit in a stage (the saved view, 261, and a background-job
  record, 286); a probe writes neither. The native case checks the complete
  list of what the real probe leaves with its test profile (one offline
  profile, no default behaviors). The profiles of a real installation are not
  checked there; a whole in-app update on a clean PC is the check for them.
  A first installation without an
  AI connection has no stage: `<folder>\app\updates\baseline-runtime` holds
  receipts only (longest path 118).
- **A chat with its own setup** (a saved plan, which a fork has; a bundle
  that is not offered; dependency overrides or local code in its folder) is
  prepared and checked in a stage too (`runtime_profiles.ensure`). The probe
  then gets the chat's folder as its workspace. Its session is not a saved
  conversation: it is in the stage's own shared home. Every folder has the
  stage's name there, so the probe's paths are the rows above, whatever the
  chat's folder is: an app chat folder, the default workspace folder, a folder
  that a person chose. The preparation itself adds only shorter paths (155 at
  59). Before this rule a folder that a person chose kept the CLI name inside
  the stage. For the default workspace folder the probe's session folder was
  twice the folder path plus 134 (252 at 59), and its history write fitted
  only up to a folder path of 39. For another folder the length of its own
  path decided: at most 30 characters at 59, 38 at 51 and 55 at 34. The
  native job shows that failure with the app's real probe at 51 and 59, under
  the names from before this rule. A run of the app's whole preparation at 59
  without the rule is on a branch that is not merged (November 17, 2026):
  every step up to the frozen worker environment ended well, and then the
  check ended with "FileNotFoundError" (ENOENT). Earlier versions refused every chat with
  its own setup on Windows before they made a stage, and the chat page showed
  only "The conversation worker could not start."
- **A provider's own state files:** the Anthropic component keeps two small
  files in the shared home, and it writes each one under a temporary name
  first (`amplifier_web/provider_state_files.py`). The longest of those paths
  is the folder path plus 41 (100 at 59); in the shared home of an update
  stage it is the folder path plus 102 (161 at 59).
- **Source store:** Amplifier Foundation owns its layout; the app supplies the
  folder: `<folder>\s` in a new installation, `<folder>\source-store` in an
  installation that an earlier setup file made. A Foundation revision before `7526bb55`
  cannot stage the default bundles at any supported folder path (measured at
  51; arithmetic for the others), so the conversation worker cannot start on a
  default PC. From `7526bb55` the store
  reaches every staged file itself through extended-length paths, and new
  entries on Windows have 16-digit folder names. Repository depth then no
  longer limits staging, sealing, cleanup or the copy into a build view.
- **Source store, the tightest row:** other programs still open store files by
  ordinary paths. Python imports module source from the store object: folder
  path + 69 (`\s\objects\<16>\<40>\`) + the path inside the repository. The
  deepest module file of the default bundles is 125 characters (measured), so
  the path is folder path + 194: **253 at 59**, with 6 characters to spare.
  The measured depth is a number in the budget test, not a live check: a
  default module more than 6 characters deeper fails on the longest accepted
  account name until the budget changes. An entry that already exists under a
  64-digit name stays in use and is 48 characters longer.
- **Source store, the files beyond the limit:** the store stages whole
  repositories, with their Git data and their tests. Measured on native
  Windows with long paths off at a folder path of 59, after the app prepared
  the default components for two chats: in the run of November 17, 2026,
  4 files and 2 folders pass the limit. The job prints these counts and the
  size of the whole tree at each run; a later revision of a component can
  change them. All six are test
  fixtures of one module of the context-intelligence repository
  (`modules\tool-context-intelligence-upload\tests\fixtures\ground_truth\<ID>\context-intelligence`,
  for two IDs): `metadata.json` is 268 characters, `events.jsonl` is 267, and
  the folder is 254. Their depth in the repository is 140. At a folder path of
  51 the two `metadata.json` files are 260, as a clean-room installation
  showed. The store writes all of them through extended-length paths, and
  every step of the preparation ends well. An ordinary path does not open the
  4 files. No bundle file, module source, context file or skill is among
  them, and no chat reads them. The copy that a build reads by ordinary paths
  is in the build view of the module and is 42 characters shorter (226). The
  deepest files that count as needed are two documents below `docs\lanes`,
  one of them in the skills repository (depth 127): 255 at 59, with 4
  characters to spare. They count as needed because nothing shows that no
  chat opens them. The
  native job lists each path over the limit. It fails when one is anywhere
  else than in the Git data or the tests of a staged repository, in the tests
  of one of its modules, or in bytecode (`NOT_NEEDED` in the budget test). A
  folder that is only named `tests` somewhere else does not count. The layout
  of the store is unchanged.
- **Source store, the earlier folder name:** earlier setup files named the
  folder `source-store`, 11 characters longer. There module source is folder
  path + 205. It fits up to a folder path of **54** and is 264 at 59. Those
  setup files accepted an account whose profile-folder name has 22 to 26
  characters, and that account cannot load the deepest default module. This is
  arithmetic from measured depths plus the native case below; it has not been
  run on such an account. An existing installation keeps its store where it
  is: the launcher uses `source-store` when that folder exists and `s`
  otherwise (`source_store` in `amplifier_web/windows_consumer.py`). Nothing
  is moved, renamed or downloaded again, by a second setup run, by an update
  or by the launcher refresh. So an existing installation also keeps its limit
  of 54. Nothing renames its store folder, and a second setup run on a
  finished installation does not give it the short name.
  The earlier name wins when both folders exist, because an earlier launcher
  and the recorded sign-in task use it, and one installation must not have two
  stores. The cost: a `source-store` folder that something adds to a new
  installation sends its store to the long name. Setup, the app, an update and
  the launcher refresh do not create that folder. A `source-store` entry that
  exists and fails the private-folder check (a link, another owner) stops the
  start; the other name never replaces it.

`.github/workflows/windows-path-length.yml` is the native check. It turns
`LongPathsEnabled` off on its own runner, proves in a new process that a
260-character path is refused, runs the real session and capture stores under a
folder path of exactly 59 characters, and restores the runner's value. It
installs the locked dependencies, and the lock selects Foundation `e37a946b`.
Its source-store case stages a repository as deep as the default bundles
through Foundation's real store. It requires that the store seals, builds and
cleans up files beyond the limit, and that Windows accepts or refuses each
ordinary path exactly as the budget table says. In the folder `s` the deepest
module file opens at a folder path of 59 (253 characters). In the earlier
folder `source-store` it is refused at 59 (264) and opens at 54 (259). The
same job requires that an installation with each folder name resolves its own
store through the real private-folder checks. Its update-stage cases make a
stage with the updater's layout, at folder paths of 51 and 59 (through the
app's private-folder creator, as setup does; the updater itself uses an
ordinary `mkdir`, which the probe's private-folder check refused on the
runner and accepted on a clean PC, where the probe of release 0.98.5 passed
that check and failed later, at the session store). They require that the
session store's folder is refused under the CLI name and that the app's real
compatibility probe then ends with the record that the Updates page showed.
They require that the same probe, unchanged, completes under the short name,
with modules from the locked test dependencies and no download, and that what
it leaves with that test profile is exactly the list in
`tests/update_stage_probe.py`. That is the
probe in a stage, not a whole in-app update: no component is fetched or
installed. It is not setup-file or clean-account acceptance.
The same job runs that probe with the folder of a chat as its workspace: the
default workspace folder, a person's folder with a path of 150 characters, and
an app chat folder. With the earlier names the probe fails for the first two.
With the stage's name it completes for all three and writes nothing into the
chat's folder. The job also checks the app's answer for a folder that a person
chose against Windows, one character apart: at 59 a folder path of 78
characters keeps a chat, and at 79 Windows refuses the saved view.

A second job, `windows-chat-setup`, runs the whole preparation of a chat's own
setup, unchanged (`runtime_profiles.ensure`): real uv, the worker
dependencies, the default components from their repositories, the freeze and
both probes. It runs below a folder path of 59 with the folders and settings
that the installed launcher gives the app, for the default workspace folder
and for a person's folder. Each of the two folders declares a dependency
override of its own. Two chats with the same setup start at the same time:
one prepares the setup, and the other waits and uses it. One preparation is
cancelled while its first command runs. That is what a stop of the chat does:
`RuntimeManager.stop` cancels the preparation's task. The
job requires that the attempt is recorded as cancelled, that nothing admits
it, and that its copy of the keys file is gone after the last process of the
stopped preparation. The stop removes the copy. When the copy is still there
after the stop, the job makes the same removal itself after that last process
and requires that it works; it prints which of the two it saw. The job does
not show why a copy was still there (Windows refuses the removal while a
program has the file open). The job starts the
chat again directly after the stop. The second start gets a new stage and
ends well. Whether a program of the stopped command still ran at that moment
is not required; the job prints what it saw. It reads the list of processes
every two seconds until it finds no process of the stopped preparation. That
list is a sample: a program whose parent started and ended between two
readings is not in it. In the runs of November 17, 2026, the list held
programs of the stopped command (Python and Git) directly after the stop and
none some seconds later. The stopped stage and its project stay on the disk;
nothing removes them. Only the stage's copy of the keys file goes: at the stop,
or at a later idle pass when the stop could not remove it (`docs/UPDATES.md`).

What a stop ends is checked with a command that the test controls
(`tests/test_windows_stopped_command.py`, in the same job). The command
starts a program through a parent that ends at once, so the program has no
living parent. The test holds an open handle of that program, and Windows
signals the handle when the program ends; no list of processes is used. A
stop and a time limit both go through `updates.process`. Each ends the
command that the app started: the command has ended when the app's call
returns. The app does not end the program that the command started: 3
seconds later that program still runs. The launcher puts the app into a
kill-on-close Job before it starts any child (`contain_current_host` in
`amplifier_web/windows_jobs.py`, called for the supervised sign-in task in
`amplifier_web/windows_service.py`). The third case runs that same call and
the same stop in a process of its own, and then lets that process end:
Windows ends the program with it. The fourth case is the control: the same
process without that call ends, and the program still runs 10 seconds later.
So a program of a stopped preparation can run on while the app runs, and the
Job ends it with the app at the latest. That the installed launcher makes
the call is read from the code; these cases run the call, not the installed
launcher. The first two cases run without the launcher's Job, as the big
preparation case does. The
job reads each prepared stage's copy of the keys file through the app's private-file
reader, which refuses another owner and a wider access list. It then lists
every path below the installation
folder (the list is an artifact of the run) and requires that no path which
the app or a chat needs passes the limit. That tree holds the worker
environments, the stages, the source store with the default components, and
uv's cache. It does not hold what the setup file installs (the launcher
environment, the private Python, the private Git), and the job starts no
conversation worker and calls no model. A hosted runner's account is
elevated, so the case makes the account the default owner of the files that
its process creates, as on a person's PC. The job prepares a chat's own setup
from dependency overrides. A saved plan, which a fork has, goes the same way
through the preparation and is not run there.

Setup checks the PC and the folder before its first question. Each stop names
one cause and one next step in plain language, and never shows exception text,
child output or network values:

| Found | Result |
| --- | --- |
| Setup was started with "Run as administrator" | Stop: close, open the file again with a double-click |
| The sign-in always runs as administrator (User Account Control off) | Stop: use a standard sign-in or turn User Account Control on |
| Not Windows 11 on a 64-bit Intel or AMD processor | Stop |
| A folder above the installation folder lets other accounts change it | Stop: names that folder. The storage check is unchanged |
| Less than 4 GB free | Stop |
| The folder exists and setup did not create it | Stop. Setup never changes it |
| The folder holds a finished installation | "Amplifier is already installed." Yes adds missing Start-menu and Apps-list entries again, then opens it. No app files are installed again or removed. The launcher and its code stay the ones that the earlier setup file installed |
| The folder holds setup's own unfinished attempt, or is empty | Setup removes it and installs |
| The folder has setup's first marker and app data, but no completion record | Stop. Setup never removes it |
| Another installation already starts at sign-in for this account | Stop: remove it first. One sign-in task exists per account |
| A sign-in entry remains for an installation whose files were deleted | Stop: exact steps to delete that entry in Task Scheduler |
| Task Scheduler cannot be read | Stop: restart Windows |

Setup removes or starts only what it made, and marker files alone do not prove
that. An existing folder is first pinned and checked by the storage owner with
the checks of every private folder that setup creates: the person is its owner,
no other account has access, it is not a link, and every folder above it passes
the existing ancestor checks. `InstallStorage` gains only that create-nothing
form and a create-nothing check of the folders above a new folder. A folder
that fails is "not setup's": it is never removed and never started.

Inside a checked folder two marker files make the states exact.
`unified-setup-started.json` is the first file setup writes.
`unified-setup-complete.json` is written after the installed-package audit and
before the Start-menu step; setup then deletes the first marker. A finished
installation therefore never carries the mark of unfinished work, and a lost
completion record makes the folder "not setup's", not removable. As a second
guard, a folder whose app data folders hold anything is never "unfinished".

Removal has one path. It pins the folder and the folders above it, classifies
the folder again at that moment, copies the small private log files to a new
private folder that the storage owner creates exclusively in the person's
temporary folder, and removes the contents while the pins hold. It uses
extended-length paths, clears read-only attributes, removes a link without
entering its target, and deletes the marker last, so a removal that stops
part-way leaves a folder that the next run still recognizes. A failed run
removes the folder that it created unless the completion record exists; it
deletes nothing that it did not create. Opening an installation pins the
folder, the folders above it and the program file while Windows starts it.
One setup runs per Windows account at a time.

Setup reads the sign-in task and changes no task. Only "not found" counts as
absence.

Downloads retry twice in the same exclusive private file after a dropped
connection. A refused, oversized or checksum-failed file is not retried. The
progress window names each step in plain words and says which steps take
minutes.

Not in this change: Repair and Update on a second run, refresh of the launcher
and private tools, removal of program files at uninstall, removal of a leftover
sign-in entry, the setup file's icon and version information, and a restart of
an elevated setup without elevation. A person who removed Amplifier through the
Apps list keeps the folder; a later setup run restores its Start-menu and
Apps-list entries, then starts that installation again.
An installation in an earlier long folder is not moved or read: setup asks the
person to remove it and then installs into the short folder. The real setup
file on a clean ordinary account remains clean-room acceptance. The native
`windows-setup-folder` check compiles the actual sources and exercises, on a
Windows runner, folder states, a planted marker in a folder that other accounts
can change, a link in place of the folder, removal, the held installation, the
named folder above, elevation detection and the stop text. It shows no setup
window and installs nothing.

### A private file is read while another process replaces it

Amplifier's processes replace a private state file with a rename of a complete
temporary file over its name (`publish_private(replace=True)` in
`amplifier_web/windows_files.py`: `FileRenameInfoEx`, replace with POSIX
semantics). A reader that opened the earlier file keeps a valid handle, but
that object has no name any more. Measured on a Windows runner for such an
object: Windows reports 0 links, and its final path is a name below
`C:\$Extend\$Deleted`. Its ACL and its complete earlier content stay.

The reader's validation read both answers as a refusal of the named file:
"reparse point, hardlink, or wrong type" for 0 links, and "alternate
namespace/case alias" for the other path. "Stop Amplifier" reads
`task-runtime.json` every 0.1 seconds while the app writes its phases
`stopping` and `stopped`. So a person could see "Amplifier could not stop its
background app ... Reference: stop-app, PermissionError" although the app
stopped (clean room, November 16, 2026). On a hosted runner 99 of 3000 reads
failed against 302 replacements by another process.

The rule:

- An object that has no name when it is validated is `PrivateFileUnlinked`. It
  is not a hardlink, not an alias, and it is never accepted. The reader closes
  it, checks the pinned folders again, opens the name again and validates
  everything again. The limits are 8 attempts for one name, and no further
  attempt begins after 2 seconds. The time limit is tested as the last step
  before each further attempt, after the wait and the pin check. It decides
  only whether an attempt begins: the first attempt has no time test, and the
  time of the Windows calls of an attempt is not limited. The reader asks for
  waits of at most 0.162 seconds in total; the system can make a wait longer.
- A file that another process replaces during each of those attempts gives
  `PrivateFileUnsettled`. Its text says that the file did not settle.
- A read returns the complete earlier file (validated before the replacement)
  or the complete new file, never a mix.
- Every other refusal stays and is not tried again: 2 or more links, a reparse
  point, a wrong type, another path or spelling of an object that still has a
  name, foreign ownership, a DACL that grants foreign access, another volume.
  File sharing is unchanged.
- A publication whose object loses its name after the successful rename (a
  later publication or a removal) is not reported as a failed publication.
  Only the name check is left out for that object; its ACL check stays.
  Nothing is known or accepted there about a file that has the name now.

A reader that meets a removal (not a replacement) finds no file. While another
handle still keeps the removed file, Windows can refuse the new open with
"Access is denied" until that handle closes (measured on a Windows runner).
Windows gives that answer to each open of such a name, with or without this
change. The reopening meets it once and does not try again. The native case
`test_file_removed_while_a_third_handle_keeps_it` prints which answer a runner
gives. The reopening blocks its thread for the waits and for
one pin check per further attempt.

Both new errors are `PermissionError` subclasses, so each earlier handler of a
refusal still catches them. A handle that passed validation can lose its name
later; it still holds the complete file that was validated. So the reset
reader (`_read_reset_configuration` in `amplifier_web/recovery.py`) takes the
size from that validation and does not query the name a third time. Nobody but
the file's owner, SYSTEM and administrators can replace or remove a private
file: its ACL and its folder's ACL grant no other account any access.

### Repair of a source store object that holds bytecode

On Windows a helper of 0.106.5 or earlier wrote `__pycache__\*.pyc` into an
object of the installation's source store (the connection check of a
connection whose module comes from a source: "OpenAI Compatible API", Azure
OpenAI, Ollama, vLLM). Foundation refuses such an object from then on
("Shared source contents changed"): no chat with that connection starts, a
component update fails while the connection exists, and the Updates page keeps
"A source could not be checked" after the connection is removed. 0.106.6 stops
new bytecode (`source_imports.prepare`). `source_store_repair.at_host_start`
makes an object that already has such files equal to its commit again.

**What it deletes.** A file only when all of these hold: it is a plain file
directly in a folder named `__pycache__`; its name ends with `.pyc`; it is
below an object of this installation's own store; Git lists it as untracked in
that object; and the object has no changed tracked file and no other untracked
entry. One exception, the one that Foundation's own check makes:
`.amplifier_cache_meta.json` at the object's top, which Foundation writes into
every object. The step never deletes that file. Then it deletes that
`__pycache__` folder when this run took a file from it and it is empty. It
deletes nothing else. Any doubt leaves the object exactly as it is: a link of
any kind that the listing finds in the object or below its `.git` (Git does
not run then), a Git error or timeout, a name that Git gives and the listing
did not have. A link that appears after the listing can be read by Git; the
deletion still opens each folder of a file's path and the file as themselves
and checks each on its handle, so nothing behind such a link is deleted.

**How.** It lists and deletes with the native steps of `windows_removal`
(`_Native`: a name from the listing of an open handle, an entry opened
relative to its parent's handle as itself, a deletion through the handle). It
imports that class and does not copy it;
`tests/test_source_store_repair_native.py` pins each step that it uses. Git
decides with the two commands of Foundation's `clean_checkout`, with
`--git-dir` and `--work-tree` fixed, every `GIT_*` name out of the
environment, `core.fsmonitor=false`, and for both commands
`--no-optional-locks` (Foundation gives it to `git status`): with it
`git status` leaves out its optional write of the object's index. The
folders of the object's path stay open while Git runs. What is deleted is
checked again on handles.

**When.** Only on Windows, only in the host that the start-at-sign-in task
started, only on the store of the installation that the host belongs to, in
`cli._serve` before the host takes its own lock. It takes the data folder's
host lock alone, without a wait; every host holds that lock shared while it
serves. This host has started no worker, helper or update stage yet (only the
holder of its hidden console, which reads nothing of the store). A worker or a
helper of an earlier host ends with that host (a kill-on-close Job), and the
task's launcher starts the next host only when the earlier host's process tree
is empty. One process outside that Job can be alive at this moment: the helper
that replaces the launcher after an app update (`windows_launcher_refresh`)
starts the task and waits for this host. It installs the launcher's own
packages and does not read or write the source store. It waits 180 seconds
for the host, more than this step's bounds.

**Stated limits.** Three cases are not excluded. Each needs a host that a
person started with a terminal command on this installation: a child of such a
host that has no Job of its own (an update command, a transfer check, a
sign-in helper) and outlived it; such a host with another data folder that
names this store; such a host in the moment between the end of its lock and
the end of its process. A process of the current version in them only reads:
a `.pyc` that it has open stays, and Python reads the `.py` where a `.pyc` is
gone. A process of an app version before 0.106.6 in them can write new
bytecode; that object is then refused again until the next app version.
Nothing outside the rule above is deleted in any of them. The repair keeps no
list of running programs.

One more limit is Git's own. The step does not turn off Git's configuration.
Among the files that Git reads are the system configuration of its
installation, the person's global configuration and the object's
`.git\config`, each with the files that it includes. The code of
Foundation's check does not turn it off either; the environment of its
process can change what Git reads (see below). The step tells Git to start
no file system monitor (`core.fsmonitor=false`), because such a program can
stay after the command. With `--no-optional-locks`, `git status` leaves out
its optional write of the object's index. These two settings are no promise
that Git only reads. Three examples follow. Each was seen with Git 2.43.0 on
Linux and these settings. Git writes a trace file that its configuration
names (`trace2.eventTarget`). `git status` starts a content filter.
`git status` starts a second Git in a submodule.

A content filter starts like this. An attribute file that Git reads for the
object can give a tracked file a filter by its name. Git's configuration can
name a program for that filter (Git LFS is the usual one). Then `git status`
can start that program for a file whose content it must compare. Among the
attribute sources that Git reads for an object are a `.gitattributes` file
in a folder of the object, the object's `.git\info\attributes`, the
person's global attribute file and the system attribute file of the Git
installation. So a filter needs no attribute file in a module. The four
provider modules had no `.gitattributes` file and no `.gitmodules` file
when this was written (November 17, 2026); that says nothing about the
other attribute sources on a PC. A committed attribute file alone names no
program.

For an object with an ordinary `.git` folder, Foundation's `clean_checkout`
also runs `git status` in the object. The step itself starts at most one
`git status` command for each try of an object. The second command
(`ls-files --others`) starts no filter. The step can still start a program
that Foundation's check does not start, or start none where that check
starts one. The step removes every `GIT_*` name from Git's environment.
Foundation's check starts Git with the environment of its process as it is.
Some of those names change what Git reads. `GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_SYSTEM` and `GIT_CONFIG_NOSYSTEM` name or drop a configuration
file. `GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_<n>` and `GIT_CONFIG_VALUE_<n>`
adds configuration values. `GIT_ATTR_NOSYSTEM` drops the system attribute
file. `GIT_INDEX_FILE` names another index. A person's environment can have
such a name. The app also gives such names to child processes that it starts
(`reset.repository_environment`). That function can add configuration values
through `GIT_CONFIG_COUNT`. With anonymous GitHub access it names the null
device as the global configuration file and as the system configuration
file. The two can also run different Git programs, and each Git installation
has its own system configuration file and system attribute file. What a
program that Git starts does is not bound by the rule of what the step
deletes.

**Cost and record.** `<data>\updates\source-store-repair.json` holds the app
version, whether the pass is complete, a change mark, the tries of each object
that was left, and the counts and milliseconds of the last run. It holds no
path and no time of day. A run writes the record before it looks at the
store; a record that cannot be written means that the store is not looked at.
After a complete pass a start of the same app version reads that file and does
nothing more. The first start of an app version lists every folder of every
object outside `.git` and starts no Git on a store with no `__pycache__`
folder. An object that has such a folder costs more: the step lists every
folder below its `.git` for links and starts at most two Git commands. One run has
these bounds: 20,000 listings, 400,000 entries, 3 seconds of search, 5 seconds
for one Git command, 8 seconds in all (ending a Git that passed its limit can
add one second).

No native step of a run begins after those 8 seconds: every listing, open,
look at a kind and deletion goes through one wrapper (`_Bounded`) that checks
the limit first. The look for Git into each folder of PATH, each Git command
and the write of a change mark have the same check, and a Git command is
started only when half a second is left. The check of the folders above the store does not begin after the
limit either. So after an operation that took the rest of the time, whether it
worked or failed, the run only gives its handles back, writes the record a
last time and writes its one line into the log. One operation has no time
limit of its own. The limit counts from the start of a run: before it the step
reads its record, looks for setup's receipt, takes the lock, reads the record
again and loads the native steps, each once (the second read is under the
lock, so a run never writes back a change mark that another host's run has
replaced); after it the step gives the lock back. At its limit
the step ends the Git program that it started and stops waiting; the installation's
`git\cmd\git.exe` is Git's starter program, so the Git program behind it can
run on until its read ends. The step does not limit what that program
writes or starts. The step deletes nothing in that object.

A bound reached ends the run and a later start goes on. The object in which a
run reaches a bound gets one try when it is the first object of that run, also
when the listings before it used a part of the time; so one large object
cannot hold the pass at itself. An object that could not be decided (a file in
use, a Git failure) gets one try too. An object gets at most three tries for
one app version. One app version gets at most ten runs, also when a run is cut
before its end (the count is written first). A source folder that cannot be
opened leaves the pass open; the other sources are still looked at, and its
objects keep their tries.

Every run that finds a file to delete writes a new change mark, 32 random
hexadecimal digits, before its first deletion; a mark that cannot be written
means no deletion. The mark is an input of the update check's scan signature
(`update_inventory_index.signature`), so the check after a repair asks
Foundation again and the saved "could not be checked" row goes. The mark is
random and not a count: after a record that was lost or could not be read, a
new run cannot write a mark that an earlier check saw. Such a record itself
gives no mark, as before the first repair; the update check keeps the rows of
one signature only, so it scans again then too. One sequence keeps saved rows:
the record is lost, a check saves rows, a run repairs, and the record is lost
again before the next check. The check's own rule ends that after 4 hours.
The step raises no error into the host's start; a stop of the process passes
through it. One line with the counts and the milliseconds goes to the host's
private log (`config\services\task-output.log`).

Not shown on a PC when this was written: the Updates page texts after a
repair, whether a chat's saved discovery failure goes without "Refresh this
connection", and the cost on a cold disk. A hosted runner cannot make the
state through the product, because a new installation takes the newest app
release before a person can connect anything.

### Uninstall stops the app before it removes an entry

Uninstall runs from the Apps list or from "Uninstall Amplifier" in the Start
menu. Both start the stable launcher (`windows_consumer --uninstall`). A person
must never be left with a running app and no entry to open, stop or remove it.
So the order is:

1. One question. For a folder without setup's completion record it has two
   buttons, and No changes nothing. For a folder that setup completed it also
   offers to delete the folder; see "Uninstall can delete the installation
   folder" below. This order is the path that keeps the folder.
2. If the running app reports that a chat is working, a second question names
   that. Yes stops the work through the app's normal stop. No keeps Amplifier
   installed. The launcher asks the app over the same pinned private HTTPS
   connection that browser entry uses. The app counts conversations with the
   update gate's own two predicates (conversation work, background-worker
   work). An app that does not answer, or an older app that reports no count,
   gets no second question.
3. Check and pin every Start-menu shortcut and the Apps-list entry. A changed
   entry stops here, before the app is touched. Another program can have a
   shortcut open at that moment (on hosted runners `explorer.exe` had a new
   shortcut open for a moment). Windows then refuses the pin with its error 32
   ("The process cannot access the file because it is being used by another
   process."), and uninstall tries again before it stops here: at most 50
   more times for this step, with a wait of a tenth of a second before each.
   The waits are 5 seconds in all; the Windows calls of each attempt take
   their own time. Each attempt reads the shortcut's bytes again, so a
   shortcut that changed meanwhile stops here too.
4. Stop the app normally and remove its sign-in task. There is no forced stop.
5. Remove the Apps-list entry, then the shortcuts ("Uninstall Amplifier" last),
   then the emptied Start-menu folder. A delete that Windows refuses with
   its error 5 ("Access is denied.") is tried again with the same limit, counted for this
   step alone. A mapped view of the shortcut gives that refusal for as long as
   the view exists. Hosted runners showed the refusal for a moment; which
   program caused it there is not known. A shortcut that is still refused
   then stops the uninstall with "Uninstall Amplifier" still in the Start
   menu. Another uninstall can finish when the shortcut is no longer held. A
   shortcut with a read-only attribute gets the same refusal each time, and
   no wait ends it.
6. If Windows trusts this installation's own key-less certificate for this
   person, ask Windows to delete it, and each root that `setup-tls force`
   replaced and Windows still holds (`windows_trust.forget_roots`): one
   Windows question for each, normally one in all. Windows itself asks the
   person; its default answer is No. The first question announces this, with
   the number when it is more than one. Every outcome finishes the uninstall:
   a certificate that stays has no signing key anywhere and names only this
   PC. There is no second attempt, no elevation and no Local Machine store. An
   identity from an earlier setup added nothing and is not touched.
7. One message says that uninstall is complete. When the result of step 6 is
   `declined` (Windows reported the person's No for at least one certificate),
   it says that Windows kept the certificate. When it is `blocked` (at least
   one was not confirmed as deleted for another reason), it says that Windows
   can still hold it. With several certificates it gives their number and says
   "at least one" or "one or more", because no outcome tells which one stays.

A step that fails leaves the entries of every later step in place and shows
one message with the cause, the next action and a short reference (step name,
error type and Windows error code, never the error text). The same step and
error are saved in the private file `consumer-maintenance.json` in
`app\config\services`, beside the app's other service records, or in the
installation folder when that folder cannot be written.

Why the app stops first. The earlier order removed the entries, rewrote
setup's entry record `consumer-shell.json` in the installation folder, and
only then stopped the app. While a conversation worker was alive, Windows
refused the private write of a new or replaced file directly in the
installation folder (sharing
violation, error 32); a worker stays alive after its chat turn ends. So after
any chat the rewrite failed, the app was never asked to stop, and it kept
running and starting at sign-in with no entry left. Two clean-room runs showed
this, and a probe in the second one measured the refusal: it is present with
live workers and gone after the app's normal stop. `app\config\services`
accepts writes in the same state.

The cause is Amplifier Foundation's session ownership. A live worker holds it
below `<folder>\ownership`, and Foundation pins every folder from the drive
root down to the session's own folder. Before Foundation `e37a946b` no pin
shared write access. So Windows refused a rename, a move or a replacement of
an entry directly in each pinned folder, also in the folders above the
installation folder: `AppData\Local`, `AppData` and the profile folder
(measured). An ordinary new file was not refused. The app's private writer
publishes a file by a rename, so it failed. From `e37a946b` a pin on a folder
above `ownership` shares read and write, and other programs can rename,
replace and move entries there (measured in the clean room with three live
workers at `e37a946b`). These refusals remain while a worker lives,
by intent: a pinned folder itself cannot be renamed, moved or deleted, and
nothing can be renamed or moved into `ownership` or a pinned folder below it.
Foundation's history operations hold the folder that they work in (below
`shared\projects`) in the same way while one operation runs.
`tests/test_windows_live_worker_folders.py` holds a real ownership in
setup's layout on a Windows runner and requires both halves; it runs in the
`windows-path-length.yml` job, which installs the locked Foundation. The
order above stays. A worker runs the Foundation that its own environment
selected, not the one in the root `uv.lock`; that revision moves with a
component update, and a running worker keeps the revision that it started
with. The app's own directory reads (`windows_read_files`) hold a folder
without write sharing while a read runs; they are unchanged.

Uninstall can be repeated. An entry that an earlier, interrupted uninstall
already removed is skipped; a task that is already gone is not an error.

The last step rewrites the entry record with the status `removed`; a later
setup run reads that status to restore the app entries. Worker programs end a
moment after the task reports that it stopped, so this write waits up to 15
seconds for them. A write that is still refused does not fail a finished
uninstall, because every entry is already gone; it is saved as a failed
`receipt` step.

After an uninstall, a second setup run adds the Start-menu and Apps-list
entries again (the entry record with the status `removed` permits exactly
that), then starts the launcher and waits for it. Setup's window with the
current step stays open during both steps. Chats and settings stay in place.
If the entries cannot be added, setup says so and still starts the app, as it
did before this step existed; the next step it names is to run the setup file
again. While setup adds the entries and starts the launcher, its window does
not close, as during a first installation. Once the launcher runs, the window
says that the person can close it; the launcher goes on. Setup's result is 0
only when the entries exist and setup saw the launcher finish; a window that
the person closed earlier leaves it at 1. The launcher skips preparation when
`runtime status` reports a prepared runtime, just as a start at sign-in uses
that runtime. Without this check, a connected AI account made the start run a
complete first installation: minutes without a window, a new folder on each
attempt, and in the observed case a failure. The launcher's failure message
has two forms: the preparation of the remaining parts failed, or the start
failed (every other step). A third text replaces both when the start ended
because the launcher did not confirm its start-at-sign-in task (see "The task
after a computer rename" below). It names the Start menu as the next step only when
the entry record says `installed` and the entry that opens Amplifier exists in
the folder that the record names; setup's second run decides by the same two
facts. Otherwise the next step is to run the setup file again. The record
alone is not enough: the last write of an uninstall can be refused after every
entry is gone. For the same reason the entries can be added again over a
record that still says `installed`, when the folder that the record names is
absent.

The entries are added again when no shortcut of this installation and no
Apps-list entry is there. The Start-menu folder itself can be there. It stays
after an uninstall when the person put a file into it (uninstall removes only
an empty folder), and when another program still had a shortcut open in a way
that let the delete pass. Setup's step uses that folder again when both hold:

- It is this account's own private folder: a real folder and no link, owned
  by this account, with access rules that name only this account, SYSTEM, the
  Administrators group and TrustedInstaller. This is the check that the first
  installation and every uninstall make.
- No entry directly in it has the name of one of the four shortcuts. Such an
  entry counts whatever it is (a file, a folder, a link), and the names are
  compared without letter case.

Every other entry in that folder is the person's. Setup's step does not open,
change or remove it, and writes the four shortcuts beside it. The step
replaces and takes over nothing that it finds: it looks before its first
write, the writer of a shortcut refuses a name that exists (also one that
appeared after the look), and the Apps-list entry is a key that the step
itself creates new. One moment is not covered, and a first installation has
the same moment: the writer makes each shortcut as a new, empty file, and the
Windows shell then saves the shortcut to that file by its path. A program of
this account that puts another file at that path between the two gets that
file written over.

In every other case the step refuses with its record unchanged: an entry with
a shortcut's name in the folder, an Apps-list entry, a link or a file at the
folder's path, a folder that fails the check, a path that Windows does not
let the step look at. A partly damaged set whose record still says
`installed` (a person deleted one shortcut) is refused by that record, as
before. Setup then says that it could not add the entries, and starts the app.

This rule replaced an earlier one, under which the entries were added again
only when the Start-menu folder was gone too. A folder that stayed then kept
every later setup run from adding them, and the person had no entry to open,
stop or uninstall Amplifier. One state is still refused: a record that says
`installed` (its last write was refused) beside a folder that stayed.

The native cases in the `windows-local-connection` job run the step on the
real Start-menu folder. After an uninstall that left a person's file there,
the four shortcuts and the Apps-list entry are added beside the file. Each
refusal of the paragraph before the last has a case there, with one
exception: the path that Windows does not let the step look at has a case
with a stand-in. Each case finds the folder, the record and the Apps list as
they were. The folder that fails the check is, in these cases, one that the
group Everyone can change. No hosted runner runs the setup program itself,
which refuses Windows Server.

`.github/workflows/windows-start-again.yml` runs this path on a Windows
runner with the product's own commands in setup's order: entries, first
start, an AI account configured without a key, the product's uninstall
function (called in the test's process, with its one question answered No:
uninstall and keep the folder), entries again, second start. The test's
folder holds setup's completion record, written where setup writes it, so the
uninstall asks the question of an installed PC (see "Uninstall can delete the
installation folder"); the test never answers Yes to it. Task Scheduler, the
host that the task starts and the
runtime preparation are real there. It requires that `runtime status` reports
a prepared runtime with the account configured, and that the second start
succeeds with a new host process on the same port. It also requires that a
marker file in the app data and the configured account are unchanged, and
that no folder under `app\updates\releases` was added or replaced: a
preparation with an account creates such a folder first. That case does not
use the setup file or a clean
ordinary account (the runner's account is elevated). The launcher runs there
with a console Python and `--no-open`, so the certificate question and the
browser are not part of it. It has no chat, and no sign-out and sign-in: the
task starts because it was registered, not because a person signed in. The
standard-account case (see "A standard Windows account") runs the same
commands as a standard user in that user's own profile, with the certificate
step, a sign-out and a new sign-in.

A runner does not show the state after a chat: a chat installs further parts
into the same runtime. That state was shown once on a real installation
(clean-room run in Windows Sandbox, setup 0.98.6, long paths off, November 15,
2026): after setup, a connected account, one chat and an uninstall from
Settings > Apps, `runtime status` reported a prepared runtime, and the setup
file's second run brought Amplifier back with its entries, its task, the same
port, the earlier chat and the account. No folder under
`app\updates\releases` was added. Not shown anywhere: a start at sign-in
after this.

`runtime status` reads the installed parts with Git. Without Git on its
`PATH` it answers "not prepared" for a runtime that is prepared (seen in the
same clean-room run, from a plain shell). The launcher always runs it with
the installation's own tool path, which contains the installation's Git. A
caller with another `PATH` gets the wrong answer, and a start would then
prepare as before. When the status is negative or cannot be read, the start
prepares as before.

A second native check runs setup's second-run window and its two steps with a
stand-in program in place of the installation's Python (exact arguments, the
wait, the exit codes, a window that closes by itself, and a window that is
closed on request while the launcher still runs). It does not show the
question before the window, a message, or the real launcher.

Not changed, each stated as a limit:

- When the runtime is not prepared and an AI account is connected, a start
  without a task still runs the complete first installation. A failed attempt
  leaves its folder under `app\updates\releases` (failed candidates are
  retained by design), and the next start begins again; it does not continue
  the failed attempt. After an uninstall this path is avoided only when
  `runtime status` reports a prepared runtime.
- The limits of a preparation stay as they were: 1800 seconds for the whole
  preparation and 900 seconds for each of its steps. No shorter limit exists.
  The failure that was observed was not a time limit. Read from its saved
  progress record: after about four minutes of other work, four parallel
  checks started and all four ended as failed within the same second. The
  saved failure record names a cancellation, which is what the other three
  record when one of them fails.
- A start from the Start menu has no window while it prepares. Only a start
  from the setup file shows setup's window.
- An installation that an earlier setup file made keeps that setup file's
  launcher. A second run of a newer setup file starts that launcher and asks
  it to add the entries; it does not replace it. That launcher refuses to add
  the entries again and prepares as before. Such an installation gets this
  change only from a new installation.

The Start-menu commands Stop and Status use the same form of message: what
did not happen, the next action and the reference. They no longer show the
general text "could not complete this operation".

The launcher is the code that setup installed; an in-app update does not
replace it. A new installation gets this behavior from its setup file. An
earlier installation gets it only when its launcher is refreshed. The count
in step 2 comes from the running app, which in-app updates do replace.

Unless the person chooses to delete the folder (next section), uninstall
keeps the program files and the person's data. A changed shortcut or Apps-list
entry still makes uninstall refuse. The Apps list still shows the version that
setup installed.
The native check in the `windows-local-connection` job uses the real
shortcuts, the real registry transaction, private files and NTFS sharing on a
Windows runner. It holds the throwaway installation folder open without write
sharing to get the same refusal, and it uses a stand-in for Task Scheduler,
because a runner cannot register the sign-in task. Two cases hold a shortcut
as another program can (an open that does not share deleting, and a mapped
view) and check the wait and its end. A real stop with a live
conversation worker remains clean-room acceptance.

### Uninstall can delete the installation folder

Deleting a person's folder cannot be undone. So uninstall deletes only what
belongs to this installation, only after the person chose that twice, and in
an order in which every interruption is safe. The order and its reasons are in
`windows_uninstall.remove`; the link-safe walk and the step after exit are in
`windows_removal`.

**The questions.** The delete choice is offered only when setup's private
completion record `unified-setup-complete.json` is in the folder.

- Question 1 has three buttons; the default is No. No uninstalls and keeps the
  whole folder, exactly as before. Cancel changes nothing. Yes leads to
  question 2.
- Question 2 has the warning icon; the default is No. It names the folder and
  says that chats, settings, saved accounts and stored keys are deleted and
  that this cannot be undone. No changes nothing: Amplifier stays installed.
- Then the question about a working chat, as before.

**The order.** Steps 1 to 4 delete nothing. A step that fails there writes
only the private note of the failure, as an uninstall that keeps the folder
does.

1. One start or removal at a time for one installation: a named mutex whose
   name comes from the folder path, in the name space that all sign-in
   sessions of this PC share. A mutex has no count, and only the thread that
   took it can release it. It is created as this person's own, with no
   access for another account, and one that another account made is refused.
   So another account that is signed in at the same time can make Amplifier
   refuse to open or uninstall while its program runs, and can never hold or
   release the lock. Opening Amplifier takes it around
   its start step, and an uninstall that keeps the folder takes it too. When
   it is taken, the second window says so and changes nothing. An uninstall
   that keeps the folder looks for the journal again under the lock: if
   another window began to delete the folder after the question, it refuses
   and leaves the Windows entries for the second uninstall.
2. The process leaves the folder (its current folder was the installation's
   workspace) and stops saving compiled modules.
3. Prove and pin the folder. Proof: the command runs from this folder's own
   launcher (`installation`), setup's completion record is read through the
   private-file boundary, and the folder is opened once and checked on that
   handle (a real folder, exact spelling, this person's own, no access for
   another account). The handle stays open and shares read and write but not
   delete, so no program can rename or replace the folder meanwhile. A
   top-level entry whose name differs from one of this installation's names
   only by case (`App` for `app`) refuses here. Windows opens it under the
   installation's name, but it is not the entry that the installation made.
   Uninstall then deletes nothing, because it could not truthfully say that
   the person's data is gone.
4. Check and pin the Windows entries; stop the app; remove its sign-in task;
   ask Windows about each certificate. A failure up to here leaves the
   installation whole, with the messages of an uninstall that keeps the folder.
5. Write the journal `unified-uninstall.json` (`schema` 1, `choice`, a random
   `token`) with the private atomic writer, then look whether it is in the
   folder. Nothing is deleted before it exists.
6. Delete setup's mark of unfinished work. Setup can leave that mark in a
   finished installation; with the mark and without the completion record, a
   later setup run would treat a folder without data as its own unfinished
   work and empty it. Setup's completion record stays for as long as anything
   is left in the folder: with it, setup installs and removes nothing there.
   Only the step after exit deletes the record, immediately before the
   journal. Uninstall never writes either file.
7. Delete the person's data: `shared` (stored keys) first, then `app`,
   `capture`, `ownership`, `import`, `workspace`, `export`. A closing program
   gets up to 15 seconds. An emptied folder that Windows still keeps (the
   launcher's start program has `workspace` as its current folder) is not
   data. If anything of the data stays, uninstall stops here. The Windows
   entries and the launcher are whole, so the person can uninstall again.
8. Delete caches and program files, then remove the Apps-list entry and the
   shortcuts, then delete what the launcher's own check needs (`git`, `uv`,
   the receipts). So a second uninstall can still start for as long as an
   entry for it exists.
9. One message. It says that the data is deleted, which the removal observed,
   and that Amplifier starts to remove its last program files when the person
   closes the message. When the last listing of the folder showed entries that
   are not Amplifier's, the message names up to five of them, with the number
   of the others when there are more, and says that the folder stays. When
   that listing showed none, the message says that the person can delete the
   folder if it still exists a minute later. When the removal has no such
   listing, because the listing or the deletion directly before it stopped
   with an error, the message says that the folder can still exist afterwards.
   If Windows does not show that message, the uninstall stops there, before
   its last step. So it does not delete `python`, `tools`, the journal or
   setup's completion record, and it tries to write a private note of that
   failure. The uninstall program then tries once more to show a message. That
   message says that the data is deleted and that the last program files were
   not removed. It is never the message of an uninstall that changed nothing.
   The same holds for the last message of an uninstall that keeps the folder:
   the further message is that last message again. When Windows does not show
   the further message either, the person gets no message of the uninstall
   program after its questions.
10. The last step (`windows_uninstall.finish`), under the lock and with the
    folder proven and pinned again: create the flat folder
    `unified-uninstall-last` and two kept files, one in the installation
    folder and one in the flat folder, through handles; start the step after
    exit, which inherits both files open; delete `python` and `tools` through
    handles, and move the few files that this running program has mapped
    into the flat folder. Then the process ends.

**Only exact names.** Uninstall deletes a top-level entry only when its name
is, with its exact case, one that setup, the launcher or the launcher refresh
creates (`windows_uninstall.sort`), or has one of a few exact forms (the
release file, a launcher folder that the refresh kept, the private writer's
temporary file, setup's notes about a failed helper). Names come from the
listing of the pinned handle. Everything else is not Amplifier's: it stays,
and then the folder stays. A test reads setup's source and fails when setup
creates a top-level name that this list does not know. The journal is in no
delete list.

The source store has one of two names (see the path budget above). An
earlier setup file named it `source-store`. From release 0.98.5 setup names
it `s`.

- `source-store` is a program folder by its name in every installation, as
  before. The launcher uses a folder of that name as its store whenever one
  exists.
- `s` is a program folder only in an installation that setup made with that
  name. Uninstall reads this from setup's own private completion record:
  `version` there is the release of the setup file
  (`windows_uninstall.short_store`). A release from 0.98.5 answers yes. Every
  other answer is no: an earlier release, a record without a release in the
  form of three numbers, a record that cannot be read.

"Delete everything" removes the installation's store with the caches, and
"keep" leaves it. In an installation that an earlier setup file made, a
folder `s` is not Amplifier's. It stays with everything in it, the message
names it, and then the folder stays. That holds also when something removed
that installation's `source-store`, and for a second uninstall after an
interruption: only setup writes its completion record, an update and the
launcher refresh do not change it (read in their source), and uninstall
keeps it until the step after exit.

A removal decides this once, under the lock and with the folder pinned,
before it deletes anything, and its last step gets that answer handed on. So
a record that changes during a removal does not change what that removal
deletes.

Another spelling of an own name refuses, as for `App`: `Source-Store` in
every installation, and `S` in an installation whose store is `s`. In an
earlier installation `S` is only one more entry that is not Amplifier's.

What this rule does not cover, stated:

- A program of this account can rewrite setup's completion record (a private
  file of this person) so that it names a release from 0.98.5. After the two
  Yes answers, a folder `s` in that earlier installation then goes with the
  program files. Such a program can delete that folder itself. This is the
  known limit G1 below.
- A `source-store` folder that something added to a new installation is
  deleted by its name, as before this change. The launcher uses such a
  folder as the store from its next start.
- A setup file that was built from this source with a release number below
  0.98.5 makes a folder `s` that uninstall treats as not Amplifier's: it
  stays, and the folder stays. No public setup file is like that: the
  release names the setup file's release from the app release that it
  installs (read in `scripts/windows_consumer_release.py` and setup's
  check of its plan), and release 0.98.5 is the first whose setup source
  names the store `s` (read at the tags `v0.98.4` and `v0.98.5`).

**While the journal exists** the folder can be partly deleted. Open, Stop and
Status refuse with one message that tells the person to select Uninstall
Amplifier. Only these launcher commands look at the journal; a program file
that a person starts by hand from the folder does not. A second uninstall
reads the journal through the private-file boundary in place of setup's
record. The journal is not proof of the person's choice (a program of this
account can write it), so the person chooses again, twice. Then the whole
order runs again, including the stop, with a new token. A journal that is damaged, or
that records another choice, proves nothing: uninstall then offers only to
keep the folder.

A journal can appear while an uninstall asks its questions, for example when
another window began a removal while a question waited. (A program of this
account can also write a file with that name. Every look takes an entry with
that name for a journal.) An uninstall that keeps the folder and found no
journal before its questions looks again under the lock, and refuses when that
look finds one (step 1 of the order). It looks once more when one of its
steps, from the lock to the removal of the Windows entries, stops with an
error before those steps gave their result. When that look finds a journal,
the failed step shows a text of a removal that did not finish, and the
uninstall tries to write the failure note into the installation folder itself,
not beside the app's service records. Three failures of a keeping uninstall
get no new look: a refused write of setup's entry record (the entries are gone
by then), an error when the removal afterwards lets go of the entries or of
the lock, and a last message that Windows does not show. For them the
uninstall tries to write the note where the look before the questions chose.
An uninstall with the delete choice has no such refusal: a journal that
appeared does not stop it, as it does not stop a second uninstall. Its own
checks can still stop it. It looks for the journal's name when a step before
the write of its own journal stops with an error, and directly before that
write. If the first of these looks finds a journal, the failed step shows a
text of a removal that did not finish, not the step's own text. If the second
finds one, a failure of that write shows the text of a removal that did not
finish too. A lock that another window holds is a refusal, not such an error:
neither uninstall looks then. An uninstall that found no journal before its
questions also looks when Windows does not show one of them; that look chooses
only the place of the failure note. So does the look at a failure that no step
and no question names, and at a preparation that fails after the check of the
installation files. Of the looks that this paragraph names, only the one under
the lock decides a refusal, as it did before the others were added. The others
choose only a text and the place of the failure note. They change nothing that
an uninstall deletes or removes. "The place of the failure note" is the folder
into which the uninstall first tries to write that note. The write is only
tried: when Windows refuses it, there can be no note.

Each of these looks, and the launcher's look before it opens Amplifier, asks
only whether the journal's name is in the folder. A look that Windows does not
answer counts as "no journal". Where this section says that the launcher
refuses, or does not open Amplifier, while the journal exists, that is the
condition: its look finds the journal. So no text of a failed step of an
uninstall says that Amplifier does not open. The texts of a removal that did
not finish (`removal` and `data` in `windows_consumer.py`, and the opening for
a start-at-sign-in entry that is not confirmed during a removal) tell the
person not to open Amplifier, because its folder can be partly deleted or
incomplete. The text of the launcher's own refusal is not changed: it says
that Amplifier does not open until the removal has finished, and the program
that shows it has just found the journal. The first question of a second
uninstall is not changed either: its last sentence says the same, and the
program that asks it has just read the journal. An uninstall's own look has
the same condition. An uninstall looks twice before its questions. The first
look (`maintenance`) decides only whether the uninstall prepares the
environment of a start, which checks folders that a removal deletes. The
second (`_uninstall`) chooses the questions, and "the look before its
questions" in this document is that one. Where this document says what an
uninstall does with a journal in the folder (a second uninstall; L4), that
look has found the journal's name, and the uninstall has then read the file
through the private-file boundary. A second uninstall is one whose read gave a
journal that records the delete choice. L4 is one whose read gave anything
else. When that look finds no journal, the uninstall asks the questions of a
folder without a journal: when it reads a valid completion record of setup, it
offers both choices, and otherwise only to keep the folder.

Where this document says that an uninstall shows a text or a message, or says
something to the person, the uninstall program asks Windows to show that text.
Windows can fail to show it. Three kinds of message can come after an
uninstall did its work: the last message of an uninstall that keeps the
folder; the confirmation of step 9; and, when the last step stops with an
error, the message that the last program files could not be removed. When
Windows does not show one of them, the program tries once more. The further
text is the same text again with a reference line, except after the
confirmation: step 9 has that text. When Windows does not show the further
text either, the person gets no further message. The text of an uninstall that
stops (for example a step that failed, a question that Windows did not show, a
refusal) gets one attempt: when Windows does not show it, the person sees no
text of that stop. The uninstall program shows these texts only when its
process has no standard output. That is the uninstall that a person starts
from the Start menu or the Apps list: those entries start the windowless
Python. With a standard output (a console Python; the automated native cases
start the uninstall so) the uninstall asks the same questions and prints its
result. It then shows no last message and no text of a stop.

**No link is followed.** In the process, every entry is opened relative to
its parent's handle as the entry itself, judged on its own handle and deleted
through it. A link of any kind is removed as a link. No attribute is changed.
While the walk holds an entry, the entry does not share delete, so no program
can move it out of the folder before it is deleted. An entry that a program
renames only by case after the check counts as still there, from the same
listing that looks for its exact name: uninstall stops, never deletes setup's
completion record past it, and never says that the data is deleted. Until the
entry has its own name again, a second uninstall refuses at its first check;
data that was deleted before the renamed entry stays deleted.

**The step after exit.** A running program cannot delete the files that it
runs from (its `.exe`, a loaded `.dll` or `.pyd`). It can move them. So the
uninstall program deletes `python` and `tools` itself, through handles, and
moves those few files, through their handles, into one flat folder that it
created and holds. Only they are left for a step after exit: one static
Windows PowerShell 5.1 script, found in the system folder, with the folder
path and the token as JSON on standard input, outside the launcher's Job,
with a temporary folder outside the installation.

The step works with paths, so it deletes a file only while its path cannot
lead anywhere else. It walks no tree. One fact carries this: a folder cannot
be replaced while it holds a file that a program keeps open without any
sharing. Nobody can delete or move that file, so the folder is never empty;
Windows turns only an empty folder into a link; and Windows refuses to rename
a folder with an open file below it. The same holds for every folder above.

- The kept files. The uninstall program creates two files named
  `<token>.hold` through handles, relative to the folders that it holds: one
  in the installation folder, one in the flat folder. The create refuses an
  existing entry of that name, also a link, so nothing is created elsewhere.
  Nobody else can open these files, and Windows deletes each when its last
  handle closes.
- Hand-over. The step inherits both open handles when it is created, so
  there is no moment in which nobody holds them. It takes them over, checks
  that both files are in their folders, and only then answers. The uninstall
  program goes on, and lets its own handles go, only after that answer.
- While both files are open, the step deletes the parked files directly in
  the flat folder: plain files whose name is a number. It never uses
  `-Force`: a read-only, hidden or system file stays, and no attribute of
  any file is changed. Anything else there stays. It removes the named
  folders of the installation that are empty by then.
- Still while both are open, and only when a fresh listing shows nothing but
  the flat folder, the kept files and the two records: the step deletes the
  journal, and then, if nothing appeared meanwhile, setup's completion
  record. Both records are checked before anything of the end happens; a
  record that is a link, a folder or a read-only file is refused. A plain
  delete of a path removes a link as a link.
- The step closes the flat folder's file and removes the flat folder while
  the installation folder's file is still open. Then, if everything was
  deleted and the folder above holds something else, it closes that last
  file and removes the installation folder. Every folder goes by
  RemoveDirectory only: an empty folder goes, a link goes as a link, a
  folder with entries refuses.
- The step creates no file and opens no existing file. It reads the whole
  journal record before every pass and every named folder, and stops when
  the record is missing or different. An entry that was created after the
  step started stays.

The step does not delete other files in the installation folder. A file of
the installation that is still there at the end (a program uses it) stays,
and then the folder stays with both records.

Four earlier forms of this step were given up after review. The first walked
`python` and `tools` by path: a program of the same account could swap a
folder for a link between the look and the delete. The second deleted in a
flat folder that could be empty, and the third relied on the step's current
folder: Windows lets a program turn an empty folder into a link in place,
without a rename, also when it is another program's current folder. The
fourth let the step create its kept file by a path: Windows follows a
dangling file link of that name and creates the file at the link's target.

**Known limits.** Two reviewers examined this design in eight passes (pull
request 2110). These limits stayed. They are stated, not solved. The numbers
are the review's; a number that is not in the list was closed before the last
pass. G1 is from the later review of the store rule, and the entry without a
number is from the last review. Except for L3, L4, L14, L15 and the first
part of the entry without a number, each needs a program that runs as the
same person. For G1 that program can act at any time before the uninstall.
For the entry without a number it can act during the uninstall, or at any
time after an uninstall that stopped. For the others it must act in the
moment of the uninstall.

One reviewer rated L12, L13 and G1 as blocking, the other as not blocking.
The owner accepted L12 and L13 on November 15, 2026. On November 17, 2026,
with G1 stated to him, he ordered the merge. Both reviewers rated every other
limit as not blocking.

- **L12 (accepted).** A program can put files into the installation folder
  between the step's last listing and its deletion of setup's completion
  record (one command), and a file with the name of setup's mark of
  unfinished work among them. The folder then stays without setup's record,
  and a later setup run empties it, including that program's files. What can
  be lost is a file that this program put there in that instant: the
  person's data is already deleted by then (the person chose that), and the
  listing showed nothing else. No order of commands closes this while the
  folder is removed: Windows cannot remove a folder together with its last
  file in one step. Such a program can delete those files, and setup's
  record, without us.
- **L13 (accepted).** The last command removes the installation folder by its
  path after the last kept file is closed. It can remove only an empty
  folder. It runs only when the folder above holds something else, so that
  folder cannot be emptied and turned into a link unless a program removes
  those other entries in that instant. If a program renames the folder above
  in that instant and puts a link in its place, the command removes an empty
  folder named `Amplifier` at the link's target, if one exists. What can be
  lost is one empty folder: no file, and no folder with entries. Windows
  refuses to rename a folder while a program has a file open below it. The
  product installs only into `%LOCALAPPDATA%\Amplifier`, and that folder
  always holds files that are in use (general knowledge about Windows; not
  measured in this work). To close this, uninstall would have to
  keep a file in the folder above, which is outside the installation folder;
  uninstall changes nothing there.
- **G1 (the owner ordered the merge with this limit stated).** Uninstall
  takes the folder `s` for program files when
  setup's completion record names a release from 0.98.5 (see "Only exact
  names"). That record is a private file of this person, so a program that
  runs as this person can rewrite it, at any time before the uninstall. In
  an installation that an earlier setup file made, a folder `s` that is not
  Amplifier's is then deleted with the program files after the two Yes
  answers. What can be lost is that one folder with its entries, inside the
  installation folder. Such a program can delete the folder itself. No code
  closes this while the store folder of a new installation is still deleted:
  every record that could prove who made `s` is a file of this person. The
  design already rests on the same record: without it there is no delete
  choice at all.
- **L2.** The step tells a parked file from a later entry by its creation
  time. A plain file with a number as its name and an older creation time,
  which a program moves into the flat folder while the step runs, is deleted
  with the parked files. That folder is inside the folder that the person
  chose to delete.
- **L3.** After a power loss in the last seconds, or when the step after exit
  cannot run, program files and both records stay (see Leftovers below). They
  hold none of the person's data, Amplifier cannot start from that folder,
  and setup installs and deletes nothing in it. Nothing in the product
  removes this leftover: the person deletes the folder by hand, or moves it
  away. When the launcher's start program is gone and setup's look finds the
  journal of a delete and none of the person's data, setup's message says
  that it found no chats or settings, and it tells the person to move or
  rename the folder (see Leftovers below). In the other cases setup's
  messages there say that Amplifier is already installed, and that the
  person's chats and settings are in the folder or are kept. That is not
  true for this leftover.
- **L4.** A journal that is damaged, or that records another choice, proves
  nothing. When the look that an uninstall makes before its questions finds a
  journal, the uninstall reads it through the private-file boundary. When that
  read gives no journal that records the delete choice, the uninstall offers
  only to keep the folder, and it deletes none of the person's chats,
  settings, accounts or program files in it. Afterwards the launcher refuses
  to open Amplifier from that folder, as long as its look finds that file. The
  question and the messages of that uninstall say what it does, not what the
  folder holds: an earlier removal can have deleted files. When one of its
  steps from the lock to the removal of the Windows entries fails, it shows a
  text of a removal that did not finish, which tells the person not to open
  Amplifier. Its other stops show other texts. A failed check of the
  installation files, and a question that Windows does not show, show the text
  of an uninstall that stopped before it changed anything. While another
  Amplifier window holds the lock, the text says that. For a last message that
  Windows does not show, the uninstall program tries once more to show it. A
  failure that no step names shows a text that does not say what was changed.
- **L5.** After a program renamed an entry only by case during the removal,
  uninstall stops with the journal in place. A second uninstall refuses at
  its first check until the entry has its own name again.
- **L9.** A program that can control another program of the same person
  (close its handles, change its memory, end it) can end every hold. Such a
  program can do anything that this person can do.
- **L10.** The step after exit reads the journal by its path, after it looked
  at the entry. A program that swaps a link in between makes the step read
  another file. The step compares that text with its token and stops when it
  differs. Nothing is written or deleted through that path.
- **L14.** A step after exit whose answer came too late keeps the two files
  open and waits. The uninstall program has then moved nothing into the flat
  folder and has not deleted `python` and `tools`. So the step deletes no
  file; it can remove a named folder of the installation that is already
  empty. It stops at its deadline.
- **L15.** The two kept files' handles can be inherited only in the moment in
  which the step after exit is created, and the step is created with an
  exact list of handles. A process that another thread of the uninstall
  program started in that same moment, with handle inheritance and without
  such a list, would also get the two handles. The uninstall program starts
  no other process then.
- **The journal is not held open.** The uninstall program writes the journal
  and then looks once, by its name, whether it is in the folder. When that
  look does not find it, this uninstall stops, and it has deleted nothing in
  the folder. An earlier uninstall can have deleted files there. (The
  private-file writer reports a publication as done also when the file lost
  its name directly afterwards, since pull request 2247; before, the writer
  itself stopped the removal then.) After that look the uninstall program
  holds no handle on the journal, and it does not read the journal again
  before its last step (`windows_uninstall.finish`). Three things follow. They
  say what the uninstall program does and what it does not delete. A program
  of this account can change the folder at any time, so they do not say what
  the folder holds afterwards.
  - A stop at this step leaves it open whether a journal is in the folder.
    This part needs no other program. The writer can report an error after the
    journal has its name. The look answers no for every error, not only for a
    name that is not there. The launcher's look is the same one: when it finds
    a journal, the launcher refuses to open Amplifier; when it finds none, or
    gets no answer, the launcher does not refuse for that reason. So the two
    texts of this step say neither. Which of the two it is follows what this
    uninstall saw, not what is in the folder at the write. It saw a journal
    when its read before its questions gave a journal that records the delete
    choice (a second uninstall), and when its look directly before it writes
    its own journal finds an entry with that name (for example, another window
    began a removal while a question waited). A journal is no proof that a
    removal began (a program of this account can write one). The uninstall
    takes it as the sign of one. Then the text is the one of a removal that
    did not finish: it tells the person not to open Amplifier, because its
    folder can be partly deleted. A journal that a program deleted after the
    uninstall saw it still counts. When the uninstall saw no journal in either
    way, the text says that this uninstall deleted nothing in the folder, and
    that Amplifier might not open until the next uninstall.
  - A program of this account deletes the journal: during a removal, or at any
    time after a removal that stopped. That takes away what the journal does.
    If the removal did not finish, then, for as long as no look finds a
    journal, the launcher's commands do not refuse because of one, Amplifier
    can open on a folder that is partly deleted, and a next uninstall that
    reaches its questions asks the questions of a folder without a journal. A
    removal whose journal a program deletes after that look goes on up to its
    last step as it does with its journal: it does not look for the journal in
    between. When none of its steps fails and step 9 did not stop the
    uninstall, the last step follows. It reads the journal once it has the
    lock and the folder. When it finds no journal then, it refuses and deletes
    nothing: not `python`, not `tools` and not setup's completion record. A
    journal that a program deletes after that read does not by itself stop the
    last step. The step after exit reads the journal's token before every pass
    and stops without it.
  - A program of this account replaces the journal with its own file in the
    instant of the write. When the writer reports no error and the look after
    the write finds the name, the removal goes on as it does with its own
    journal: the uninstall program does not read the file at that look. When
    none of its steps fails, it deletes the person's data, as the person chose
    twice. It removes the Windows entries through setup's record of them: when
    it finds no such record in the folder, or the record says that they are
    removed already, it removes none. It also tries to delete the caches and
    the other program files, in two attempts: the first before its step for
    the Windows entries, the second after that step, for what the launcher's
    own check needs. When an attempt ends with files still there, the removal
    goes on. An error of the first attempt stops the removal. An error of the
    second that the removal catches (`OSError`, `ValueError` or
    `RuntimeError`) does not stop it: the removal then has no last listing of
    the folder (step 9). When none of its steps fails and step 9 did not stop
    the uninstall, the last step (`windows_uninstall.finish`) follows. Once it
    has the lock and the folder, it reads the journal again through the
    private-file boundary, before it starts the step after exit. It refuses a
    file that it cannot read there, a file that is not a journal, a journal
    that records another choice, and a journal with another token. Then the
    last step deletes nothing: not `python`, not `tools`, not that file and
    not setup's completion record. It makes no flat folder and does not start
    the step after exit. The uninstall program tries to show a message that
    the last program files could not be removed. When Windows does not show
    that message, the program tries once more. When Windows does not show it
    then either, the person gets no message after the confirmation of step 9.
    A file that the last step can read there as a journal (`schema` 1), and
    that records the delete choice and this removal's own token, passes. The
    step after exit (`FINISH_SCRIPT` in `windows_removal.py`) looks at the
    entry with the journal's name and refuses it when the creation time that
    Windows reports for it is later than the step's own start. Then it reads
    the file by its path and compares the same values: schema, choice, token.
    (L10 is about the time between that look and that read.) When it accepts
    the file too, the removal goes on as it does without a replacement, and
    the step after exit treats the other program's file as the journal. In
    each case the uninstall deletes no entry that it does not delete with its
    own journal. For a later uninstall that reaches the look before its
    questions, the other program's file is a journal like any other: what that
    uninstall does follows that look and its own read (see "While the journal
    exists" above, and L4).

  Such a program runs as the person and can delete the person's data itself.
  That is the condition of this limit, not a protection against it.

Under PowerShell constrained language mode the step stops before it answers
and changes nothing; the parked files and both records stay.

**Rule for setup.** An installation must never be made in a folder that holds
`unified-uninstall.json`. Today's setup keeps this rule without a change:
while anything is left in the folder, setup's completion record is there too,
and with that record setup installs and removes nothing. The step after exit
deletes the journal and then the record, when nothing else is left. A second
setup run can add the Start-menu and Apps-list entries again and start the
launcher (see Leftovers). The launcher does not open Amplifier while the
journal exists. Setup reads the journal for one purpose only: to choose the
text of one message (see Leftovers).

**Leftovers.** If the step after exit cannot run, the parked program files
and both records stay; they hold none of the person's data, and the message
says that the folder can be deleted. The same leftover stays when the PC
loses power in the last seconds, after the Windows entries are gone and
before the step after exit has run; then no message was shown and no entry
exists to uninstall again. If something that is not Amplifier's is in the
folder, that entry and both records stay.

A later setup run finds its completion record in such a folder. It installs
nothing and deletes nothing. When the launcher's start program is gone, setup
shows one message and ends. Before it shows that message, it looks once
(`Emptied` in `scripts/windows_bootstrap_native.cs`), and the look chooses
between two texts:

- Setup looks at the attributes of the entry with the journal's name. When
  they show a file that is no folder and no link, setup opens that path and
  reads it. It takes what it read for the journal of a delete when it is at
  most 4096 bytes of UTF-8 that hold a record with `schema` 1, the choice
  `delete` and a token of 32 lower-case hexadecimal digits. The schema and
  the token are what uninstall's own reader requires
  (`windows_uninstall.pending`). That reader requires a choice that is a
  text; uninstall compares the choice in `remove` and in `finish`. Setup
  requires the choice `delete` in the look itself. Setup reads the file
  with its own JSON reader, as UTF-8 only, and not through the private-file
  boundary; the two readers were not compared on a file in another encoding
  or on one that is not strict JSON. Setup opens the file for reading with
  every sharing mode, so Windows does not refuse another program's deletion
  of the journal because of this handle (the step after exit deletes the
  journal), and on the runner the journal's name is gone from the folder
  directly after such a deletion, while a handle of the look's kind is still
  open.
- Setup looks at the seven names of the person's data
  (`windows_uninstall.DATA`), one after the other. A name counts as data
  when the look at its attributes shows a file or a link, or when the
  listing of the folder shows anything in it. Setup lists an entry only
  after that look showed a folder that is no link. An emptied folder that
  Windows still keeps for a program is not data when Windows answers the look
  at its attributes and its listing; an entry that Windows does not let
  setup examine counts as data. For an entry with the exact spelling of a
  name, the removal itself gives the same two answers (`holding` in
  `windows_removal.py`). For an entry under another spelling (`App` for
  `app`) the two differ. The removal never examines such an entry: it
  counts the name as holding something, and it does not begin in a folder
  that has one. Setup's look asks Windows for the path with the name's own
  spelling and treats the entry that Windows gives like any other. Where
  Windows ignores the case of letters (an ordinary folder), that is the
  entry `App`: when it is an empty folder it is not data, and with a file
  in it it is data. A folder can be set so that Windows compares its names
  with the case of letters; there Windows gives no entry `App` for the path
  with `app`, and the look does not see what `App` holds. That is not
  shown. Setup looks for chats and settings under these seven names only.
- The look is a row of separate steps on paths. Setup does not take the
  installation's lock for it, and it holds no handle on the folder or on an
  entry between two of those steps. A program of this account that replaces
  an entry between the look at its attributes and the read or the listing
  gets the read or the listing of what it put there, also when that is a
  link. The look then answers for what it read. It still only chooses a
  text.
- When the look finds such a journal and no data, setup says: "Setup cannot
  install Amplifier yet. Setup found an installation record in this
  folder:", the folder, then "Setup cannot find a program file that it needs
  to open Amplifier from that folder. Setup found no chats or settings in the
  folder. It does not change the folder, because the folder can hold files or
  folders that are not Amplifier's." and "Move or rename that folder. Then
  run this setup file again."
- In every other case, and when any part of the look fails, setup says what
  it said before: "Amplifier is already installed in", the folder, "but setup
  cannot open it: a program file is missing. Keep that folder. It holds your
  chats and settings. This version of setup cannot repair it."

Each statement of the first text is what setup found in this run, or what
setup does, with the reason why it does not change the folder; its last two
sentences are the next step. Setup found the record's
name when it classified the folder, and it looked for the launcher's start
program directly before this look. "Setup found an installation record in this
folder": the folder has a file with the name of setup's completion record,
which setup writes at the end of an installation. Setup does not read that
file in this look, and it knows no more about the folder's history. The text
does not say that an installation was made in the folder. (The second text
says "Amplifier is already installed in" on the same evidence, as it did
before.) "Setup cannot find a program file that it needs to open Amplifier
from that folder": setup's look for the launcher's start program found no file
(`File.Exists`). That look also finds no file when Windows does not let setup
see one that is there, so the first text says "cannot find" and not "is
missing". The second text says "a program file is missing" for the same look,
as it did before; that is not true for a file that setup cannot see. "Setup
found no chats or settings in the folder": none of the seven names held
anything at the look. The text does not say that Amplifier was uninstalled,
and it does not say that Amplifier cannot start: a file that reads as a
journal is no proof of an uninstall (a program of this account can write one),
and setup looked for one program file only. The text tells the person to move
or rename the folder, not to delete it: the folder can hold something of the
person's that the look did not see, and a program can put data into it after
the look. Setup's stop for a damaged folder names the same step. A setup run
that finds no folder at that path goes on as for a first installation. The
look only chooses a text. With either text setup installs nothing, deletes
nothing and ends with status 1.

Three states give the first text (a program of this account can make
others): an uninstall with the delete choice left the folder because it holds
something that is not Amplifier's (seen on a PC on November 17, 2026, with
the earlier text; that is why the first text exists); the leftover of L3
when the launcher's start program is gone; and a setup run while the step
after exit still works (that step can still delete the folder, and Windows
can refuse to move the folder while that step holds it). The second text can
still be false after a delete: when the look finds no journal of a delete
(the journal is gone, a program replaced or damaged it, or Windows refused
the look), and when one of the seven names holds something again (a folder
that the person made under that name, for example). The text then says that
the folder holds chats and settings that the uninstall deleted. That limit
stays.

Proven on a Windows runner (Windows Server;
`tests/windows/native_setup.Tests.cs`, job `windows-setup-folder`): both
texts, complete; each of the seven names as an empty folder, as a folder with
a file or a folder in it, and as a file; an empty folder, and a folder with
a file in it, under another spelling of a data name (`App`); a link with a
data name; an empty data folder that Windows refuses to list; a link to a
journal, and a link to
a folder, with the journal's name; nineteen files that are not such a
journal, bytes that are not UTF-8, a record with one byte in a text value
that is not UTF-8, and a folder with the journal's name; a journal of exactly
4096 bytes; a journal that another program has open for writing, one that is
deleted while a handle of the look's kind is open, and one that another
program holds for itself; that the folder holds afterwards what the case put
there; and that a look changes no creation time, no write time and no
attribute of the journal, of setup's record and of the person's file. Not
proven: the message in setup's real window, the three states on a PC with
this text, the look on Windows 11, the look in a folder whose names Windows
compares with the case of letters, a program that replaces an entry during
the look, and a setup run in the instant in which the step after exit deletes
the journal.

When the launcher's start program is still there (a removal that
stopped before its last step), setup asks, and on Yes it lets the
installation's own program add the Start-menu and Apps-list entries again,
and then it starts the launcher. That program adds the entries only when the
installation's own records are whole and no entry exists, and it does not
look for the journal. The launcher then does not open Amplifier:

- While the installation's own records are whole, the launcher finds the
  journal, and its message tells the person to select Uninstall Amplifier.
  With the entries back, the person can do that, and the second uninstall
  finishes the removal.
- When those records are already deleted (a removal that stopped in its last
  seconds), no entry is added, and the launcher's own check of the
  installation fails before it looks for the journal. A launcher from a setup
  file before pull request 2270 then says that Amplifier could not start and
  names the setup file again, with "It keeps your chats and settings". Like
  setup's message, that is not true for this leftover (L3). A launcher from
  that pull request on looks for the journal when a start fails, and says
  what it says under the lock: the removal did not finish, and Amplifier does
  not open until it has.

This is read from the source of both programs; the combination was not run.
In none of these states can Amplifier start, and in none of them does setup
delete anything.

The program that adds the entries again takes the installation's lock and
checks the installation under it (`windows_consumer.integrate`, from pull
request 2270; before, it took no lock). An uninstall that deletes the folder
holds that lock from before it removes its entries until it has deleted the
launcher's records. So a second setup run that the person answers with Yes in
those seconds waits, at most 10 seconds. Then it finds no installation, or the
lock is still taken; in both cases it adds no entry, and setup says that it
could not add Amplifier to the Start menu. One case remains. Each file that
the check reads stayed in the folder, because another program held it. The
uninstall's last message is open, so the lock is free. A setup run in that
time adds the entries, as it does after a removal that stopped. The uninstall
then removes its own program files, and the entries name a program that is
gone. No file of the person is involved. The program does not look for the
journal here: after a removal that stopped, the entries are what lets the
person uninstall again.

The start texts name the setup file when Amplifier is not in the Start menu.
"It keeps your chats and settings" follows only where the launcher sees that:
setup's completion record is in the folder, no journal is, and the app's own
folder is there. Before pull request 2270 the sentence was also shown after an
uninstall that deleted the folder.

**A second setup run after an uninstall.** What the delete choice changes:

- After No (keep the folder): nothing. The folder is whole, with setup's
  completion record. A second setup run asks, and on Yes it adds the entries
  again and starts Amplifier with its chats and settings, as described under
  "Uninstall stops the app before it removes an entry".
- After Yes and Yes, when the folder is gone: setup finds no folder and makes
  a new installation. Nothing of the earlier installation is left to bring
  back.
- After Yes and Yes, when the folder is still there (it held something that
  is not Amplifier's, or the step after exit could not run): the leftover
  above. Setup installs nothing there and deletes nothing, and Amplifier does
  not start from that folder. A new installation is possible only after the
  person deleted that folder, or moved what is theirs out of it and deleted
  it.

The first is shown on a Windows runner (`windows-start-again.yml`, without
the setup file). The second and the third are clean-room acceptance.

**Which installations get it.** This is launcher code: a new installation from
a setup file of a release that has it, and an earlier installation after a
launcher refresh.

**Proven on a Windows runner** (`windows-local-connection` job; a skipped case
fails that lane): the whole order on a throwaway installation folder with its
real shortcuts and Apps-list entry, each command as a new process behind a
real start program with the workspace as its current folder. The running
start program and a loaded DLL cannot be deleted and are moved into the flat
folder (same bytes there); when the process ends, the folder holds only the
flat folder, the journal and the emptied workspace; the step after exit
removes them and the folder is gone; an outside folder behind junctions is
unchanged. One case deletes the Python that it runs from (interpreter,
standard library and the installed package inside the folder) and still ends
cleanly. While the step waits, a rename of the installation folder, of the
folder above it and of the flat folder is refused, and Windows refuses to
turn the flat folder or the installation folder into a link in place (error
145); the same call
works on an empty folder that nobody holds. A folder with an open file in it,
and the folder above it, can be neither turned into a link nor renamed. The
delete choice runs once on a folder whose setup record names release 0.98.5
and whose store is `s`, and once on a folder whose record names 0.98.4 and
whose store is `source-store` (the test writes the record and the store; no
setup file runs there). The keep choice leaves either store with every other
entry. The second kind of folder, when it also holds a folder `s`, keeps that
folder with its file, and then the installation folder stays. A
link under the kept file's name is refused and nothing is created at its
target (the same create by a path does create the target). After the
uninstall program let go, the two kept files stay with the process that
inherited them: no other program can delete, move or read them, and both are
gone when that process has ended. With nothing else in the folder above, the
emptied installation folder stays. A plain delete of a path that has become a
link removes the link or nothing, never anything behind it. In the flat folder a junction, a
folder, a file link, a file in use, a read-only file, a hard link to an
outside read-only file and a later entry all stay. A data file in use stops the removal with the entries and the
journal in place; Open, Status and Stop refuse; a second window refuses while
the lock is held; a second uninstall finishes; a file that is not Amplifier's
stays. A journal that the test deletes inside the real writer, directly after
the writer renamed it into place, stops a first and a second uninstall before
they delete anything, each with its own text; no journal is in the folder
then, and Open reaches its start step, the second time on a folder that is
partly deleted. A journal that the test replaces in that instant lets the
removal go on to its last step, which refuses it (the test's journal
has another token): `python`, `tools`, that journal and setup's completion
record stay, and no step after exit starts. The emptied `workspace` can stay
too (it is the current folder of the test's uninstall process). Stand-ins:
Task Scheduler, the check of an installed release, the start step of Open,
and the dialogs.

**Not proven:** the dialogs on screen; a real stop with live conversation
workers followed by the deletion; uv's own start program (the check uses a
venv start program); PowerShell constrained language mode; the lock between
two sign-in sessions of one person; a second setup run on a folder whose
removal did not finish; a complete
uninstall of an installed build. These remain clean-room acceptance. Shown
only with a stand-in for the part that fails, never with a real failure of
Windows: a look for the journal that Windows does not answer; an error of the
writer after the journal has its name; a question, a last message or the
text of a stop that Windows does not show; a second uninstall window (a
second thread on a stand-in folder). Not
built: "keep my chats, remove the program". The launcher refresh command does
not look at the journal.

The standard-account case (next section) runs the delete choice once on an
installation that was started, stopped and started again, with the real task
and the Windows question about the certificate. Its start program is the one
that `uv tool install` made (the runner's uv, not the uv that setup
downloads). Afterwards the folder is gone. That is one run on a folder that
the test made. It is not an installed build, and it has no chat.

### A standard Windows account

A standard account is not a member of Administrators. Many work PCs give the
person such an account. Every clean-room run until November 17, 2026 used
Windows Sandbox. Its user is a member of Administrators, so those runs showed
"no administrator rights" only as "no elevation prompt appeared". The account
of a hosted runner is the built-in Administrator.

`.github/workflows/windows-standard-account.yml` runs the consumer journey as
a standard user on a hosted Windows runner
(`tests/fixtures/windows_standard_account_native.py`). The job creates one
local user with a random password that is never printed. The user is a member
of Users and of Remote Desktop Users; the second group gives only the right to
sign in through Remote Desktop. The user signs in with its name and password
through Remote Desktop to the same computer (address `127.0.0.2`). Windows
gives it a desktop session of its own. The test requires what Windows says
about its own token there: this user, not a member of Administrators, one
token with no administrator token beside it, not elevated, medium integrity,
a sign-in of the user itself, and a desktop session that is not the runner's.
A helper task of that user starts the test in that session. The program that
runs as the user refuses unless the run's account record names this user,
belongs to the administrators group, and cannot be changed by this user. It
reads the record's text, its owner and its permissions from one open file,
and Windows itself computes from the permissions whether this user could
change the file or its folder. A person without administrator rights cannot
make such a record.

The job's cleanup step (the step before the one that keeps the results) runs
in every case, also after a failure. It has these parts. In every case: end
the Remote Desktop viewers that the run recorded, delete the saved sign-in,
restore the one setting that the job changed. For the run's account: sign the
user out, try to ask Windows to end the programs of the user, remove the
user's tasks, its profile and the account. A part can fail. The next
paragraphs say what the step does then, and which parts it leaves out.

The job records the account's name before Windows creates the account, with a
mark that Windows keeps as the account's comment. It records the account's
SID before it signs the user in. When the job recorded no account, the step
has only the three parts of every case. A record without a name or without a
mark fails the step, and the step touches no account. Before the cleanup step
removes anything of the account, it reads whether the account with that name
has that mark. When the name belongs to another account, or Windows does not
answer, the step removes nothing of that account and fails. When Windows has
no account with the recorded name, the step sends no request to end programs
and deletes no account; it does the other parts with the recorded name and
the recorded SID. Windows names the user's Amplifier task and its profile by
the account's SID. When the step has no SID (the job did not record it, and
Windows does not give it or has no account with the recorded name), it counts
the task and the profile as not read, does the other parts, and fails. It
concludes nothing from an account that is absent. Apart from these cases, a
part that fails does not stop the parts after it, and the step fails when one
of these parts is not done: the recorded viewers, the saved sign-in, the
setting, the user's session, the tasks, the profile, the account.

The step decides that from Windows. A recorded viewer is one process: its
number and the time at which Windows created it. The step opens the process
that has that number once. When its creation time is the recorded one and the
process still runs, the step asks Windows to end it and waits up to 20
seconds for its end, through that same open process. A viewer that still runs
then counts as left. When no process has the number, or the process with the
number has another creation time, the viewer is gone, and the step ends
nothing. A viewer that the run started and did not record is not ended by
this step. Before the job creates the user, it requires three things on the
runner, with a program of the test: the recorded creation time is the time of
the program's start (within two seconds); under another creation time the
program is left alone; and its own record ends it. For the user's session,
the tasks, the profile, the account and the setting, the step reads what
Windows lists after the part. A list that Windows does not give is a failed
part. For the saved sign-in the step takes Windows' answer to the deletion.

For the run's account, the step tries once to ask Windows to end the programs
that run under the user's name (`taskkill`). When the request cannot be sent,
the result says so, and the step does not fail for that. The step does not
read whether a program of the user is left, and that does not decide whether
the step passes. The step also copies the user's reports to the results and
saves its own result; neither decides whether the step passes. The step does
not delete this case's folders in the runner's temporary folder; no file
there holds the password. If GitHub ends the job at its time limit, the step
may not finish. A hosted runner is deleted after its job.

The sign-in is needed. A program that another account starts with the user's
name and password (`CreateProcessWithLogonW`) can register the user's task,
but Task Scheduler does not start it: a task that runs with a person's own
sign-in starts only in a desktop session of that person (state Ready, result
0x41303, measured on a hosted runner).

As that user, in `%LOCALAPPDATA%\Amplifier` of its own profile, the case has
18 steps. Each step is one line of the result: passed as a standard user, or
failed with the exact text. A failed step fails the job, and nothing in the
job can skip.

1. Setup's own code, compiled from the setup sources. Its checks before any
   question read no elevation and stop only at setup's platform check: the
   Windows version or the processor. The runner is Windows Server on an x64
   processor; the case does not tell the two apart. A folder that a program
   of the user made reads as the user's own. The sign-in task reads as
   absent. The program of these checks is made by setup's compiler with
   setup's options. Its file is a 64-bit program that asks Windows for the
   rights of the person who starts it (`asInvoker`), and it starts for this
   user.
2. The installation in setup's layout. The test makes it, not the setup file:
   setup's two uv commands for a private Python, `uv tool install` of the
   candidate wheel (without the component list of a release), and a copy of
   the runner's Git.
3. Setup's two commands of its step "Checking the installation", and setup's
   completion record. Then the Start-menu and Apps-list entries, in the
   user's own profile.
4. The first start (the launcher with a console Python and `--no-open`): the
   task is registered, the launcher finishes, and the host that Task
   Scheduler starts answers. Status. "Stop Amplifier" (the product's
   function, with its one question answered in the test). A second start: a
   new host on the same port.
5. The launcher's certificate step (`review_browser_trust`). Before it,
   Windows does not trust the connection. Amplifier shows its one message.
   Windows asks its one question. The test requires that its text is the
   install question, names the certificate and shows the thumbprint as
   Amplifier's message shows it. The test selects Yes with a window message,
   once. Then the launcher's check reads the connection as trusted, and
   Windows' own list of the user's root certificates holds the certificate.
   A dialog of this program that is not this question, and a second question,
   get no answer. When such a dialog stays open for 90 seconds, the step
   fails with what the dialog shows, and the program ends.
6. Setup's own code reads the installation as installed, with its sign-in
   task. A second setup run would open it.
7. A sign-out and a new sign-in. After the sign-out the task exists and does
   not run. After the sign-in the task's own trigger starts a new host on the
   same port, with no launcher command, and Windows still trusts the
   connection.
8. Uninstall with the choice that keeps the folder. Windows asks whether to
   delete the certificate; the test selects No, and the certificate stays in
   Windows' own list. Then the entries again and a start, as setup's second
   run does them.
9. Uninstall with the choice that deletes the folder (Yes, and Yes again).
   The test selects Yes at the Windows question. Afterwards the user has no
   installation folder, no sign-in task, no Start-menu entry and no Apps-list
   entry, Windows' own list of the user's root certificates no longer holds
   the certificate, and setup's own code reads an account without an
   installation.

First result (November 17, 2026): every step passed. In these steps the case
found nothing that a standard account cannot do.

The case does not show these parts. The result names each of them with its
reason.

- The setup file: its windows and its question, its Windows 11 check with the
  answer yes, and its downloads of uv, Python and Git. The setup file refuses
  Windows Server, and every hosted x64 runner is Windows Server. Read from
  the published setup file of release 0.105.0, without a run: it is a 64-bit
  program, and its one manifest level is `asInvoker`. Windows asks for an
  administrator when a manifest says so, or, for a 32-bit program without a
  level, when the file name looks like a setup program. Neither is the case
  here, so a standard user should get no administrator question at the
  start. No check reads the setup file of a new release for this.
- A person's own answer to the Windows certificate question, and that the
  question is in front of other windows. No person is present. The test
  answers the question from the program that asked it.
- The browser. The launcher runs with `--no-open`, and nothing in the case
  reads a browser window.
- A click on a Start-menu or Apps-list entry, and the messages of the
  windowless launcher. A message waits for a person. The case runs the same
  launcher commands with the console Python, and answers the product's
  questions in the test.
- A chat and a model request. No AI account is connected.
- Windows 11, and a sign-in at the PC's own screen. The runner is Windows
  Server, and the user signs in through Remote Desktop.

Also not shown anywhere: a standard account on a PC that an organization
manages (rules for programs, a policy that disables a person's own
certificates, limits on Task Scheduler); a Microsoft, domain or Entra account
(the case's user is a local account); and a standard user who starts setup
with "Run as administrator" and gives an administrator's name and password.
Setup then runs as that other account. Read from source: it stops with one of
its administrator texts and installs nothing.

## Process-local Proactor cleanup backport

`windows_proactor.py` carries CPython gh-160429's socket-shutdown correction
from commit `7b06d6f80df2729a68f78d2a346c043d10f82fd6`. The Windows app factory
installs it before creating application state or accepting sockets. This is an
explicit compatibility backport, not an exception-log filter or a Python-file
edit. It overrides only the current process's Proactor **socket** transport
cleanup; pipe transports, loop policy and exception handlers are unchanged.
TCP and TLS use that socket transport.

The adapter compares the normalized source AST against the reviewed vulnerable
and fixed implementations. Wrapped callables are rejected before source
inspection, which otherwise follows `__wrapped__` rather than the installed
callable. Unknown runtime code or an independent override refuses startup rather
than replacing it blindly. A recognized upstream-fixed runtime needs no override.
Only `ConnectionError` raised by the shutdown call is
handled; protocol callback errors and unexpected socket errors remain visible.
The shipped `CPYTHON-LICENSE.txt` retains upstream licensing.

Remove the adapter when the qualified Windows interpreter includes the fix.
The initially inspected stable 3.13.18, 3.14.10 and 3.15.2 tags did not contain
it. Selecting one of those versions alone is not a repair. This compatibility
change requires its own native TCP/TLS, teardown and installed-entry evidence.
It does not authorize modifying a running user's interpreter or restarting a
host. Deploy a separate qualified candidate with a normal rollback path; retain
the prior application's evidence and unsent drafts.

## Owner-logon candidate

The selected personal-PC deployment is an **interactive-token Task Scheduler
host**, not an SCM service: start at sign-in, survive terminal/browser closure,
and stop at sign-out. It stores no Windows password, requests no elevation and
does not run agent work as LocalSystem. The explicit prototype installer accepts
`-Background`; the generated private shortcut selects that same mode.

The installed `windows_installation` library composes the ordinary CLI config,
TLS, `runtime prepare`, service install and service status operations. A second
shortcut activation inspects/reopens the same owned task; it does not resync
dependencies or replace another task. Occupied addresses and foreign task
definitions are refusals, not permission to adopt or terminate a host.

`runtime prepare/status` distinguishes two results:

- With no AI account, prepare the declared baseline dependencies and installation
  policy. Report `connectionRequired: true`, `workerReady: false`, no qualified
  profiles and no inference. Do not invent a provider to pass a profile probe.
- With a configured account, reuse the canonical updater's complete preparation,
  no-model profile qualification and exact recorded-generation verification.
  Activate only after that evidence matches.

Both paths hold the existing exclusive repair lock. A running host holds it
shared. Recorded dependencies are inspected without branch resolution or repair.
Damaged receipts stay damaged and visible; shortcut use never refreshes them.

The runner publishes a private registration/run/PID/birth/image receipt. Status
joins that exact process, the native IPv4 listener table, the private leaf pin
and authenticated app/data/version health, then rechecks currentness. Normal
stop requests target only that exact run and wait for its cleanup; Task.Stop,
force termination and port-based killing are not used. Logs retain a bounded
128 KiB private tail.

Prepared-account native checks cover real task registration/start, pinned
health, same-process reopen, normal stop/restart, private logs and removal, plus
accountless dependency preparation. These are **not** clean-account bootstrap,
actual sign-in/sign-out, browser trust, automatic app upgrade or rollback
acceptance. Existing native foreground Sol/file/Stop/history evidence remains
separate. The public activation gate remains closed until those remaining
boundaries are qualified. Script execution policy is respected: a policy refusal
does not authorize changing it or executing the file through another route.

### Fresh-guest prototype result

A separately created Hyper-V guest now provides a genuine Windows 11 Enterprise
Evaluation x64 / ordinary-user baseline. Before setup, `UnifiedTest` was
non-administrative, Python/Git/uv/PowerShell 7 commands were absent, and the guest
WSL feature was disabled. Hyper-V/WSL management on the **lab host** is not a
prerequisite installed in the guest.

The native prototype entry completed under that account with the default
PowerShell `Restricted` policy unchanged. It acquired private verified tools and
ordinary CPython 3.13.17, installed the same original 0.65.0 app wheel and frozen
dependencies, and created the private launcher. A separate installed audit
verified 65 noneditable distributions, five canonical full Git origins, 3,594
hashed installed RECORD entries, all 928 hashed original app members, and
single-link package files. Capsule delivery was **staged locally**, not downloaded
from a published Unified release. No app wheel or frontend asset rebuild was
needed for the bootstrap corrections.

The installed canonical library then prepared the accountless baseline, created
the user's real interactive-token/Limited sign-in task, and passed authenticated
process/listener/leaf-pinned health. Same-process reopen did not prepare
dependencies again. Normal stop, a new-run restart, and final normal stop passed;
independent inspection found both exact processes gone and port 8942 empty.
The stopped, enabled logon registration is deliberately retained for the pending
real sign-out/sign-in check. Finite verification tasks were removed normally.
No provider credential transfer, inference, browser entry or trust import occurred;
the authenticated health path did use its canonical private **app control**
credential. Do not describe that as zero credential reads.

Failed attempts remain evidence: policy-refused script entry, unavailable Python
download in the original helper catalog, long Git checkout paths, and a native
entry receipt/check ordering error. Separate verification-helper errors (a
nonexistent liveness helper and an eight-tick FILETIME rounding error from CIM)
were corrected using the existing exact native process handle, without replaying
startup or weakening identity checks. These are not product-failure fixes.

**Still unqualified:** public HTTPS artifact delivery, signed/download reputation,
browser trust and owner entry, real sign-in/sign-out, and cross-version automatic
upgrade. The host desktop was locked and the supported controller refused capture;
no substitute screenshot/keyboard route or lock-policy change was used.
This is clean-account **prototype bootstrap and lifecycle evidence**, not the
public “install on any Windows 11 PC” claim or permission to open its gates.

### Clean-account entry correction

The first genuine disposable Windows 11 account exposed a delivery defect:
Windows PowerShell 5.1's default effective policy was `Restricted`, and the
ordinary non-admin user's normal invocation of `install.ps1` refused with
`PSSecurityException` before setup. The policy was not changed. That result
supersedes any assumption that prepared-account script fixtures established
zero-prerequisite installation.

The prototype therefore adds a **native x64 executable entry**, built on a
Windows build runner against the in-box .NET Framework 4.8. No compiler, Python,
Git, uv, WSL, PowerShell 7 or script-policy change is required on the target.
`scripts/windows_bootstrap_native.cs` performs bootstrap directly; it does not
invoke PowerShell, load its automation engine, or repackage the refused script
as an inline command. Its build preparer
`scripts/windows_bootstrap_native.py` reuses the exact existing C# `InstallStorage`
body and canonical helper/app pins at build time, rather than introducing a
second private-filesystem policy.

This is still an explicit **prototype**, not a public installation path.
It requires a reviewed manifest digest and preserves the same capsule, frozen
host graph, private helper/Python layout, installed audit and ordinary launcher.
The optional `--capsule-file` input permits a staged, hash-verified candidate
before publication; its receipt explicitly distinguishes that path from an
official HTTPS download. Local staging is not public-download acceptance.
`--background` selects the already-defined owner-logon mode; `--no-open`
prepares setup without opening a browser or starting the app.

Build-source preparation, compilation, restricted-policy execution, clean
bootstrap, signed/download reputation, browser trust and actual sign-in/out
remain separate evidence. An unsigned development executable is not a signed
or SmartScreen-qualified release. Neither entry opens the public activation
gate, silently changes trust, or qualifies automatic upgrades.

## Zero-prerequisite bootstrap prototype

The initial direction was a single PowerShell paste for an ordinary Windows
information worker, with **no curl, Git, Python, uv, WSL, PowerShell 7 or winget
assumed**. The clean-account policy result above requires the native entry
instead of routing around a refused script. The retained `scripts/install.ps1`
has a separate explicit prototype entry:
`-PrototypeManifestUrl` plus `-PrototypeManifestSha256`. The no-argument public
release installer still requires its native-support record and retains its
closed activation gate. A prototype manifest has `status: prototype`; it is
not a native-support record and cannot enable that public path.

**Not yet a released installation command.** The Windows trial owner must first
join the exact native candidate, collect its verified prototype inputs,
publish the immutable prototype assets and provide their digests. This code
candidate does not claim those artifacts exist or that absent-prerequisite
installation has run on a clean Windows account.

### Chosen prerequisite strategy

1. The selected `pinned-vcs` mode always acquires root-local official uv
   **0.13.7** and standard **MinGit
   2.56.0.windows.4** ZIPs. Verify pinned SHA-256 before extraction/execution;
   retain whole distributions, licenses and receipts. It does not rely on
   preinstalled developer tools. The retained `offline-wheels` mode may reuse
   compatible, ACL-validated existing helpers.
2. Use uv's managed ordinary **CPython 3.13**, selecting an exact supported patch
   in the prototype plan. Download into this installation's private Python
   directory with `--no-bin --no-registry`. Store aliases and a system Python
   3.15 are neither invoked nor replaced. No compiler is installed.
3. Install the original app plus 59 registry wheels by hash, without dependency
   resolution or source-build fallback. Then install **five full-commit Git
   requirements** noneditably using normal uv semantics and six pinned isolated
   Hatchling backend wheels. This online step preserves honest installed Git
   origins; it does not replace them with locally built dependency wheels.
4. Use an absolute Python/launcher and retained helper paths on every relaunch.
   No user/machine PATH, shell profile, default Python, registry registration,
   service or Scheduler mutation is needed for the retained foreground mode.
   The explicit background mode registers only the owner-logon task described above.

Official references: [uv standalone installation](https://docs.astral.sh/uv/getting-started/installation/),
[uv unmanaged installer options](https://docs.astral.sh/uv/reference/installer/),
[managed Python provenance](https://docs.astral.sh/uv/concepts/python-versions/),
and [MinGit's non-interactive application distribution](https://gitforwindows.org/mingit).
The [Scoop installer](https://github.com/ScoopInstaller/Install) demonstrates
non-admin PowerShell setup but adds a package-manager and policy surface; it is
not installed here. [rustup's installation methods](https://rust-lang.github.io/rustup/installation/other.html)
are a useful architecture/checksum precedent, not a dependency.
Do not copy execution-policy bypasses from other projects.

Pinned helper digests are those reported by the publishers' exact
[uv release metadata](https://api.github.com/repos/astral-sh/uv/releases/tags/0.13.7)
and [Git for Windows metadata](https://api.github.com/repos/git-for-windows/git/releases/tags/v2.56.0.windows.4).
Publisher-served digests establish integrity, not independent signatures.
uv verifies its own pinned python-build-standalone download digest; the exact
Python patch and resulting interpreter still need native candidate checks.

The genuine clean-account run found that the original bootstrap uv 0.12.18 has
no bundled download for the selected CPython 3.13.17. Its Python-install child
exited 2 before host installation. A read-only native catalog probe confirms
uv 0.13.7 includes ordinary Windows x64 CPython 3.13.17; the helper is now pinned
to that publisher's exact ZIP digest and source-license commit. The selected
Python, original app wheel and frozen host dependencies do not change.
The old prepared-account proof and failed bootstrap remain retained; a catalog
entry alone is not successful interpreter installation or fresh-PC acceptance.

### Build and delivery interface — trial owner only

#### Selected online frozen-VCS mode

The short path is now `--source-mode pinned-vcs`. A completely offline capsule
was an engineering option, not a product promise. The original offline packager
(`--source-mode offline`, or the unchanged default) below remains for review and
prior evidence, but does **not** establish
accepted Git origins. No canonical provenance facility or fabricated
`direct_url.json` is introduced.

Supply the unchanged app wheel plus **59** actual registry wheels in the runtime
wheel directory, the **six** build-only wheels in a separate directory, the exact
frozen lock and retained production requirements, and a reviewed inventory for
the 60 runtime wheel inputs. The builder validates the frozen source/lock/export,
actual wheel identities, original app hash/size/metadata, registry lock hashes,
five canonical full Git refs and the separate backend closure before creating
output. The source-mode manifest and capsule identity must agree.

The current prototype binds the **0.65.0** wheel built from the byte-verified
`d9b26e7bb3c5270d377520b82e498111384e931d` app snapshot, SHA-256
`2c7c7797513eaa5c85ba88937074c635d5cbc8af3a81454d9ab124f0814b977e`.
Its host lock is `a430eea8399afdb460ea3755f656b9692dfa1ee7c696171e6251b7b7b40538c6`;
the no-dev/no-editable/no-project/no-hashes uv export is
`6249ca66ef434f47735286eed61756c4c37e1ead9de607c7724c3adb6b7ffeb5`.
The native 65-distribution copy-based installation, all five real Git origins,
installed RECORDs and original app payload were checked using the private pinned
uv/MinGit tools. This supersedes the old 0.54.5 prototype recipe, not its retained
evidence. It still does not establish clean-account or browser acceptance.

```sh
python scripts/windows_bootstrap_capsule.py --source-mode pinned-vcs --wheelhouse /path/to/60-runtime-wheels --build-wheelhouse /path/to/6-build-wheels --lock /path/to/frozen/uv.lock --production-requirements /path/to/production-requirements.txt --inventory /path/to/reviewed-runtime-inventory.json --output /path/to/new-output --source d9b26e7bb3c5270d377520b82e498111384e931d --version 0.65.0 --python EXACT_3.13_PATCH --url https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.zip
```

That is an owner-side build template, not a usable installation command or a
claim that those wheel bytes/public assets are present here. Runtime inputs
exclude the five Git wheels. Build-only pins from the exact lock are
`hatchling==1.32.4`, `packaging==26.3`, `pathspec==1.1.1`, `pluggy==1.6.0`,
`tomlkit==0.15.2`, and `trove-classifiers==2026.9.21.13`; all must be verified
pure-Python wheels. They do not become target host dependencies merely because
they build the five source packages.

The installer validates the extracted source payload using the private managed
Python **before** venv creation or package installation. It reconstructs the
requirements/constraints from the frozen inputs and refuses injected options,
wrong refs/subdirectories, missing/extra wheels or changed hashes. It then uses:

1. `uv pip install --no-index --no-deps --require-hashes --only-binary :all:`
   with the local runtime wheel directory and the 60 generated hashed requirements.
2. `uv pip install --no-index --no-deps --no-require-hashes --verify-hashes
   --only-binary :all: --no-binary <the-five-Git-names> --find-links <build-wheels>
   --build-constraint <build-constraints.txt> -r <git-requirements.txt>`.
   The named exceptions permit only the five trusted source builds. Git fetching
   stays online; isolated backend resolution has only the verified local wheels.
3. Read-only installed metadata auditing for exactly 65 distributions, no
   editables, all five real repository/commit/subdirectory origins and expected
   versions, then `uv pip check`, normal launcher validation and shortcut creation.
   Both installation phases use explicit `--link-mode copy`: package payloads
   must not share mutable file identity with the installer's cache.

These flags were checked against
[uv 0.12.18 CLI definitions](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-cli/src/lib.rs),
[build policy](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-configuration/src/build_options.rs),
[hash modes](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-configuration/src/hash.rs),
and [pip installation](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv/src/commands/pip/install.rs).
`--no-require-hashes` alone disables hash checking in this version; pairing it
with `--verify-hashes` retains supplied build-constraint hash verification.
`--no-deps` skips runtime dependency resolution, not isolated backend setup.
Constraints are not themselves an allowlist; complete local wheel-set validation
is also required. `--no-index` does not prohibit Git URLs or network calls made
by trusted build code. Build isolation is not an OS security sandbox.

Installer child processes clear inherited `UV_*`, `PIP_*`, `PYTHONHOME`,
`PYTHONPATH` and virtualenv overrides before applying the private layout.
They also discard Git executable/template redirects so inherited
`GIT_EXEC_PATH` or `GIT_TEMPLATE_DIR` cannot substitute another installation's
helpers or hooks. The pinned-mode relaunch environment applies the same redirect
removal while keeping the caller's provider credentials inherited, not saved.
Anonymous Git access disables credential helpers/configuration. Ordinary
`USERPROFILE`, `HOMEDRIVE` and `HOMEPATH` stay inherited, not removed or redirected.
No credentials are saved in the plan/receipt. On relaunch the wrapper restores
root-local cache, tools, Python and absolute helper PATH. Bootstrap phase uv is
**0.13.7**; the installed host's `uv==0.13.4` dependency is a distinct artifact,
not silently substituted into the install phase.

The first full clean-account checkout also exposed Git's legacy path limit:
the retained stderr reports `Filename too long` during Foundation checkout.
This is not an established network/authentication failure. Bootstrap child
environments now set `core.longpaths=true`; the launcher appends the same Git
setting after any inherited caller configuration. It preserves existing
credential settings, rejects malformed/oversized inherited configuration counts,
and changes neither Windows long-path policy nor user/machine gitconfig.

The original app wheel is a narrow allowed root artifact; this does not grant
verified app Git identity for later optional-feature addition. Keep that
limitation and the actual worker-graph/onboarding acceptance separate.

#### Retained all-wheel mode (`offline`)

`scripts/windows_bootstrap_capsule.py` packages an **already-built**, complete
Windows x64/CPython 3.13 host wheelhouse. It performs no dependency resolution,
installation or publication. For example, with the selected candidate's actual
values and an absent output directory:

```sh
python scripts/windows_bootstrap_capsule.py --wheelhouse /path/to/qualified-wheels --inventory /path/to/reviewed-inventory.json --output /path/to/new-output --source EXACT_APP_40_HEX_COMMIT --version EXACT_APP_VERSION --python EXACT_3.13_PATCH --url https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.zip
```

The capsule contains `capsule.json` (app source/version), `requirements.txt` with
every wheel's SHA-256, `wheels/`, and this candidate's
`windows_prototype_launch.py` as `launch.py`, plus the reviewed `inventory.json`.
The builder emits
`windows-prototype.json` with schema 1, status `prototype`, exact source/version,
architecture `x86_64`, Python patch and capsule URL/SHA-256. Output is create-new
only; duplicate distributions, missing app identity, incompatible CPython tags,
symlinks, and any name/version/hash difference from the reviewed inventory refuse
before output creation. The inventory is a separate reviewed input, **not** a
list generated from whatever happens to be in the wheel directory:

```json
{
  "schema": 1,
  "source": "EXACT_APP_40_HEX_COMMIT",
  "lockSha256": "EXACT_LOCK_64_HEX_SHA256",
  "packages": {
    "amplifier-unified": {
      "version": "EXACT_APP_VERSION",
      "sha256": "EXACT_ORIGINAL_WHEEL_64_HEX_SHA256"
    }
  }
}
```

This abbreviated schema example is deliberately invalid until resolved; a real
inventory must contain the **entire** reviewed production set. Do not self-certify
closure by hashing arbitrary input wheels. The owner first checks actual
`METADATA`, active Windows markers/extras, source/build receipts and licenses.
The builder only enforces equality with that reviewed selection. Neither a
manifest label nor `pip check` establishes source provenance or selected extras.

#### Frozen 0.54.5 production inputs — provisional packaging plan

The app input is `1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153`, tree
`9657754b48f03f518fc421861738a82f8864baf7`, version **0.54.5**. Preserve the
already accepted original app wheel, **10,218,316 bytes**, SHA-256
`de950fe6dcf43e9944bfae4c45b6adeedc4b2fdbee4eda565443516a3ffcc9f5`.
Do not rebuild it, rename it as a different source, or label it with the host
application's newer version. A bootstrap join is a separate code identity:
record that commit and the exact builder, launcher and installer hashes alongside
the capsule. `--source` remains the **app** source above.

The frozen `uv.lock` SHA-256 is
`02ca2a403cb6546238e1e3b1eed2b07e2b848f7f1edc301fc67f4cc956f89c45`.
The retained production requirements export is **2,047 bytes**, SHA-256
`4013b4a681ce2e7729c7763069a0fc621d46b34e5bc0511c17fa074f1bafa6a0`.
It has 65 cross-platform dependency lines, excluding the app. Windows CPython
3.13 activates **64 dependencies plus the app = 65 distributions**. It is not a
hash-required wheelhouse manifest and must not be given directly to the offline
installer. A sanitized native installed-host audit verifies this production
selection from actual metadata; its SHA-256 is
`a7dbcf604af61d65edba22d5a17ad415e7b2b77d21916abaf90674337fa2db7d`.
That evidence does not establish an offline capsule installation.

Select only the app's base dependencies, including its explicit
`amplifier-bundle-context-intelligence[client]` and transitive `pyjwt[crypto]`.
Evaluate Windows markers: include `colorama` and `pywin32`; exclude
`httpx2-jsfetch` (`emscripten` only). Keep unconditionally declared `python-pam`
and `six`. Exclude dev/artifacts groups, app optional extras, pytest, tool-exec,
QuickJS, custom runtime fixtures, optional provider modules and browser/desktop
automation. Do not copy the producer's 87-distribution development environment.

The 64 dependencies consist of **59 registry wheels and five VCS wheels**:

| Production source | Exact input |
| --- | --- |
| Core | `2.0.2`, registry `amplifier_core-2.0.2-cp311-abi3-win_amd64.whl`, SHA-256 `a784e9f0382c471794e429818d9e55360625523bc62c54cae56be172364c936c` |
| Foundation | `c716df3cea84b2539aabdfea58a69ae676a1e853`, repository root |
| Session naming | Same Foundation commit, `modules/hooks-session-naming` |
| Memory | `5ac75ac85b9972e06936630e347023af107fd364`, repository root |
| UI Use / Context Intelligence | `5e3b08b8e06a6095314200476d387508ddb32f67` / `a80ce69d0e96e9a343501f238dcd867350dd9aa2`, respective repository roots |

For each registry package, use its exact selected version, Windows-compatible
wheel URL and SHA-256 in the frozen lock. All 59 have compatible wheel candidates
in that lock; this is metadata availability, not a download result. Accept
ordinary `cp313-cp313-win_amd64`, compatible older `cp3N-abi3-win_amd64` (for
example Core's `cp311-abi3`), and compatible pure Python wheels. Reject sdists,
free-threaded/other-platform/newer-interpreter wheels. Never compile Core.
Keep the private bootstrap uv **0.12.18** separate from the production
`uv==0.13.4` dependency pulled in by Memory.

The retained all-wheel packaging procedure, **not executed by this local join**,
is below. Its installed Git-origin boundary remains unresolved; it is **not**
the selected short path above:

1. Obtain the exact original app wheel through the supported artifact transfer.
   Verify its size/hash before use. Reuse the accepted production export and
   exact lock; do not resolve moving `main` or repeat the installed-host audit.
2. In an authorized build environment, collect the 59 registry wheels using the
   lock's URLs/hashes. Obtain or build the five VCS wheels at the full commits
   above, with `python -m build --wheel --no-isolation --outdir <new-dir>
   <exact-checkout-or-subdirectory>`. Record build-tool versions and build inputs;
   build dependencies stay outside the host wheelhouse. Do not run this command
   on the information worker's box. Preserve each wheel's licenses/notices.
3. Verify actual wheel metadata and compatible tags against the accepted
   production set: exactly 65 names/versions, no missing or extra distributions.
   Match the five build receipts to the exact repository/commit/subdirectory;
   check all registry wheel hashes against the lock and the app against its
   original digest. Create the reviewed inventory with these **actual** hashes,
   retain its review/source receipts, then run the builder above into a new output.
4. Before delivery, qualify actual offline installation with the existing
   hash-required/binary-only/no-deps commands. Verify installed provenance as well
   as the graph: local wheel installs may record archive `direct_url.json`, not
   the original Git commit. `app_component_graph`, update identity and selected
   dependency recovery consume this information. Do **not** manufacture Git
   provenance records, remove those checks or treat `pip check` as their proof.
5. Only after those checks and the missing ordinary-user bootstrap checks below,
   publish immutable prototype assets through the publication owner. Supply their
   real digests and one resolved paste. No capsule, digest, public URL or native
   support record is asserted by this plan.

**Remaining all-wheel inputs:** a materialized complete Windows wheelhouse and its
reviewed hash inventory/build receipts; verified installed-wheel provenance behavior;
exact managed Python patch supported by pinned uv; and eventual publication
locations. The accepted app wheel and native host graph already exist, but this
local packaging checkout does not receive binaries merely by reading their
retained identities. The first actual worker graph remains **unprepared**.
UI/onboarding work proceeds independently; a later app correction requires a
new exact app input, not relabeling this candidate.

After publication and review, the owner supplies one **fully resolved** paste,
not placeholders for the information worker to assemble. The following is its
reviewable template; its guards refuse until real exact values replace them:

```powershell
$ErrorActionPreference = 'Stop'
$BootstrapRef = 'EXACT_40_HEX_BOOTSTRAP_COMMIT'
$BootstrapHash = 'EXACT_64_HEX_SCRIPT_SHA256'
$PlanUrl = 'https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.json'
$PlanHash = 'EXACT_64_HEX_MANIFEST_SHA256'
if ($BootstrapRef -notmatch '^[0-9a-f]{40}$' -or $BootstrapHash -notmatch '^[0-9a-f]{64}$' -or $PlanHash -notmatch '^[0-9a-f]{64}$') { throw 'Obtain the resolved candidate paste from the trial owner.' }
$SetupText = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 -Uri "https://raw.githubusercontent.com/microsoft/amplifier-unified/$BootstrapRef/scripts/install.ps1").Content
$SetupBytes = [Text.Encoding]::UTF8.GetBytes($SetupText)
if ($SetupBytes.Length -gt 1048576) { throw 'Setup script exceeds its size budget.' }
$Hash = [Security.Cryptography.SHA256]::Create()
try { $ObservedHash = ([BitConverter]::ToString($Hash.ComputeHash($SetupBytes))).Replace('-', '').ToLowerInvariant() } finally { $Hash.Dispose() }
if ($ObservedHash -cne $BootstrapHash) { throw 'Setup script identity mismatch; no setup was run.' }
. ([scriptblock]::Create($SetupText))
Invoke-UnifiedPrototype $PlanUrl $PlanHash $false
```

This uses built-in Windows PowerShell 5.1 and .NET, without downloading another
package manager, running a `.ps1` through a policy override or installing a
machine-wide prerequisite. The downloaded script runs only after its reviewed
hash matches. Enterprise script/language restrictions remain authoritative:
if policy blocks this flow or `Add-Type`, stop and report the policy restriction,
not a bypass. In particular, after a `.ps1` is denied as scripts disabled, do not
retry its contents through `ScriptBlock`, dot-sourcing, `-Command`, `Unblock-File`,
elevation or execution-policy changes. Report the effective policy and ask the
organization's administrator for an approved software delivery route; do not ask
the information worker to weaken policy. The existing Restricted-policy refusal
is not cleared by an elevated CI runner's permissive policy.
Windows 11 x64, a normal non-admin token, usable PowerShell/.NET,
local fixed NTFS storage and permitted official HTTPS downloads are still
environment requirements. No silent proxy or certificate exception is added.

### Installation and restoration effects

| Effect | Owned location / recovery |
| --- | --- |
| Setup artifacts and helpers | New private `LocalAppData/amplifier-install-<random>` root, owner/SYSTEM DACL at creation. Existing roots are never adopted or ACL-repaired. |
| Python, host and caches | Root-local `python`, `python-bin`, `host`, `cache`, child `TEMP`/`TMP` in `temp`; host install uses `--no-index --require-hashes --only-binary :all: --no-deps`, followed by `uv pip check`. |
| App, history and ownership | Distinct root-local `app`, `shared`, `ownership`; capture `shared/projects`, import `shared`, empty trial `workspace`. No personal history/config/keys copying. Inherited environment/tool-specific OAuth and external files remain outside this isolation. |
| Launch and TLS | Private `Launch Unified.lnk`, exact venv Python and retained uv/Git paths. Normal CLI migration/TLS/ownership chain, numeric HTTPS loopback 8942, `serve --no-open`, then real owner `open`. Public CA export only; CurrentUser trust is the user's explicit certificate-viewer action, never an agent/installer import. |
| Failure and rollback | No old install, pointer or global launcher is replaced. Failed partial root is retained with stage-only private log; no mutation retry. Download-only retry is bounded. Use the previous launcher to roll back. |

Bootstrap directory pins permit WRITE sharing only for its own private tool
mutation parents (`root`, `python`, `python-bin`, `cache`, `temp`) so uv can atomically
rename staged downloads. ACL/handle identity validation and no-DELETE sharing
remain; traversal ancestors and downloaded/extracted artifact files retain their
strict sharing. The native offline fixture checks this exact rename boundary.

Setup reruns are **side-by-side**, not an in-place updater: each gets a new
private root and leaves previous roots intact. Do not repeat setup to relaunch;
use the printed private shortcut. Relaunch restores helper/root settings and,
when port 8942 is already occupied, attempts only real pinned owner entry; it
does not reconfigure, adopt or stop that listener. A refusal stays a refusal.
If setup failed, retain the printed root and stage before choosing a fresh retry.

Keep the foreground console open; **Ctrl+C** requests normal host shutdown and
the launcher retains the exact child until exit. Closing the browser does not
stop it. There is no sign-in startup or background-lifetime promise.
If TLS trust is missing, the launcher prints the public CA path and SHA-256,
explains the scope, and offers `REVIEW` to open the certificate viewer.
The user chooses Current User / Trusted Root only when policy permits; the
launcher rechecks trust before real owner entry. It never bypasses a warning
or requests a password/Hello PIN. Complete standard AI connections/model
onboarding in the web UI; inherited API keys are not automatically configured
connections, and a ChatGPT subscription is not OpenAI API billing.

Uninstall is deliberately data-preserving until chosen: stop the foreground
host, remove only the exact trial CA entry **yourself** if you imported it and
no longer need it (see the manual runbook below), then remove only the printed
private prototype directory when its data is no longer needed. No registry,
PATH, startup task or global tool restoration should be necessary. Trust removal
and data deletion are never automatic.

### Qualification still required

Portable capsule/command-model tests are not absent-prerequisite Windows proof.
Before giving a resolved paste to another user, retain these focused results:

1. Reuse unchanged native offline mechanism evidence from
   [run 42057508481](https://github.com/microsoft/amplifier-unified/actions/runs/42057508481)
   at `bf3e851a191907165f0a2e01b3ee6b8cf94fcd59`: both standalone fixtures
   under PS5.1/PS7, plus two PATH cases and observed fixture cleanup. Its tokens
   were elevated, Desktop policy Unrestricted and Core RemoteSigned. These are
   native mechanism/model checks, not downloads, ordinary-user policy or app
   installation. Changed capsule inventory/tag checks have portable evidence
   only; this join does not claim another native run.
2. On an authorized disposable ordinary Windows account, run the exact published
   paste with no developer tools on PATH. Check helper hashes/licenses, managed
   Python identity, complete offline host graph, spaces/Unicode and restart from
   the private shortcut without tool PATH.
3. Exercise bad hash, blocked proxy/policy, partial package failure and duplicate
   setup without replacing prior installs. Confirm listener conflict refusal,
   user-controlled CA trust, real owner entry and standard onboarding.
4. Confirm Stop/owned-descendant cleanup, Ctrl+C, saved-history relaunch and
   nonzero-host-error reporting. Keep native product/device/live-provider,
   clean-machine bootstrap and installer/background/Scheduler claims separate.

### Prepared real-prerequisite CI check — not dispatched

`.github/workflows/windows-prototype-cold.yml` is a **manual-only, single-job**
driver for the real `pinned-vcs` installation with `-NoOpen`. It is not the
offline command-model job and does not run any broad platform/app/runtime suite.
The original installer remains byte-identical to `d9752da50d4afe16c5fb1e67b499bae146a48cd2`.
The driver requires its exact reviewed checkout SHA, the published manifest URL
and its reviewed SHA-256. No moving `main`/`latest` asset or unreviewed candidate
is admitted.

The assembled candidate supplied for review is a **53,046,193-byte** capsule,
SHA-256 `c35e1a66b4de88aebc871bd8a6b025eb8475c0f6f525adc44455fd0688fa58f8`,
and a **443-byte** manifest, SHA-256
`a54ea968ba1c9f0bb1fb5f182edee3a3c53c707de8cddb3533bee2fae5935b57`.
Its ordinary managed Python selection is **3.13.15**, supported by the pinned
uv download metadata but not yet executed by this driver. These are assembled
input identities, **not publication or cold-install evidence**.

The planned release path is
`releases/download/windows-prototype-1c4-d9752da5/windows-prototype.json` and its
matching `windows-prototype.zip`. Both remain **unpublished planning locations**
until the publication owner supplies the actual immutable release/asset receipt.
The driver first makes one bounded unauthenticated HTTPS GET and verifies the
exact manifest bytes. Missing or different assets refuse before installation.
Do not dispatch merely because the plan names a URL.

**Runner readiness is a separate delivery dependency.** The unchanged installer
refuses Windows Server, build numbers below 22000, non-x64 processors and elevated
tokens. The current `windows-latest` workflow target must not be presented as a
successful-install route: hosted runner facts can violate that gate. Preflight
records product type, processor architecture, elevation and refusal reasons and
fails **before installation** on an ineligible host. A future authorized owner
must select a confirmed Windows 11 client x64 runner operating with an ordinary
non-elevated token; this local preparation does not provision or enroll one.
Do not change the installer, bypass its gate, demote a process or create a user
merely to obtain a green result.

After source review, publication/availability verification, conforming runner
selection and authorization of the one future run, the responsible owner can use:

```sh
gh workflow run windows-prototype-cold.yml --repo microsoft/amplifier-unified --ref REVIEWED_DRIVER_BRANCH -f expected_head=EXACT_REVIEWED_DRIVER_COMMIT -f manifest_url=ACTUAL_PUBLISHED_EXACT_MANIFEST_URL -f manifest_sha256=a54ea968ba1c9f0bb1fb5f182edee3a3c53c707de8cddb3533bee2fae5935b57
```

That is a future operator template, not an instruction for an information worker
and not a command executed during driver preparation.

1. The setup Python/Git belong only to the controller. The actual PowerShell
   5.1 installer child has **only System32 and Windows on PATH**, with standard
   `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH` retained. Runner tokens, provider keys and
   package-manager overrides are not passed. Discovery must show no Python,
   launcher, Git, uv or PowerShell 7 on this PATH. OS-provided curl/WSL may still
   exist and are reported without invocation. No global tool is uninstalled.
2. The unmodified installer performs the real private helper downloads, managed
   Python installation, 60 hashed wheel installs and five isolated Git builds.
   The driver verifies the actual 65-distribution metadata, Git origins,
   backend/runtime inputs, retained helper hashes/notices, absolute CLI version,
   actual COM shortcut target/arguments and a fresh child `launch.py --check`.
   It does not serve the app, open a browser, configure TLS, read provider keys,
   change trust or prepare a worker.
3. Each phase uses the maintained native Job owner, explicit controller
   `sys._base_executable`, finite deadlines and bounded private pipe capture.
   Receipts are saved **before** process admission. Deadline/forced descendant
   cleanup/truncated output/unknown custody cannot become PASS. Failed partial
   installation roots and private diagnostics are not automatically removed or
   replayed. CI runner disposal is not a cleanup check or durable log retention.
4. Successful disposal is limited to the one newly observed direct LocalAppData
   install root after native owner/ACL/reparse validation, unchanged file
   identity, zero retained Job processes and a separate process-absence check.
   No prior root is adopted or deleted. Only bounded fixed-field `result.json`
   is uploaded; private root addresses, process IDs, SID and raw logs are not.

This is a prepared future CI check, not a completed installation. An elevated or
Server runner is an **explicit prerequisite refusal**, not a weaker PASS with
disclaimers. A conforming runner can still have permissive execution policy and
developer tools outside the child PATH; **restricted-PATH CI does not establish
a pristine machine**.
Execution-policy refusal stays failure; no bypass, alternate script route,
unblock, elevation or policy mutation is permitted. Native PS5.1 grammar and
real installer behavior remain pending until the reviewed driver actually runs.

## Conditional foreground trial runbook

**Proposed instructions, not yet cleared for a user trial.** Commands below are
source-checked against `4f59da50639ad80c87f9201736f58fecddd0cf7d`; that is a
candidate, not public-activation acceptance. The Windows trial owner must supply
an exact qualified commit with owner-browser entry activated before use.
Do not substitute `main`, a moving branch, or the current public release.
The source-checked `4f59` checkpoint deliberately refuses browser entry. Later
integration candidates include the public existing-only credential reader, but
that source change is not foreground CLI or device acceptance. Never bypass a
refusal, relax ACLs, disable ownership, or put the control token in a browser URL.

This is an ordinary-user **foreground** path for Windows 11 x64, Windows
PowerShell 5.1 or PowerShell 7, and Edge or Chrome. It needs no WSL, administrator
terminal, execution-policy bypass or Task Scheduler registration. Foundation
PR456 is merged at `a49c22f0f2e7ad4cee954e3091cd115f7d25d41f`; its passing
mechanism/consumer checks do not qualify the combined Unified app.

### 1. Check prerequisites and install the selected checkout

Have Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) and
ordinary, non-free-threaded **CPython 3.13 x64** available. A system `py` default
of 3.15 can remain installed: select `py -3.13` when using that launcher, or
`uv venv --python 3.13` as below. Do not use an unqualified `python`, install a compiler
to work around missing wheels, or change the system Python.

Run each block in order in the same ordinary PowerShell window. Stop on any
failure. Assignments and invocations are separate statements; these examples
do not require PowerShell 7 syntax or venv activation scripts.

```powershell
$ErrorActionPreference = 'Stop'
git --version
if ($LASTEXITCODE -ne 0) { throw 'Git is required.' }
uv --version
if ($LASTEXITCODE -ne 0) { throw 'uv is required.' }
$QualifiedRef = 'MANAGER-SUPPLIED-40-HEX-COMMIT'
if ($QualifiedRef -notmatch '^[0-9a-fA-F]{40}$') { throw 'Obtain the qualified trial commit first.' }
$Source = Join-Path $HOME ('amplifier-unified-windows-source-' + [guid]::NewGuid().ToString('N'))
git clone --no-checkout https://github.com/microsoft/amplifier-unified.git "$Source"
if ($LASTEXITCODE -ne 0) { throw 'Source download failed.' }
git -C "$Source" checkout --detach "$QualifiedRef"
if ($LASTEXITCODE -ne 0) { throw 'Qualified checkout failed.' }
$ActualRef = git -C "$Source" rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ActualRef -ne $QualifiedRef) { throw 'Candidate identity mismatch.' }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Source '.venv'
uv venv --python 3.13 "$env:UV_PROJECT_ENVIRONMENT"
if ($LASTEXITCODE -ne 0) { throw 'Trial environment creation failed.' }
$Python = Join-Path $Source '.venv\Scripts\python.exe'
$Unified = Join-Path $Source '.venv\Scripts\amplifier-unified.exe'
& "$Python" -I -S -c "import sys,sysconfig,struct; assert sys.platform=='win32'; assert sys.implementation.name=='cpython'; assert sys.version_info[:2]==(3,13); assert struct.calcsize('P')==8; assert not sysconfig.get_config_var('Py_GIL_DISABLED'); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Requires ordinary CPython 3.13 x64.' }
uv sync --project "$Source" --python 3.13 --locked --no-dev --no-editable
if ($LASTEXITCODE -ne 0) { throw 'Candidate installation failed; do not launch.' }
```

This installs non-editably into this new checkout's venv, not the global uv
tool environment. `--locked` preserves the checkout's host dependency resolution;
the project's binary-only Core rule still applies. It does not freeze dynamic
worker/bundle downloads. Record the exact checkout and the trial owner's resolved
host/worker evidence; a version string or newer package is not equivalent.
Do not run global resync/reset or the no-argument background launcher.

### 2. Create private trial state before writing trial payloads

`--data-dir` alone is **not isolation**. Use a new trial root on local fixed
NTFS storage under your real profile. Do not change `HOME` or `USERPROFILE`.
The installed Windows directory helper creates owner-private directories at
creation and validates ancestors; `New-Item` followed by an ACL repair is not
equivalent. An unsafe ACL, junction, network/redirected profile or non-NTFS path
must fail. Do not weaken it; ask the trial owner for a supported location.

```powershell
$Trial = Join-Path $HOME ('amplifier-unified-windows-trial-' + [guid]::NewGuid().ToString('N'))
$Provision = @'
import sys
from pathlib import Path
from amplifier_web.windows_files import directory
root = Path(sys.argv[1])
if root.exists():
    raise SystemExit("Choose a new trial root; existing state was not changed.")
with directory(root, owned_root=root, create=True):
    for relative in ("app", "shared", "ownership", "shared/projects", "export"):
        with directory(root / relative, owned_root=root, create=True):
            pass
'@
$Provision | & "$Python" -I - "$Trial"
if ($LASTEXITCODE -ne 0) { throw 'Private trial provisioning failed; do not continue.' }
$env:AMPLIFIER_WEB_HOME = Join-Path $Trial 'app'
$env:AMPLIFIER_HOME = Join-Path $Trial 'shared'
$env:AMPLIFIER_SESSION_STATE_HOME = Join-Path $Trial 'ownership'
$env:AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH = Join-Path $Trial 'shared\projects'
$env:AMPLIFIER_UNIFIED_IMPORT_HOME = $env:AMPLIFIER_HOME
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --version
if ($LASTEXITCODE -ne 0) { throw 'Installed launcher check failed.' }
$Workspace = (Get-Item -LiteralPath 'C:\path\to\existing\trial workspace').FullName
if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) { throw 'Choose an existing workspace directory.' }
Write-Output "Candidate: $ActualRef"
Write-Output "Trial root: $Trial"
Write-Output "Launcher: $Unified"
```

Replace the workspace placeholder with an existing, disposable local workspace
you intend the trial to access, preferably containing spaces and Unicode. This
is not a sandbox: tools can use their granted file/network access.
The roots have distinct owners:

| Setting | Trial location and meaning |
| --- | --- |
| `AMPLIFIER_WEB_HOME` / `--data-dir` | `app`: Unified app configuration and state |
| `AMPLIFIER_HOME` | `shared`: shared settings, keys location and native transcripts |
| `AMPLIFIER_SESSION_STATE_HOME` | `ownership`: Foundation execution ownership, **not transcripts** |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` | `shared/projects`: capture location |
| `AMPLIFIER_UNIFIED_IMPORT_HOME` | `shared`: legacy import scan root, not the personal `.amplifier` |

The workspace's `.amplifier/settings.yaml` and `settings.local.yaml` still apply.
Normal startup also runs additive settings migration, which can write workspace
settings from legacy `.amplifier-unified` files. Review these inputs or use a
disposable workspace copy; do not claim existing workspace files are untouched.
Inherited environment credentials, external OAuth stores, Git configuration,
proxy settings, caches and tool-specific homes are not isolated by these roots.
Do not copy personal shared settings/history or create a new `keys.env` for this
trial. Use an existing provider API key already available to the host environment
first; never print it, paste it into chat or place it in a command argument.
An OpenAI API account/key with API billing is distinct from a ChatGPT subscription.
If no suitable credential exists, leave the live-provider check pending and use
the app's connection UI only after its native credential path is qualified.

### 3. Set up TLS and review the public CA

Keep all connection options **before** the subcommand, as the CLI parser requires.
Persist the dedicated numeric loopback endpoint before certificate creation:

```powershell
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set port 8942
if ($LASTEXITCODE -ne 0) { throw 'Port configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set bind '[127.0.0.1]'
if ($LASTEXITCODE -ne 0) { throw 'Bind configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set public_origins '[https://127.0.0.1:8942]'
if ($LASTEXITCODE -ne 0) { throw 'Origin configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls
if ($LASTEXITCODE -ne 0) { throw 'TLS setup failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls status
if ($LASTEXITCODE -ne 0) { throw 'TLS inspection failed.' }
$CaFile = Join-Path $Trial 'export\trial-ca.pem'
$CaPem = & "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls export
if ($LASTEXITCODE -ne 0) { throw 'Public CA export failed.' }
$CaPem | Set-Content -LiteralPath "$CaFile" -Encoding Ascii
& "$Python" -I -c "import sys; from pathlib import Path; from cryptography import x509; from cryptography.hazmat.primitives import hashes; print(':'.join(format(b,'02X') for b in x509.load_pem_x509_certificate(Path(sys.argv[1]).read_bytes()).fingerprint(hashes.SHA256())))" "$CaFile"
if ($LASTEXITCODE -ne 0) { throw 'Exported CA inspection failed.' }
```

Require `TLS listener: ready` and compare the exported certificate's SHA-256
fingerprint with `setup-tls status`. This hashes the X.509 certificate, not the
PEM file bytes. Explicit ASCII avoids PowerShell 5.1's UTF-16 redirection default.
Only the public CA is exported; never export `ca.key`, `leaf.key` or auth files.

**Security-sensitive user action:** only after the fingerprints match, decide
whether to trust this trial CA for your Windows account. A trusted root can
authenticate certificates it signs; this is not merely dismissing a warning.
If your device policy permits it, you may run the following yourself in the
same ordinary-user shell. An agent must not perform this trust-store change.

```powershell
$ImportedCa = Import-Certificate -FilePath "$CaFile" -CertStoreLocation Cert:\CurrentUser\Root
$TrialCaStorePath = 'Cert:\CurrentUser\Root\' + $ImportedCa.Thumbprint
Write-Output "$TrialCaStorePath"
```

Record that exact store path for later user-controlled removal. The store's
Thumbprint identifies the entry; it is not the SHA-256 verification above.
Do not use LocalMachine trust, change organizational policy or bypass a browser
certificate warning. If trust is prohibited or the browser still rejects HTTPS,
stop browser entry and retain that limitation. `TLS listener: ready` proves
loadable local material, not browser trust or authenticated chat.

### 4. Start the foreground host and open the owned browser

Before serving, open a child PowerShell window that inherits these process-only
environment settings. It will be used for `open`; it does not inherit `$Unified`.
Keep the printed absolute launcher path.

```powershell
Start-Process powershell.exe -ArgumentList '-NoProfile', '-NoExit'
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --workspace "$Workspace" --port 8942 --bind 127.0.0.1 --public-origin https://127.0.0.1:8942 --no-open serve
```

Keep the first window running. This is the normal CLI startup chain, including
the repair lock, settings migration, workspace configuration, TLS loading and
host ownership. Do not call internal server constructors to skip a failed stage.
Do not stop an unrelated listener if port 8942 is busy. A listening message proves
socket binding, not browser authentication, first-worker preparation or chat.

**Only with the trial owner's qualified browser-activated commit**, in the child
window set the printed absolute launcher path, then invoke the real CLI:

```powershell
$Unified = 'C:\absolute\path\to\trial-source\.venv\Scripts\amplifier-unified.exe'
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --port 8942 --bind 127.0.0.1 --public-origin https://127.0.0.1:8942 open
if ($LASTEXITCODE -ne 0) { throw 'Owner browser entry failed; retain the refusal.' }
```

Do not use a globally installed launcher, a fresh shell without the trial
environment, a raw URL/token handoff or a password/Hello-PIN workaround.
The launcher requires app-owned leaf-pinned HTTPS before reading/sending its
control credential. If entry is disabled, that is an expected qualification
fence, not permission to bypass it.

### 5. Record what actually works

After entry and the native configuration path are qualified, open **Settings →
AI connections → Connect AI**. Choose the intended API service and **Use the key
already on this host**, save/check the connection, and choose a supported model.
Select that connection/model for the trial conversation. A key in the environment
alone is not a saved connection or a working model; retain any setup failure.
These checks contact the provider and may use its API allowance.

Use the existing trial workspace and selected default bundle.
Check a first response and a small file-backed task with UTF-8 output
attachment/readback. Record the selected provider/model and resolved worker graph
without secrets. Runtime download/setup can take minutes; startup alone is not
default-bundle or live-provider acceptance.

Test **Stop** while owned descendants are active; confirm cleanup, not just a
stopped spinner. In the serving window, **Ctrl+C is ordinary foreground shutdown**,
not a product failure. Wait for shutdown before restarting with the same roots
and command. Reopen with the same launcher and verify saved history plus a second
turn. Do not start a competing host or delete ownership state to force recovery.
Closing this foreground terminal stops the host; background lifetime is not
being tested.

After the trial and shutdown, remove only this trial's CA if you no longer need
its trust. Closing PowerShell or deleting trial files does **not** remove trust.
This is another security-sensitive **user-only** action. In the original shell,
inspect the exact entry and its thumbprint first; if the variables are gone,
restore `$TrialCaStorePath` from the exact path you recorded, not a subject search.
Do not remove certificates by wildcard or name:

```powershell
Get-Item -LiteralPath "$TrialCaStorePath" | Format-List Subject, Thumbprint, PSPath
Remove-Item -LiteralPath "$TrialCaStorePath" -Confirm
```

Confirm removal only after matching that entry to the trial import. Keep the
trial state for diagnostics until the trial owner has reviewed the result.

| Can be prepared now | Still requires exact acceptance |
| --- | --- |
| Source review and this conditional runbook | Native installed cold startup, ownership, Stop/descendant cleanup and restart checks for the selected candidate |
| A separate checkout after the owner selects its ref | Public CLI/browser entry activation and the rendered owner grant-to-cookie flow |
| A planned ordinary-user device trial | Actual Windows profile/ACLs, Edge/Chrome trust, AI-account connection, default bundle/tools and live-provider response |
| Foreground-only scope | Installer, no-argument background launcher and Task Scheduler lifecycle; clean-machine bootstrap remains separate |

In-app workspace creation/starters, publishing and cross-host transfer are outside
this initial trial. Unsupported controls must report unavailability, not bypass
retained fences. Synthetic native CI can qualify scripted responses without paid
calls; it cannot establish these device/live-provider observations. No native
trial, trust import or live-provider execution is claimed by this documentation.

## Original design assessment

Source assessment: Unified `7b9358bf6443cc003edc1f3c380da8d01a8c2f5e`,
October 31, 2026. Recheck source and resolved upstream revisions before implementation.
The [current setup guide](../../README.md#install)
continues to cover Linux, macOS and Windows through WSL.

## Intent and boundary

Requested direction:

> Let's design and document the more turnkey approach for vanilla Windows
> installation, fixing whatever is missing to make it work with Windows along
> with the currently supported environments.

Interpretation: a normal Windows user starts in PowerShell, installs Unified
without WSL, and reaches a useful authenticated conversation without selecting
Python, installing a compiler, or learning service management. Preserve existing
Linux, WSL and macOS behavior. This is a native **backend** deployment of the
existing web app, not the planned Windows desktop client.

The triggering report showed `uv tool install` selecting CPython 3.15 and
compiling PyYAML 6.0.3, then failing for lack of Microsoft C++ Build Tools.
That is user-supplied evidence, not a Windows reproduction from this assessment.
Selecting 3.13 avoids that interpreter-selection problem; it does not fix the
runtime blockers below. Installing Visual Studio is not the proposed solution.

## Recommended experience

The authentication/lifetime policies below define the proposed experience.
Implementation details and platform qualification remain pending.

1. Run one PowerShell bootstrap as the signed-in user. It explains what it will
   install and where; no administrator terminal or machine execution-policy change.
2. Reuse valid helpers or obtain user-local uv and Git, then a managed ordinary
   CPython 3.13. Do not invoke the `python` Store alias or change system Python.
3. Verify a stable release and its platform support record. Install the host and
   prepare the default worker graph without native compilation.
4. Register and start a least-privilege, per-user logon task. Verify the expected
   authenticated host identity before opening a browser.
5. Authorize that browser from the owner's launcher, then connect an AI account
   in the app. Package installation, runtime readiness and first response remain
   separately reported states.

Initial qualification target: Windows 11 x64, built-in Windows PowerShell 5.1
and PowerShell 7, Edge and Chrome. Windows ARM64, Windows 10 and other Python
versions require their own results; none are silently excluded from future work
or inferred supported from the universal Unified wheel.

### Bootstrap contract

Add `scripts/install.ps1` only with its executable Windows checks. It is a thin
bootstrap; retain lifecycle, diagnostics and recovery in Python owners.
Use `Invoke-WebRequest` with explicit failure handling, TLS verification and
bounded downloads. Neither separate curl nor Bash should be required.
Publish a review-first download/run path beside any one-line convenience form.
Do not publish a command pointing to a script that does not yet exist.

Detect actual OS and interpreter architecture, not only the current shell's
architecture. Resolve latest once to an exact stable release; support explicit
version, no-open, verbose and anonymous-public-download options equivalent to
the existing installer. Reject unsupported tuples before replacing a working
installation. Private logs identify the failed stage and one next action.

Use the existing uv installation when valid. Otherwise obtain an official
Windows uv artifact with pinned identity/integrity checks. While Git sources
remain dependencies, reuse valid Git or provision verified user-local Git from
its official distribution, including license notices and an exact receipt.
Do not silently install a machine-wide Git package or require winget.
Record provenance and constrain archive extraction to the owned directory.
Refresh only the child/current process PATH; preserve personal Git configuration.
An anonymous retry applies to that download process, not global account settings.

Request ordinary non-free-threaded Python 3.13 explicitly. Let uv manage it
alongside any system Python. Use absolute helper and launcher paths so no terminal
restart is needed. Do not rely on whichever `python` happens to resolve first.

Verify the wheel against the release checksum before installation. A checksum
served beside an artifact is integrity evidence, not an independent signature.
Preserve the current Core binary-only rule. Qualify all default host/worker
native dependencies and fail with a supported-package explanation rather than
trying MSVC/Rust builds. Pure-Python Git packages may still need packaging.

Current manifests follow component branches. Preserve that update policy:
record the exact resolved host and worker graphs, qualify candidates before
activation, and retain the prior qualified generation for rollback. Do not
convert a one-time tested revision into an undocumented permanent pin.
For initial installation, distribution-owned constraints or a verified wheel
set may reproduce a qualified graph; define and check that artifact before
advertising reproducibility. A top-level wheel alone does not freeze Git dependencies.

Re-running setup must reuse the same data identity, refuse an unmanaged startup
entry, and retain chats, settings and credentials. Failed installation must not
reset user data. Removal unregisters only owned startup/program files; data
removal remains a separate explicit action.

## Platform boundaries

### Authentication that works with Windows Hello accounts

Accepted policy: the authenticated owner's Windows session can authorize a local
browser through the launcher. Identify the owner by process-token **SID**, not
display name, environment variables or a guessed email address.
Do not require a Hello PIN in a password form: it is not an account password.
A `LogonUser` password-only path would not cover all passwordless Microsoft
and Entra accounts.

The owner launcher authenticates using the private existing local control
credential and requests a distinct short-lived, single-use browser grant.
Bind it to the host/data identity and exact origin. Store a verifier, consume
atomically, and exchange for the existing HttpOnly session cookie.
Do not place the long-lived control credential in a browser URL.
Design the grant handoff to avoid request/access logs, referrer leakage and
persisted browser history; strip transient browser material immediately.
Qualify CSRF, login-CSRF, expiry, replay and cross-origin rejection before use.

Loopback alone is never authentication. Preserve Host/Origin checks, API
authorization, TLS policy and browser-cookie protections.
The current launcher implementation requires an app-owned, private-read leaf
certificate pin and HTTPS **before reading or transmitting the control bearer**.
Validating a health JSON response after sending the bearer is too late: another
local account could occupy the port while the host is stopped. This is a
listener-authentication requirement, not a new Windows Hello challenge.
This delegates existing OS-session authority; it does not prove a fresh Hello
challenge. A new native Hello challenge is a different product/security promise.
Keep PAM authentication unchanged for existing POSIX deployments.

The owner can end every browser session with `amplifier-unified browsers sign-out`
(see the deployment guide). All browser session checks go through one function,
`auth.valid_session`, so that no check can ignore that sign-out.

The command and the running host are two processes, and the host looks at the
saved record every two seconds. A ticket or code from before a sign-out must
not open a session that outlasts it, also in that gap. Two rules give that,
with no lock between the two processes:

1. The command saves a second that is not earlier than the second in which its
   record was saved. When a write ends in a later second, it pauses for a
   moment and saves again with that later second (`auth.sign_out_all`).
2. For a ticket or code, the host reads the new session's second first and
   looks at the saved record after that. When the record has changed and the
   host has not applied it yet, the ticket or code is used up and opens no
   session (`auth.sign_in`). The host keeps the mark of a record only when it
   has read that record.
3. The host drops pending tickets and codes for every changed record, also
   when its second is the one already applied. A second sign-out in the same
   second therefore still drops a ticket or code that was made in between
   (`auth.refresh_sessions`). Open streams end only for a later second: a
   session that is valid at that point was made after the record's second.

A session that the host returns was therefore made before any record that is
saved later, so it carries no later second than that record, and the host
refuses it when it applies that sign-out. A session in the same second as a
sign-out is refused. The owner signs in again a few seconds later, with a new
ticket or code.

The host tells a new record from the file's time, size and identity, not from
its content. Every save puts a new file in place while the record before it
still exists, so two saves in a row never have the same identity. The command
checks this for each save: a save that the host could not tell from the record
before it is made again. After three saves that were too slow or not visible,
and after a save that is not confirmed although the record may have changed,
the command says that it could not confirm the sign-out and asks the owner to
run it again. It says that nothing was changed only when it looked at the
record before and after the attempt and found the same record.

A look at the record can fail for another reason than a missing record. Such
a look is never taken for "no record" and never for "unchanged". The host
then opens no session for a ticket or code. The watcher applies nothing, keeps
nothing, and says so once in the host log. The command never takes such a
look as proof of a finished sign-out or of an unchanged record. Without that
proof it says that it could not confirm the sign-out, unless a later save of
the same run gives the proof.

This holds while the PC clock does not go backward. One case is left open: the
host looks every two seconds, and it misses a change when two or more saves
between two looks end in a file with the same time, size and identity as the
file that the host saw last. That needs a file time that does not change
between saves and a reused identity. It is not measured on any file system.

When the host misses a change, that sign-out is not applied: sessions from
before it are not refused, and their open streams are not ended. Such a
session stays valid for every request until it expires, until the record
changes again, or until the host starts again. This is so on every system.
Two facts make the case narrower. The Windows private store accepts only a
local fixed NTFS volume. Tickets and codes exist on a Windows host only, so on
the other systems no ticket or code is part of the case.

A ticket or code that is made after a sign-out and before the host has applied
it is dropped as well. The owner waits a few seconds after the command.

#### Remote browser sign-in on a Windows host

A browser on another device cannot use the local launcher, and a Windows host
has no password form. The owner starts that sign-in at the PC:
`amplifier-unified browsers code` prints a code, and the owner types it on the
sign-in page that the other device shows. Terminal and companion grants are not
reused for this; they carry broader authority.

- **The code.** Ten symbols from 32 (no `0`, `1`, `I` or `O`): 50 bits. It
  lasts 120 seconds and works once. The host keeps only its SHA-256, and at
  most eight codes at a time. Input is upper-cased, and spaces and hyphens are
  removed. No other character is changed, so a look-alike is a wrong code.
- **Who can make one.** The command uses the same request as `open`. It pins
  the privately read leaf certificate before it reads or sends the control
  credential, and the host accepts that request from this computer only. The
  code is bound to the host instance, the data folder, the owner SID and one
  address: an exact HTTPS entry of `public_origins` that is not this computer.
- **Where it works.** The redeem endpoint has its own narrow check: HTTPS, an
  explicit `Origin` equal to the address the browser used, and that address an
  exact configured remote one. It does **not** look at the peer address. A
  Tailscale TCP forward connects to the host from `127.0.0.1` (measured), so
  the socket does not say where the browser is. The local ticket keeps its
  check that both socket ends are local. With the forward on, that check no
  longer tells a tailnet device from this computer: a client there can also
  send the local address as `Host` and `Origin`. It gains nothing by that. The
  owner endpoints still need the control credential from the owner-only folder,
  and the local redeem still needs a ticket that only the launcher can get.
  Loopback was never authority; another Windows account on the PC is in the
  same position.
- **Wrong codes.** No lockout and no delay. A lockout would let any device
  that reaches the address keep the owner out. A delay taken in turn would
  make the owner wait behind a flood of wrong codes. The numbers carry the
  guess resistance: at 10,000 guesses each second against eight pending codes,
  the chance of a hit in one code lifetime is about 1 in 100 million. A wrong
  code never removes a real one and never reaches the owner's command, which
  uses another endpoint. The host counts wrong codes and logs only the count.
- **The page.** Server-rendered, with no script, and it shows nothing from the
  request. It is sent with `Referrer-Policy: same-origin`, not `no-referrer`:
  from a `no-referrer` page a browser posts the form with `Origin: null`, and
  the redeem check refuses that. The redeem reply is `no-store`, `no-referrer`.
  The field and **Sign in** come directly after the heading and the error
  text; the help follows them. In clean-room Run G the help stood before the
  field, and with the error text **Sign in** was below the window edge at
  300 % scale. Measured with Chromium and the page's own stylesheet, in CSS
  pixels: in that window (1272 x 512) **Sign in** ended at 565 before and ends
  at 372 now; at 320 x 480 it ends at 384. With the error text, a window lower
  than 372 CSS pixels still needs scrolling. The page has no style of its own:
  the stylesheet belongs to the web client, and the page's policy allows no
  inline style.
- **The session.** The signed cookie from a code is marked remote
  (`"via": "remote"`), so that a rule for remote sessions only is possible
  later. Local cookies do not change. The lifetime is the configured one.
- **The control.** `amplifier-unified browsers sign-out` signs out every
  browser and drops every pending ticket and code.

Limits: there is no sign-out for one device. A person on the same network who
gets the owner to type the code into a look-alike page within 120 seconds can
use it; the command prints the exact address to open. One host process holds
the pending codes in memory, so a restart drops them and the owner makes a new
code. Not measured yet: a phone browser, and the whole journey on a real second
device. Linux, macOS and WSL hosts keep the password form and refuse both code
endpoints. The steps for the owner are in the
[deployment guide](../DEPLOYMENT.md#windows-host-reach-unified-from-another-device).

#### The next step for a browser on the PC that is not signed in

A browser on the PC that is not signed in gets the sign-in page, or a refusal
of its ticket. Both name one next step (`browser_entry.next_step`). The step is
a text of the host, and a text says only what this host knows.

- **The Start menu.** "Open Amplifier from the Start menu: Amplifier Unified.
  Amplifier then opens in your browser." Only when all of these hold. This
  host can give a ticket (it has its ticket store); without one it would
  refuse the entry's `open`. The
  request is from this computer: both socket ends and the name in use. The
  Windows task of this installation started this host. The data folder is
  `<root>\app`. That task's Python is in
  `<root>\tools\amplifier-unified\Scripts`, and its workspace is
  `<root>\workspace`; the entry itself tests these two values before it opens
  the browser (`prepare_background`). This installation's Start-menu entry
  exists (`windows_consumer_shell.listed`).
- **Every other Windows case.** "Open Amplifier again on the Windows PC that
  runs it." It names no way. The command `amplifier-unified open` works as
  written only on an installation by hand with the launcher on the PATH and
  the default data folder. A full command with both paths was decided against:
  it would put the Windows user name on a page that needs no sign-in.
- macOS and Linux keep "To open Amplifier, run this: amplifier-unified open."

Limits. The host keeps the task definition that it compared with the Task
Scheduler's at its start, and it does not ask the Task Scheduler again: that
starts a program, and a page without a sign-in must not start one. So the host
does not see a task that was changed, stopped or removed while it runs; the
entry then shows its own message. `listed` reads that the entry's file is
there, not what it starts. The host does not test that `open` will reach it,
for example after a change of the saved port or certificate while it runs. A
request that is not from this computer reads no file. A forward that the owner
made connects from this computer too; a device behind it that also sends this
computer's name can get the Start-menu sentence, which holds no path and no
secret.

The sign-in page belongs to the web client. The host puts the step into the
page's mark `__REOPEN__`, HTML-escaped, before any value of the request. A
page without the mark is not changed: until the client's page has the mark, it
keeps the client's own sentence, and the step is in the refusal texts only.

`amplifier-unified open` is the step, so on Windows its own failure messages
never name it or the Start menu. Their reader ran the command, or the launcher
ran it and shows its own message. `open` prints the host's reason for a
refused request; that reply came over the pinned connection from a host of the
same package version. It prints one fixed sentence for a reply without a
reason, for an unconfirmed reply, and when no host answered. The typed-code
command keeps its texts and its own next step.

### Background lifetime

Implementation choice for the accepted lifetime: an interactive-token Scheduled Task under the owner SID,
least privilege, with a user-logon trigger and no stored password.
The November 7 clean-install follow-up explicitly selected this sign-in
background host rather than an SCM service. It must not be advertised as an
always-on Windows service or as surviving sign-out.
Do not use LocalSystem or S4U to avoid account-password prompts.
Use absolute executable/data/workspace/shared-state paths and explicit quoting.
Configure indefinite execution, restart-on-failure and battery behavior
deliberately. Automatic logon startup must not open a browser.

**The task after a computer rename (measured November 15, 2026).** The app
registers the task with the owner SID in the principal and in the sign-in
trigger. Task Scheduler keeps the principal as the SID. It stores the trigger's
user as a name, `COMPUTER\user`, whatever spelling registration gave, and it
never rewrites that name: not when the account or the computer is renamed, and
not when the same definition is registered again. It refuses to register a
trigger user that does not resolve at that moment. After the computer's name
changes, Windows still starts the task at sign-in, and `LookupAccountNameW`
maps the stored name, and the old computer name alone, to nothing (error 1332).
Measured on Windows Server 2022 and 2025 and on Windows 11 ARM64 runners, and
in Windows Sandbox, which renames its guest at every boot.

The ownership check compares the whole definition with the one that the app
generates from the task's own launch record. For the trigger's user text it
accepts exactly three forms:

1. the owner SID as text;
2. a name that Windows maps to the owner's user account;
3. a name `authority\account` that Windows maps to nothing, when the authority
   alone also maps to nothing and the account part alone maps to the owner's
   user account.

Form 3 is the renamed computer. It is an equivalence for this one text, not
proof of who made the task. It also accepts a trigger that someone with the
owner's or an administrator's rights had set to another account of the same
account name, once that account's authority no longer exists (and the two
variants in which an account was also renamed or deleted). In each of them the
task still runs as the owner, with least privilege and the app's exact action:
the principal, the task name, the action and every setting stay an exact
comparison. A stored name that maps to nothing while its authority still maps
is refused, so the name of a deleted or renamed account is not accepted. A
lookup that Windows did not answer is not "no such name": the check then
refuses and says that it could not check.

Limits. A renamed Windows account is still refused: the stored name is the
account's earlier name, it maps to nothing, and the computer's name still
maps. Amplifier does not repair this by itself, and that is a decision
(follow-up B6, November 17, 2026; two independent reviews accepted it). The
check cannot tell the earlier name of this account from the name of another
account that was deleted, and each rule that accepts the first also accepts
the second. That holds for a private record of the name that the launcher saw
resolve: a program that can edit the task can put the recorded name back
after the name was another account's. The person gets the message below and
its steps: delete the task in Task Scheduler, restart the PC, open Amplifier.
A start without a task registers a new one when its own checks pass; the
chats and settings stay. Read from source, not seen on a PC: the complete
repair after a real account rename, and whether Windows still starts the task
at sign-in after such a rename. On a domain member, a local account with the
owner's account name makes form 3 refuse a domain owner. Domain-joined,
Entra-joined and offline PCs are not measured.

An installation whose launcher predates this rule is not covered. The launcher
runs the check before any app version starts, and an in-app update does not
replace the launcher. After a computer rename such a launcher still refuses
the task that holds the old name, with the earlier messages. The refusal
ends when the stored name maps to the account again (the PC gets its old name
back), and with one of two repairs: a launcher that has the rule, or a new
task. None of the three is measured on a PC.

- A launcher that has the rule. For an installation that the setup file made,
  only `amplifier_web.windows_launcher_refresh` replaces the launcher. A
  second setup run does not: on an installed PC setup offers to add missing
  Start-menu and Apps-list entries and to open the app (with a program file
  missing it offers nothing), and it does not install the program files
  again. The app starts the refresh by itself after an app update that
  changed a file that the launcher runs, when no work runs (see "The launcher
  follows the app"). After a rename such a launcher does not start the app,
  so then only a person can run the refresh, with the Python of the active
  app version; setup never runs it. The refresh has its own
  conditions (for one, it installs nothing when no newer app version is
  active). It reads the task with the active app version's ownership check.
  So after a rename the refresh ends at that read and changes nothing, unless
  the active app version has this rule.
- A new task. The person deletes the task in Task Scheduler, restarts the PC
  and opens Amplifier. A start without a task registers a new one when its
  own checks pass, and Task Scheduler stores the name of that moment. A
  launcher older than 0.98.6 can run the long first-start preparation at that
  start. The launcher is unchanged, so a later computer rename brings the
  refusal back.

An installation that is renamed while both its launcher and its active app
version predate the rule cannot update itself, because the app does not run.
Of the two repairs, only a new task helps there.

When the check does not confirm the task, the launcher says that it could not
confirm that the entry belongs to this installation, names the entry, and
gives the steps to delete it; a new start then registers a new entry when its
checks pass, with the saved port and settings. It does not say that the entry
was changed, because it cannot know that. The entry can belong to another
Amplifier installation of the same Windows account. The text says so, and
what that installation does not have without the entry: it does not start at
sign-in. Then: "If that is all right, delete the entry." The text makes no
claim that the other installation starts today, and it does not tell the
person to keep the entry or to go to that installation (before pull request
2270: "use that installation, or uninstall it first"). After an account
rename that installation's entry has the same cause; its start and its
uninstall stop in the same way, and deleting the entry is its repair too.
When Windows gave no answer about the
account, the launcher asks for a restart and deletes nothing. Start, Status
and Uninstall each have these two texts. Stop has one text for both causes:
to stop the app now, sign out of Windows or restart the PC. Before, they
named the internet connection, an unreachable service manager, or a Task
Scheduler that could not be read. A start at sign-in shows no window: the
task ends with an error, and the person sees the text at the next start from
the Start menu.

Work survives browser/terminal closure while the owner remains signed in.
This model does **not** promise execution after sign-out or before sign-in.
If that is required, design a separate headless service deployment, including
network credentials and ownership. Do not pretend a logon task provides it.

Integrate install/start/stop/restart/status/logs with existing lifecycle commands.
Task registration is not health: verify version, data identity, authenticated
response and actual task process. Preserve the single host/reset exclusion.
The implementation candidate records the actual interpreter PID, exact native
creation FILETIME, image, registration and random run identity in a private
receipt. Readiness joins that receipt to the live listener and leaf-pinned
authenticated health, then rechecks the run. `IRunningTask.EnginePID` is not the
application PID. Normal stop publishes a private request bound to that exact run,
allows aiohttp and the application to complete cleanup, and waits for the retained
process handle and scheduler instance to finish. A timeout is not permission to
call Task.Stop, force-kill a PID or start a replacement.

Task output is private diagnostic content, not a public/secret-free receipt.
The candidate keeps a 128 KiB UTF-8 tail, coalescing snapshot publication to at
most once per second plus final close; neither normal status nor installer logs
should copy its body. These changes are implementation candidates until their
native task/host and clean-account installation checks pass. Public activation
gates remain closed in the meantime.
An occupied port or unrelated task must never be adopted or terminated.
If policy blocks registration, explain the failure and offer explicit foreground
`serve` once that path itself works; do not silently elevate or report turnkey success.

### Storage and execution safety

Keep each mechanism with its existing owner; avoid a general portability
framework or duplicated conversation authority.

| Boundary | Required Windows behavior |
| --- | --- |
| Private storage | Owner/SYSTEM DACLs on app-owned secret storage and temporaries before secret writes; validate existing/custom roots. POSIX mode checks do not prove Windows confidentiality. |
| Contained file operations | Handle-based identity, reparse/junction refusal and replacement detection for workspace creation, attachments, outputs, themes and task storage. Retain bounded reads and configured access boundaries. |
| Locks and durable writes | Preserve shared host/exclusive repair locking, memory serialization, and Foundation single-writer/fence semantics. Flush and atomically replace on the same volume; never delete the destination first to bypass a sharing failure. |
| Worker transport | Bounded Windows-compatible pipe reads, explicit UTF-8 protocol bytes, separate logs, EOF and cancellation that cannot strand shutdown. Retain message-size limits. |
| Process lifecycle | Passive process-handle liveness, never Windows `os.kill(pid, 0)`. Job Objects contain owned workers/preparation descendants before tool execution; graceful cancellation precedes bounded termination. Unknown effects remain unknown. |

Python 3.13 provides Windows `os.fchmod` and special handling for new
`mkdir(mode=0o700)` directories. Do not misclassify `fchmod` as an unavailable API
at this Python floor. These APIs do not validate existing DACLs, containment or
directory durability. Inspect actual Windows guarantees at each boundary.

Resolve host venv interpreters as `Scripts/python.exe` on Windows and `bin/python`
on POSIX, using an existing owner/helper where possible. Do not rewrite intentional
Linux **guest** paths in remote test-runner protocols.
Job containment must not kill the detached update helper that must outlive the old
host. Validate nested-job restrictions and escape prevention separately.

#### Private config and credential boundary

The first filesystem increment covers `config/server.yaml`, `session-secret`
and `control-token`; it is not a claim that all application storage is adapted.
Pass the original expanded absolute Windows address to the native boundary.
Resolving it first can remove the junction/reparse component being inspected.
Keep app data, shared settings/native history and Foundation coordination roots
distinct and preserve their explicit overrides.

Pin and inspect each traversal ancestor before creating or opening its child.
Denying delete sharing protects against rename, but sharing alone does not
prevent reparse conversion through attribute access. Validate actual handle
identity, type, filesystem and ACLs. Treat the owner and narrowly identified
Windows system authorities as trusted; reject ordinary foreign mutation rights
on traversal ancestors and ordinary foreign read access to private state.
Do not silently rewrite an existing custom root's ACLs.

New private directories and temporary files receive their security descriptors
at creation, before content is written. Credential initialization publishes
without replacing an existing credential and reads the winner; concurrent
processes must converge on one value. Existing empty, malformed or unsafe
credentials fail rather than regenerate. Config replacement uses a flushed
same-directory temporary and never deletes the destination to bypass sharing
errors. Atomic visibility does not establish namespace power-loss durability.

Shared settings migration, provider OAuth files, TLS material and SQLite/WAL
storage remain separate privacy checks. Do not mark browser authorization
ready merely because its ticket protocol passes with synthetic credentials.

#### Owned process launch boundary

Python 3.13's documented `STARTUPINFO.lpAttributeList` exposes `handle_list`,
not `job_list`. An ignored dictionary key is not creation-time containment.
Do not launch the payload and assign it to a job afterward.

A bounded candidate mechanism may launch a trusted, isolated standard-library
guard with `-I -S` and no payload/application imports. It must exit on control
EOF or a finite startup timeout. The host retains a non-inheritable Job Object
with kill-on-close, verifies the exact guard process identity and membership,
then releases a single execution gate. The payload may start only afterward;
it receives exact argv, environment, cwd and binary streams without shell
reinterpretation. The guard must never inherit the job's lifetime handle.
Assignment or nested-job failure refuses execution, not an uncontained retry.

The current isolated guard implementation supports **CPython 3.13 only**.
Its trusted bootstrap executable must explicitly name the host-controlled
`sys._base_executable`: the standard Windows venv executable is a redirector,
so its initial process ID is not the interpreter child's process ID. Do not
weaken exact process-identity checks to accept it. A contained payload may still
use its own qualified venv after the guard is assigned and released. The narrow
asyncio I/O-finalization bridge is version-specific; newer Python versions
require separate qualification rather than an assumed `>=3.13` guarantee.

Observe process exit separately from pipe EOF: descendants can keep stdout
open. After graceful shutdown, or after a finite command exits successfully,
settle remaining owned descendants and verify zero active job processes before
claiming cleanup. Preserve failed/unknown cleanup ownership and external-effect
receipts. Job termination does not undo a provider request, file write or cloud
resource. Qualify this mechanism on Windows before integrating runtime callers.

Optional Linux controllers must not crash ordinary startup. Gate unavailable
actions explicitly; preserve retained resource records without launching, deleting
or silently repairing them. Local Linux Docker/Incus testing is not a native
Windows prerequisite. Remote Linux runners retain their own supported contract.
Publishing and task transfer require their own faithful Windows durability/locking
checks or explicit limitations; do not weaken their promises to make startup pass.

## Source-backed implementation dependencies

The following are observed source assumptions, not recorded Windows test failures.
References identify the assessed source; line numbers may move during implementation.

| Work and owner | Evidence at the assessed revision | Required increment and falsifier |
| --- | --- | --- |
| Unified startup/repair | `cli.py:214`, `reset.py:391`; `service.py:778`; `amplifier_publishing/publisher.py:253`; `amplifier_portability/protocol.py:37` | Portable shared/exclusive repair lock; isolate optional constructors. Fails if `serve` imports `fcntl` or skips ownership/durability. |
| Foundation session state | Locked Foundation `amplifier_foundation/session/shared_state.py`, `_ensure_supported` and writer acquisition; consumed at `runtime_worker.py:838` | Windows ownership/private storage/atomic publication backend through the existing API, plus cooperative release transport. Fails if two contenders write or crash recovery overwrites newer history. |
| Memory store | Locked memory `src/amplifier_memory/store.py:69`, `:612`; reached through `memory_consolidation.py:7` | Portable exclusive store lock and non-inheritable descriptors. Fails if host import requires `fcntl`, timeout mutates data, or a dead holder strands the lock. |
| Unified login/lifecycle | `auth.py:141`, `deployment_service.py:264`, `installation_health.py:37`, `app_updates.py:29` | Owner authorization and Windows task backend shared with health/update/reset. Fails if login requires PAM or task registration is reported as readiness. |
| Unified I/O/process/files | `runtime_worker.py:26`, `:1696`; `generation_leases.py:19`; `workspace_placement.py:151`; `outputs.py:45`; `task_resources.py:95`; `theme_library.py:173`; `managed_deletion.py:497`; `smart_tools.py:605` | Platform-specific implementations preserving containment, encoding and cleanup. Fails on Unicode corruption, junction substitution, unsafe liveness inspection or surviving owned descendants. |

Paths without a package prefix are under `amplifier_web/`. Dependency assessments
must use the exact release-resolution receipts, not a convenient sibling checkout.
The assessed lock records Foundation `8d26ece...`, memory `b011380...`, and
Context Intelligence `a80ce69...`; capture full IDs from `uv.lock` for each check.
Core 2.0.2 already lists `cp311-abi3-win_amd64` in that lock; an abi3 wheel can
support ordinary CPython 3.13. Native-wheel availability is not runtime qualification.

Audit the default tools/providers as well. A tool named Bash may require Git Bash
or need an explicit Windows implementation. Do not silently reinterpret shell
programs as PowerShell, install WSL, or remove default capabilities. Qualify a real
file-backed task through the exact installed bundle/module graph.

These changes advance optionality and faithful ownership/recovery under
[CB1–CB6](../../contracts/component-boundaries.v1.md) and
[AP2–AP6](../../contracts/agent-protocol.v1.md). No locked contract is amended by
this document. Changes to promised lifetime/authentication or deliberate feature
omissions must be resolved explicitly rather than hidden as compatibility work.

## Delivery order and independent acceptance

Each stage returns exact-source evidence. None is a substitute for the next.

1. **Safety and upstream mechanisms:** land the passive liveness fix; qualify
   Foundation writer/release and memory locks on Windows. Preserve POSIX behavior.
   Integrate upstream only after its merge/CI; do not publish downstream first.
2. **Host runtime:** portable repair lock, storage, optional-controller isolation,
   pipe transport, process trees and default tool behavior. Prove host start,
   first worker, file tool, Stop and recovery against combined candidates.
3. **Owner entry and lifecycle:** apply the accepted policy choices below; implement
   local browser authorization and user-logon task, including health, update,
   rollback and reset. An unchanged data root must remain the same identity.
4. **Bootstrap:** implement PowerShell setup with verified prerequisites and
   release artifacts. Exercise missing/preinstalled helpers, an incompatible
   system Python, partial install, bad checksum, proxy/policy failure and rerun.
5. **Platform delivery:** run the [native acceptance checks](../validation/release-acceptance.md#native-windows-candidate-checks)
   on a clean Windows account and the affected Linux/macOS/WSL regressions.
   Only then add a supported native-Windows command to the README.

Run focused Windows mechanism tests automatically on a real Windows runner;
mocked platform branches on Linux are regression coverage only. Follow the
[local-first release policy](../validation/release-qualification.md); do not add
every unrelated suite as a foreground gate. First support qualification is still
required for the new platform. CI runners with Python/Git preinstalled do not
prove a vanilla-machine bootstrap.

For multi-repository changes, use one isolated combined-source integration
environment as well as native Windows. Retain source revisions, dependency graphs,
artifact hashes, OS/architecture, account class, commands, results and limitations.
Keep private usernames, SIDs, paths, tokens and screenshots out of public evidence.

## Accepted policy choices

| Choice | Accepted policy | Consequence |
| --- | --- | --- |
| Is the signed-in owner OS session sufficient to authorize a browser? | Yes, through a narrow single-use launcher grant. | Supports passwordless accounts without asking for the OS password. A fresh Hello challenge would add a different security requirement. |
| Must work continue after Windows sign-out? | Not for the ordinary user-logon deployment. | Browser and terminal closure are supported; sign-out is a documented lifecycle boundary. Always-on execution needs a separately designed service mode. |

The accepted choices cover only owner-session browser authorization and
user-logon lifetime, not every implementation detail in this document.
No fresh Windows password or Hello challenge is required. A user-logon host starts
again on the next sign-in; unknown tool effects are never replayed.
Remote browser access and architecture coverage remain explicit qualification work,
not an implicit promise or a silent scope reduction.

## Qualification record

This document records the design, source audit and bounded qualification results.
The manual installation guide (`docs/DEPLOYMENT.md`) selects 3.13 in its
commands. The README offers the consumer setup file
for Windows 11 as an unsigned preview and states its limits: one stable
download link, the browser's hold on the unsigned file, three stops that setup
or the first start explains by itself (a sign-in that runs every program as
administrator, a folder path over the limit, security settings that stop the
certificate step), Windows 11 on an Intel
or AMD processor, one Windows account, the installation folder, and an
uninstall that asks whether to keep or to delete that folder (the two
questions, the three buttons of the first one, and the earlier question,
which always keeps the folder). `tests/test_installation_guidance.py` keeps
that section and the product's own names in agreement. Qualified native Windows
support is not claimed. Earlier results below
are historical evidence for their exact candidates, not current release acceptance.

The accompanying safety increment replaces Windows lease liveness signaling with
`OpenProcess(SYNCHRONIZE)` and a zero-timeout wait. Uncertain results retain storage.
Its focused workflow runs the real sentinel on Windows, Linux and macOS; no remote
workflow run was observed during this assessment.

Local verification on Linux with Python 3.13: **14 tests passed**, including a real
subprocess survival check and stubbed Win32 outcomes, using:

```sh
python -I -S -B -c "import sys, unittest; sys.path.insert(0, '.'); suite = unittest.TestSuite(unittest.defaultTestLoader.discover('tests', pattern=pattern) for pattern in ('test_generation_leases.py', 'test_installation_guidance.py')); result = unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
```

Local link targets, workflow YAML parsing and `git diff --check` also passed.
Independent source review found no actionable defect in the passive probe.

### Host mechanism implementation increment

The candidate adds Windows `LockFileEx` shared/exclusive repair locking and
bounded worker stdin reading, with explicit binary UTF-8 protocol output. POSIX
keeps its existing `flock` and asynchronous pipe reader. The repair lock file is
stable and never unlinked. A Windows reader uses one daemon thread and a
one-slot request handoff; cancellation stops delivery but does not interrupt a
synchronous OS read. The retained descriptor/thread ends on input/EOF or
worker-process exit. It is not a reusable connection pool.

Independent review found a hidden test dependency: child worker probes loaded
installed Foundation even when the parent used standard-library isolation.
History-only Foundation imports now occur inside the history-reading function,
and the real worker probe subprocesses also use `-I -S`. The focused workflow
includes the complete pipe and repair-lock tests on all three platforms.

Observed on Linux ARM64, Python 3.13.11:

- **37 tests passed** with `-I -S -B`, covering passive inspection, guidance,
  real subprocess lock contention/crash release, and real worker transport.
- **173 tests passed, 1 skipped, 54 subtests passed** in the focused runtime,
  shutdown, reset, installer, guidance and mechanism regression selection.
- **4 targeted delivery checks passed**, preserving passive history inspection,
  ownership checks and duplicate-input refusal after the import isolation.

A broader delivery-suite invocation exceeded its 120-second command budget;
no result is claimed for that invocation. The smaller named checks above ran
separately. Source review and these Linux results do not establish Win32 API
execution, Windows account/privacy behavior or clean-machine installation.

At this stage, Foundation and memory were separate upstream candidates. Their
subsequent merge and CI results are recorded below. The bounded Foundation
ownership increment is not checkpoint persistence or Windows cooperative handoff.
Optional publishing/transfer startup isolation must preserve retained ownership
and transfer fences, not replace unavailable controllers with empty state.
Owner browser authorization, user-logon lifecycle, process-tree containment,
general filesystem compatibility and PowerShell bootstrap remain unfinished.
Native Windows, macOS and WSL runtime execution remain unverified here.

### Subsequent bounded qualification

These results qualify the named increments only. They do not establish an
installed native Windows host or a clean-machine installer.

| Increment | Observed evidence | Remaining boundary |
| --- | --- | --- |
| Foundation ownership and advisory reads | [PR 455](https://github.com/microsoft/amplifier-foundation/pull/455), reviewed head `06a825db4732f7c540b51f1d095d499c67d92b0a`, merged as `99fdb524037fb998b9c36355315e66a3052b662f`. [Candidate CI 41218283216](https://github.com/microsoft/amplifier-foundation/actions/runs/41218283216) passed Windows and Linux Python 3.11/3.12/3.13. Each Windows job ran all 20 native cases, with zero native skips; 2548 total passed and 75 other skips. [Post-merge CI 41267999104](https://github.com/microsoft/amplifier-foundation/actions/runs/41267999104) passed all six Windows/Linux jobs at the merge commit. | Windows Server 2025 build 26100, not minimum Windows 10 or clean Windows 11. Checkpoint/fence mutation and Windows handoff remain unsupported. |
| Memory store locking/encoding | [PR 24](https://github.com/microsoft/amplifier-bundle-memory/pull/24), reviewed head `6820d5cc2cb7701ec9f965c493886a1e89ef22d5`, merged as `5ac75ac85b9972e06936630e347023af107fd364`. [Candidate CI 41215809263](https://github.com/microsoft/amplifier-bundle-memory/actions/runs/41215809263) passed all four Windows/Linux Python 3.11/3.13 jobs: 34 tests per job, no skips. [Post-merge CI 41268245374](https://github.com/microsoft/amplifier-bundle-memory/actions/runs/41268245374) passed all four Windows/Linux jobs at the merge commit. | Selected store mechanisms, not the whole installed runtime. |
| Unified private config files and owned processes | Reviewed local candidates retain lexical paths, handle-bound rename, no-replace credential initialization, exact interpreter identity and bounded pipe finalization. Focused checks: 24 file tests passed/13 native skips; 29 deployment tests passed/1 native skip; 34 process tests passed/11 native skips. | Linux control-flow and real-pipe checks are not native Win32 acceptance. No production process callers integrated yet. |
| Owner browser entry | The reproduced unverified-HTTP bearer disclosure is repaired in the local candidate. Independent real-socket HTTP/wrong-leaf/proxy/redirect checks pass; private credential adapters use bounded pinned-directory reads. | Browser activation still unconditionally refuses pending native credential/TLS and combined rendered-flow qualification. No passwordless-account acceptance is claimed. |

The initial Foundation [CI 41215809534](https://github.com/microsoft/amplifier-foundation/actions/runs/41215809534)
remains failure evidence: `MoveFileExW` replacement failed with access denied
while an advisory reader held the old file open. The repair uses
`FileRenameInfoEx` replace/POSIX semantics. The new native regression passes
release and successor publication while the old reader still returns the
complete original JSON. This evidence is not substituted for Unified's separate
private-file tests.

Native TLS storage/loading, Scheduled Task lifecycle, PowerShell bootstrap,
process caller integration and the native acceptance rubric remain separate
work. Publication of Unified still follows upstream merge/CI; isolated
combined-source testing does not require those merges.
An installed release does not contain local candidate changes merely because
its version is newer than the candidate's base.

### Reviewed local candidates, not native acceptance

The retained local increments now include:

1. **Private files and TLS:** fixed app-owned certificate/key paths, bounded
   reads, held file handles through OpenSSL loading, and a stable bounded TLS
   lock. Focused checks: 49 passed, 7 native skips; independent changed-risk
   review: 24 passed. Per-file publication is not pair-atomic. Partial material
   fails explicitly without automatic CA regeneration.
2. **Interactive Scheduled Task:** owner-SID identity, least privilege,
   live-definition ownership checks and private explicit-replacement backups.
   Executable and working-directory fields reject `%` because Scheduler expands
   those fields; encoded data/state paths retain literal percent characters.
   Focused checks: 98 passed, 2 native skips before the narrow repair; 21 focused
   repair cases pass. The CLI's 26 checks pass after forwarding the selected data
   root. Task state is not health; authenticated host/process correlation, logs
   and automatic managed restart remain unavailable.
3. **PowerShell bootstrap:** pinned, owner/DACL-validated traversal, exclusive
   extraction-root creation, immutable checked-file handles and correct
   PowerShell source quoting. Implementation verification and independent review each observed
   15 portable guards passed, 2 native skips. Neither PowerShell parsing,
   embedded C# compilation nor Win32 fixture execution has occurred here.
   Both release-support and activation gates remain closed.

The focused platform workflow selects the standalone file, process and installer
fixtures without importing installed application dependencies. Windows preflight
requires ordinary x64 CPython 3.13 and both PowerShell 5.1 and 7. A separate
four-dependency job selects configuration, TLS and service policy checks,
explicitly excluding Task Scheduler registration. Local verification of that
selection passed 145 tests, with 9 native skips and 1 deselection. YAML and
embedded Python checks passed. The following native runs supersede the earlier
unexecuted status, but do not establish full mechanism or host acceptance.

### Initial Unified native CI

Draft [PR 1431](https://github.com/microsoft/amplifier-unified/pull/1431)
published only the bounded committed mechanisms, not the remaining local
browser/runtime candidates. The failed runs below remain qualification evidence.

| Candidate and run | Observed result | Unresolved boundary |
| --- | --- | --- |
| `e824e014010b6280324416dab40c66457511509b`, [CI 41269236533](https://github.com/microsoft/amplifier-unified/actions/runs/41269236533) | Linux passed. All 11 native Windows process cases passed. Windows standalone had 3 failures, 1 error and 9 POSIX-only skips. Configuration/TLS had 151 passes, 2 failures, 1 skip and 1 deselection. macOS failed the cwd path-spelling assertion. | Inherited-file/config fixtures, junction command quoting, both PowerShell fixtures and macOS alias handling required repair. No aggregate native pass. |
| `1c57dacb41f3cfa45f7422e4c635880aff8c3ab0`, [CI 41270115320](https://github.com/microsoft/amplifier-unified/actions/runs/41270115320) | Linux and macOS passed. Windows configuration/TLS/service-policy passed: 153 passed, 1 generic TLS skip and 1 Scheduler deselection. Windows standalone ran 139 checks with 3 failures and 9 POSIX-only skips. | The inherited-owner assertion and both PowerShell fixtures still failed. New sharing access strengthened directory pins; native fixture lifetime and structural owner checks still require qualification. |

The repair requests real directory-list access so bootstrap directory handles
participate in sharing enforcement. The Windows PowerShell fixture removes an
inherited cross-edition module path only from its child environment. Private
ownership rules and both activation gates remain unchanged.

Ordinary private native-history persistence is separately proposed in Foundation
[PR 456](https://github.com/microsoft/amplifier-foundation/pull/456), candidate
`1cc7cd0dc0b082f4e61da5b15a5a2b5e138dbb4b`. Its initial
[CI 41269990061](https://github.com/microsoft/amplifier-foundation/actions/runs/41269990061)
passed all three Linux legs and failed all three Windows legs. The Windows
failures include existing fork/store paths with nonprivate creation and the
native warm-append bound. This candidate is not merged or qualified; the original
ownership/store merges above do not qualify these new history operations.

**Next native-CI dependency:** repair the demonstrated failures and verify their
named native cases. The original upstream ownership/store merge and CI
prerequisites above are satisfied; the new private-history dependency is not.
Until the standalone mechanism passes its own native checks, do not integrate
runtime process callers. After that boundary, preserve owner cleanup-failure
custody and generation leases, distinguish guard/payload/worker identities, and
qualify the runtime-sized stream limit. Detached restart and synchronous
standalone reset require their own lifetime boundaries; they are not drop-in
consumers of the asynchronous finite-process owner.

### Installed Linux consumer checks, October 31, 2026

One isolated Ubuntu 24.04 aarch64 DTU, ordinary CPython 3.13.9, exercised
non-editable installed candidate packages. Explicit Foundation, delegate and
memory overrides selected the candidate sources rather than the consumers'
upstream pins. This is combined-candidate evidence, not a published installation.

| Source | Tested identity |
| --- | --- |
| Unified | HEAD `9f94e5355e0e78be8796ce16160af1af30374a8f` plus dirty/untracked source, frozen as snapshot `4067404ec0897fd9c57f071009b58f75bf453a40` |
| Foundation | `06a825db4732f7c540b51f1d095d499c67d92b0a` |
| Memory | `6820d5cc2cb7701ec9f965c493886a1e89ef22d5` |
| Agent SDK and engine | `95877ef54cd8849f8f2066ef3e97ce4c8a1669af`; complete SDK/engine package trees match the SDK-declared `v0.25.0` revision `1da05aa81fa2239d9044b375f21f75ed0e46c89f` |
| App CLI | `631c4bdbcf3dcd2da20456ad6ccef386e778c101` |

Agent retained Core **2.0.1** in its own environment. CLI/Unified retained Core
**2.0.2** in another environment on the same DTU. A single Agent/Unified package
environment is unsatisfiable: the engine pins Core 2.0.1 and Unified requires
Core >=2.0.2. Do not relax either requirement to manufacture co-installability.

The final focused selection passed **341 test executions, zero failures and
zero skips**: 141 in the Agent environment and 200 in CLI/Unified. This includes
Foundation/memory checks in each environment, not 341 distinct tests. Earlier
setup and fixture failures remain retained; no production source was patched.
Nineteen same-process import audits found no candidate source shadowing, and
installed Python bytes matched their selected source packages.

The deterministic roundtrips saved/read UTF-8 memory through the real library,
resumed SDK history, preserved synthetic configuration bytes, and persisted CLI
user/tool/assistant content. Separate-process checks established live-owner
refusal, release, old-capability fencing and successor checkpoint readback
across the two environments. Independent verification repeated the two consumer
roundtrips and ownership sequence with fresh synthetic data, and checked all
208 exported evidence-file hashes before teardown. The owned DTU was removed;
unrelated environments were left untouched.

**Mocked boundaries remain explicit:** scripted provider, CLI first-run and
bundle-assembly seams, and fixture tool bridges to the real memory library.
The shipped memory tool/injector adapters were not mounted. Actual Foundation
loading of the memory bundle and its declarations passed separately. No
live-provider, rendered-browser, native Windows or full-default-bundle acceptance
is established by these checks.

A separate Windows x64/ordinary CPython 3.13 resolution passed for each default
package graph: Unified, Agent and App CLI. Native-only binary rechecks preserved
the selected identities; 24 unique native wheels across 91 selected registry
releases were available and hash-verified. Independent readback verified the wheels,
210 retained evidence files and 97 AMD64 PE members. PyYAML 6.0.3 has a matching
CPython 3.13 Windows wheel but none compatible with ordinary CPython 3.15.
Pure-Python/VCS packaging was not globally prohibited. Wheel availability does
not establish Windows execution, OS-wrapper behavior or optional/dynamic graphs.

Two independent reviews assessed the earlier evidence brief, not source or
DTU execution. Their requested consumer/provenance and native-wheel checks now
have the bounded evidence above. Their native runtime, security/lifecycle and
clean-install gaps remain open. Both activation gates stay closed.