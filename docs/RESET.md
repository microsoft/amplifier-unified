# Repair a broken installation

## In-app installation repair contract

**Repair installation** is separate from runtime dependency repair and selected
data reset. It supports only the verified generated Linux/WSL systemd user service
or macOS launch agent running from its owning uv tool installation. Native Windows
repair is unqualified and explicitly unsupported; the app offers no workaround.
Foreground/development hosts and customized or ambiguous service launches are
refused, not replaced.

`maintenance.installationRepairPreview` returns the exact host instance and label,
physical data directory, application-only scope, latest stable release version and
source revision, installed optional features, replacement paths and preserved
categories. A preview expires after ten minutes. It does not stop or install.
`maintenance.installationRepairConfirm` requires that preview's `previewId`,
`hostInstanceId` and `confirm:true`, through explicit user confirmation. Changed
targets and nonidle work reject before handoff; there is no delayed automatic
retry or force-stop. An existing pending update/restart/replacement must be
resolved through its own operation first.

The helper is dispatched once into a separate service-manager job using a stable
base interpreter and copied standard-library code outside the installation being
replaced. It prepares and probes an isolated fresh candidate from the exact
official wheel, verified publication checksum and source stamp **before** stopping
the current service. It preserves installed optional extras. Failed preparation
leaves the current host serving. The confirmed repair fence prevents new model,
configuration, update and scheduled work from entering during handoff.

After checking the original authenticated listener, service PID, data-directory
identity and exact service definition, the helper stops that service and obtains
exclusive data-directory and shared-tool repair leases. It backs up the old uv
environment, public launcher and exact application launch pointer. It replaces
only the application installation and deactivates that pointer; its previous
bytes and generation tree remain retained. A failed installation/probe restores
those exact paths where possible. An incomplete rollback is **unknown**, not a
successful restoration, and never restarts or repeats installation automatically.

**Conversations, credentials, settings, TLS files, workspaces, runtime caches,
downloaded sources, configured source selections and existing operation receipts
are not reset.** Unknown outcomes and Stop remain unchanged. Repair does not
reconcile an earlier update, revive a conversation, replay messages or reinstall
custom components. For deeper cache recovery, use the distinct CLI reset described
below after reviewing its broader scope.

One private `<data-dir>/installation-repair/<operationId>/operation.json` receipt
owns the operation's stage and outcome across browser disconnect and host
replacement. `maintenance.installationRepairStatus` accepts **either** `operationId`
or `previewId`; an exact unconsumed preview returns `{operation:null}`, never an
unrelated operation. With neither ID it returns the latest receipt. The initial
`maintenance.installationRepair` projection contains `{preview,operation}`.
The client saves the preview identity before confirming and reconnects by reading
status, never by resending a confirmation. Duplicate consumed previews return
their original operation without another helper dispatch.

The receipt's `helperJob` records the exact OS job label and cleanup owner.
Linux uses a transient user service with `Restart=no` and manager collection on
exit. macOS uses a one-shot submitted launchd job; its registration can remain
after exit and is explicitly retained for installation-owner review/removal of
that **exact settled label**. A registered job is not running/success proof.
Never relaunch a retained helper or remove an unknown live job to hide uncertainty.

`accepted`, `running` and `unknown` are not success. `reinstalled` reports package
replacement without verified readiness. `rolled-back` reports restoration, not a
fresh installation. `ready` requires installed package probes, passing doctor,
the managed service running, and authenticated successor health for the exact
target version/revision/data directory with a **different** host instance.
`reinstalled`, `serviceState` and `healthVerified` remain separate receipt fields.
A failed start leaves the verified new installation and recovery backup in place
without claiming ready. Receipts, plans and backups are private and are not
automatically pruned or replayed.

The candidate must implement repair protocol 1 and must not downgrade the serving
version. Its resolved distribution versions and source identities must match the
fresh final installation exactly; constraints alone are not that proof. A target
release predating this protocol is rejected before service stop. The successor
retains the repair admission fence until the helper records verified readiness;
an installed-but-unverified host keeps inspection and Stop available, not new work.

Focused synthetic checks establish admission, no-replay, ordering and rollback
boundaries. They do not establish native Linux/macOS service-manager qualification
or authorize live adoption/restart.

## CLI reset (deeper recovery)

Use `amplifier-unified reset` when an update was interrupted, installed packages
were edited, or damaged runtime caches prevent Unified from starting. It repairs
the supported **uv tool installation** on Linux, WSL and macOS.

```sh
amplifier-unified reset --dry-run
amplifier-unified reset
```

Reset shows its plan and asks for confirmation. Use `--yes` for unattended repair.
Run it as the account that installed Unified, with the same `UV_TOOL_DIR` and
`UV_TOOL_BIN_DIR` if those were customized. For a custom data directory:

```sh
amplifier-unified reset --data-dir /path/to/unified-data
```

## What it does

1. Resolves the latest published stable Unified release to an exact Git commit.
   Public release metadata is read without authentication first; GitHub CLI is
   optional. No model/provider requests are made.
2. Stops the generated systemd/launchd service for this data directory. Stop
   manually launched hosts and workers first; reset refuses detected running
   processes. Do not run other installers or start hosts during repair.
3. Moves the old uv tool environment aside and installs a completely fresh app
   and dependencies, bypassing uv caches and project configuration. This removes
   local changes inside the managed installation. Development checkouts are left
   alone; reset installs the normal uv tool rather than repairing a checkout.
4. Checks the new app's imports, bundled web assets, authentication library and
   worker bootstrap. If installation or validation fails, restores the old
   environment and launcher.
5. Retires Unified's `updates`, `runtime`, `foundation`, and `source-store`
   directories to a recovery backup. This clears active version pointers,
   downloaded source edits, worker environments and failed update candidates.
   Before retirement, converts legacy saved conversation module sources from
   verified managed-cache paths to exact Git repository/commit/subdirectory
   references. Both saved overrides and effective snapshots are covered; external
   local sources and existing Git pins are unchanged. The freshly verified
   installation prepares this conversion, not the installation being repaired.
   It clears only the saved update status in the application database, including
   interrupted-update fences; a database backup is taken first.
6. Starts the generated service again, then runs `doctor` and `service status`.
   It waits up to one minute for the configured app to respond and only says
   “Unified is ready” after both checks pass. Use `--no-start` to leave it stopped;
   both checks still run, but a stopped service is expected in that mode.
   Installation verification, saved update-state changes and a passing `doctor`
   qualify an intentional `--no-start` reset as successful without a listener.
   The next message can take a few minutes while worker caches rebuild.

**Chats, credentials, settings, TLS certificates, attachments, bookmarks, workspace
files and unrecognized data are preserved.** The separate `~/.amplifier` data and
shared session store are untouched. Optional desktop/TUI features can be enabled
again in Settings after this base installation repair.

This is installation recovery, not a factory wipe or a database salvage tool.
Invalid user configuration or a corrupt chat database may still need separate
repair. Custom/unmanaged service definitions require separate attention. External
shared source stores are not cleared: unset `AMPLIFIER_SOURCE_STORE` before
reset, and repair an externally configured store separately if necessary.

### Saved plans and in-app cache reset

The in-app **cache** reset uses the same pre-retirement conversion when it keeps
conversations. Runtime-only reset does not convert plans. Selecting conversations
as well retires their plans with their existing private backup instead.

Conversion does not select today's bundle defaults, drop disabled modules or
rewrite module configuration. It requires verified source metadata and a clean
checkout at the recorded commit. An unreferenced damaged cache does not block
reset. A referenced source that is missing, modified or unverifiable does block
cache retirement; reset cannot reconstruct that source honestly. The error (in
the CLI's private repair report) names the saved plan that needs attention.
Redirected private configuration paths refuse conversion. Reset does not impose
a separate plan-size limit; reads use the existing file's size.

Reset checks module declaration `source` fields, including nested agents and
spawn tools, and explicit root/agent context-file maps. A context-file path still
inside a retiring directory stops reset before cache moves: module provenance
does not prove how to reconstruct that resource. Opaque module configuration
(including `tool-skills.config.cache_dir`), prompts and log paths are preserved,
not reinterpreted as executable sources or rewritten by the host.

Pending configuration requests with those declared source/resource dependencies
must be resolved through their original operation first. Successful application
and cancellation remove the queue file; retained finished operation records are
history and do not block reset. A remaining queue file is not proof of completion.
Reset never rewrites a retained request or sealed receipt, clears its hold, or
replays the change.

All conversions are prepared before saved plans are changed. Original plan bytes
are retained in the CLI recovery directory or the in-app private backup. If plan
application or cache retirement fails before logical state changes, the changed
plans and already moved cache directories are restored. Once the in-app reset
begins catalog, history or settings publication, a failure may follow a committed
database change. It retains the completed filesystem changes and recovery backup,
reports failure and does not attempt a file-only undo or claim reset success.
This is not a fallback that runs code from reset backups.
After a successful reset, normal runtime preparation fetches the exact saved
source revisions; unavailable repositories or credentials can still prevent that
preparation.

## If the launcher itself is broken

Reset normally works even when application dependencies or an active update
receipt are broken. If the Python environment or reset module itself is missing,
run a fresh copy of the same dependency-free recovery script with system Python:

```sh
repair_dir=$(mktemp -d)
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/microsoft/amplifier-unified/main/amplifier_web/reset.py \
   -o "$repair_dir/reset.py" &&
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/microsoft/amplifier-unified/main/amplifier_web/reset_retention.py \
  -o "$repair_dir/reset_retention.py" && python3 "$repair_dir/reset.py"
```

Use Python 3.10+ for this recovery script. uv obtains Python 3.13 for the new app.
The script and its sibling retention policy use only the standard library, with
the same options and safeguards as the installed command. Keep both files from
the same reviewed revision together; no ordinary app dependencies are imported
into the emergency script. Saved-plan conversion runs only in the freshly
verified installation's isolated interpreter.

### Public downloads on a device with restricted GitHub sign-in

An account's organization policy can deny authenticated requests even when the
repository is publicly readable. Release checks try public access first. To also
bypass personal Git credential helpers, injected headers, and HTTPS-to-SSH URL
rewrites during repair, use:

```sh
amplifier-unified reset --anonymous
# Or append --anonymous to the standalone recovery script above.
```

This applies to the repair's downloads, including its Git dependencies. It does
not sign you out or change your Git configuration. For future background updates
and bundle preparation, install the service with the same download policy:

```sh
AMPLIFIER_GITHUB_ACCESS=anonymous amplifier-unified service install --replace
```

This command backs up the generated service definition and restarts Unified;
wait for current work to finish first. Use the same `--data-dir` and `--workspace`
as your existing installation if customized. Both systemd and launchd retain the
choice across service replacement. Set `AMPLIFIER_GITHUB_ACCESS=auto` with the
same command to restore normal Git configuration and authenticated fallback.
For a foreground host, set the variable on `amplifier-unified serve` instead.

Anonymous mode is for public sources. Private sources still require authorized
access and will fail in this mode. Personal Git proxy/CA configuration is also
bypassed; configure necessary transport settings through the process environment.
This is a download compatibility option, not a sandbox for arbitrary Git sources.
For a fresh installation, `scripts/install.sh --anonymous` applies this policy
to dependency downloads and the new background service as well.

To reinstall a specific trusted release wheel or Git revision instead of looking
up the latest release:

```sh
amplifier-unified reset --source /path/to/amplifier_unified-VERSION-py3-none-any.whl
```

Only use sources you trust: installing a package can execute its build code.
The source must provide the `amplifier-unified` distribution.

## Recovery backups

Reset prints the path to a private repair report containing the exact backup locations.
Package lists and service-manager details go into this report by default. Use
`reset --verbose` to also display technical details. During longer steps, reset
prints elapsed time so you can see it is still working.

`doctor` and `service status` provide short summaries by default; use `--verbose`
for technical details. Both return a nonzero exit code when a check fails. Service
status checks the running app’s version and data directory, not just whether a
background process exists. A failed startup check leaves the fresh installation
in place and reports that it is not ready; it does not claim a successful repair.

Retired runtime directories and a copy
of the database are under `<data-dir>/reset-backups/<id>/`. The old app environment
is retained outside uv's tool directory, under the sibling
`<tool-directory-name>-amplifier-unified-recovery/<id>/previous`. Failed
replacement environments are retained in that same recovery folder as `failed`.
The backup manifest records the exact path. Keeping recovery copies and the
installation repair lock outside uv's tool directory prevents them from being
mistaken for installed tools by `uv tool list` and `uv tool upgrade`.
Older versions used hidden `.amplifier-unified-reset-<id>` and
`.amplifier-unified-failed-<id>` tool-directory siblings. The manifest in
the data backup records the paths. Backups can contain credentials or private
conversation data; keep them private. They are not active runtime paths.

### One shared reset recovery point

After a **successful** CLI or in-app reset, Unified automatically retains its
recovery set and deletes older validated successful reset sets for that same
data directory. The two entry points share one keep-one policy, not one backup
each. A set includes every paired recovery folder, including the previous tool
environment outside the data directory. For in-app resets,
`backups/<id>/private-state.tar.gz` and `backups/reset-<id>/` are one set with
the same operation ID. Recovery covers the latest reset, not a separate point
for each previously selected component. Archive contents and restore format
are unchanged.

CLI reset commits success only after installation verification, saved update
state, restart and passing `doctor` and service-status checks. With `--no-start`,
verified installation and passing `doctor` count as a successful, intentionally
stopped reset. A failing doctor, installation, rollback, startup or readiness
check does **not** prune earlier sets. In-app success requires both the applied
reset stores and its terminal action outcome to be durably saved.

Reset ownership and monotonic successful-completion order live in
`<data-dir>/reset-recovery/state.json`; modification times do not choose the
retained point. Do not edit this ledger or member ownership markers. Pending,
failed, cancelled, interrupted or unverified attempts are preserved as unresolved
recovery evidence; a later success does not silently discard them. Legacy CLI
manifests lack a reliable successful-completion witness, and legacy in-app
archives cannot reliably be paired with retained originals. Those copies are
reported for explicit review, never paired by timestamps or automatically
deleted. Independent manual private backups, migration backups, current data,
workspaces and shared CLI history are not reset-retention candidates. Storage
keeps the newest manual private backup separately and protects reset members.

If deletion fails, Unified reports **reset succeeded; older-backup cleanup
incomplete**, preserves the new point and saves retry progress. It does not
claim exactly one set remains. Resolve the reported access/I/O issue, then retry
without resetting again:

```sh
amplifier-unified reset --data-dir /path/to/unified-data --cleanup-only
```

The equivalent in-app/agent action is `maintenance.resetCleanup`. The next
successful reset also retries obsolete-member cleanup. Cleanup is awaited, runs
off the in-app event loop and outside database/runtime/publishing locks, and
does not replay reset changes. Unresolved attempts and ambiguous legacy material
still need explicit owner review. If committing the retention ledger itself
fails after a healthy reset, old copies remain; do not promote the unverified
record by hand or assume cleanup retry can establish missing success evidence.

Do not restore an old update pointer by itself: it refers to matching retired
runtime directories. Keep recovery copies private.

### Linux: background service manager unavailable

Reset reconnects to your existing user service manager when a terminal is missing its login environment. If Linux reports “Failed to connect to bus” or “No medium found” and no user service manager is available, sign in directly as the account that installed Unified and retry. On a machine without a desktop login, an administrator may need to enable background services for that account with `sudo loginctl enable-linger ACCOUNT`, then have that user sign in again. Run reset as the original user, never with `sudo`. Reset leaves the installation unchanged when it cannot safely stop the service.

If release discovery fails, repair tries both the anonymous GitHub release API
and GitHub's public latest-release link before optional account access. The
error distinguishes HTTP failures (including reported rate limits), DNS,
timeouts and TLS certificate verification. It never disables TLS verification.
Use the private repair report for more detail.

If Git downloads work but release discovery is unavailable, you can bypass the
lookup with `amplifier-unified reset --anonymous --source git+https://github.com/microsoft/amplifier-unified@FULL_RELEASE_COMMIT`.
Replace `FULL_RELEASE_COMMIT` with the exact commit of a verified published
release; do not use a moving branch. This performs the normal repair, including
its backup and service restart. `--anonymous` alone does not bypass network,
certificate or rate-limit failures.

Public repository downloads automatically retry once without GitHub sign-in when
an account is rejected by organization SSO or authentication. The retry affects
only child downloads and leaves your saved sign-ins and Git configuration intact.
`--anonymous` remains available to force that mode immediately. Private sources
still need authorized access. This policy also applies to in-app update downloads;
see the [repository access contract](../contracts/repository-access.v1.md).
