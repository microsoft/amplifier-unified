# Repair a broken installation

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
   GitHub CLI authentication (`gh auth login`) and Git access to the repository
   are required. No model/provider requests are made.
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
   It clears only the saved update status in the application database, including
   interrupted-update fences; a database backup is taken first.
6. Starts the generated service again. Use `--no-start` to leave it stopped.
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

## If the launcher itself is broken

Reset normally works even when application dependencies or an active update
receipt are broken. If the Python environment or reset module itself is missing,
run a fresh copy of the same dependency-free recovery script with system Python:

```sh
repair_dir=$(mktemp -d)
gh api -H 'Accept: application/vnd.github.raw+json' \
  repos/microsoft/amplifier-unified/contents/amplifier_web/reset.py \
  > "$repair_dir/reset.py" && python3 "$repair_dir/reset.py"
```

Use Python 3.10+ for this recovery script. uv obtains Python 3.13 for the new app.
The script uses the same options and safeguards as the installed command.

To reinstall a specific trusted release wheel or Git revision instead of looking
up the latest release:

```sh
amplifier-unified reset --source /path/to/amplifier_unified-VERSION-py3-none-any.whl
```

Only use sources you trust: installing a package can execute its build code.
The source must provide the `amplifier-unified` distribution.

## Recovery backups

Reset prints the exact backup locations. Retired runtime directories and a copy
of the database are under `<data-dir>/reset-backups/<id>/`. The old app environment
is retained beside the uv tool environment as `.amplifier-unified-reset-<id>`.
Failed candidates are retained as `.amplifier-unified-failed-<id>`. The manifest in
the data backup records the paths. Backups can contain credentials or private
conversation data; keep them private. They are not active runtime paths.

Keep backups until you have checked the repaired app, then remove those exact
backup directories to reclaim space. Do not restore an old update pointer by
itself: it refers to the matching retired runtime directories. If repair fails,
the command reports failure and retains evidence rather than claiming success.
