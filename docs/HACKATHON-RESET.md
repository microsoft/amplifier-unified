# Fresh Unified install after the hackathon version

Use this for the older Unified installed from `bkrabach/amplifier-unified` when
you want to discard its partial setup and start Unified fresh. Run it on the
computer hosting Unified, as the same user who installed it. On Windows, run
the Linux steps inside the same WSL distribution.

This removes **Unified's service, Python tool installation, and private app
directory**. It deliberately keeps `~/.amplifier`, the `amplifier` CLI, native
chat history, shared provider settings/keys, and `amplifier-memory`. It also
keeps shared coordination data under `~/.local/state/amplifier` (or your custom
shared state root). Do not delete any of those shared locations.

The commands below assume Unified's default `~/.amplifier-unified` directory
and generated per-user service. If you previously used `--data-dir`,
`AMPLIFIER_WEB_HOME` or `AMPLIFIER_WEB_DATA_DIR`, identify that old private
directory first; deleting the default directory will not reset a custom one.
Do not substitute `~/.amplifier` as the Unified data directory.

## 1. Stop and remove only the old Unified service

Finish any Unified work or voice call, and close its browser/PWA windows. For
the standard generated service:

```sh
amplifier-unified service uninstall
```

This works with the older Linux service and the current Linux/macOS services.
If Unified was run in a terminal or through a custom launcher, stop that
launcher instead. Do not stop all Python, Amplifier, or memory processes.

### Linux/WSL fallback if the old command is broken or already removed

Use this only for the standard **user** unit named `amplifier-unified.service`:

```sh
systemctl --user disable --now amplifier-unified.service
systemctl --user show amplifier-unified.service --property=MainPID --value
```

Confirm the PID is `0` (or the unit is absent) before continuing. If stopping
fails for another reason, resolve that first so it cannot restart during the reset.

```sh
rm -f -- "$HOME/.config/systemd/user/amplifier-unified.service"
rm -rf -- "$HOME/.config/systemd/user/amplifier-unified.service.d"
systemctl --user daemon-reload
```

These paths are specific to Unified. Custom system-level units, containers,
or units under another name need their own stop/remove operation; do not use
wildcards against Amplifier services. An absent unit is already uninstalled.

### macOS fallback

For the current generated launch agent, if the command is unavailable:

```sh
launchctl bootout "gui/$(id -u)/com.microsoft.amplifier-unified"
launchctl print "gui/$(id -u)/com.microsoft.amplifier-unified"
```

The second command should report that the service cannot be found. Once it is
unloaded, remove just its definition:

```sh
rm -f -- "$HOME/Library/LaunchAgents/com.microsoft.amplifier-unified.plist"
```

An old custom launch-agent label is a different service: stop that specific
launcher too. The earlier hackathon release did not provide this macOS agent.

## 2. Remove the old tool and Unified-only state

```sh
uv tool uninstall amplifier-unified
rm -rf -- "$HOME/.amplifier-unified"
```

If uv reports the tool is not installed, there is no uv tool to remove. A
development checkout or another Python environment is a separate launcher;
leave its source intact and stop using it to start the app. Do not uninstall
`amplifier`, `amplifier-memory`, or shared dependencies from their environments.

Deleting the app directory intentionally discards its private settings,
runtime/cache generations, UI state, Smart Tool registrations, TLS certificates,
and anything saved only there. Native chats already under `~/.amplifier/projects`
remain in place and can be discovered again. This is not a promise to recover
app-only data after deletion.

## 3. Install the Microsoft release and start fresh

From your home directory, outside any old checkout or activated development
environment:

```sh
cd "$HOME"
uv tool install --python 3.13 --upgrade \
  'git+https://github.com/microsoft/amplifier-unified@v0.20.31'
amplifier-unified --version
amplifier-unified service install
amplifier-unified service status
```

The version should be `0.20.31`. If an old executable still wins on PATH, open
a fresh terminal and check `command -v amplifier-unified`; remove the old alias
or launcher from your launch command before installing the service. Do not run
two Unified hosts against the same directory or port. The new install requires
Python 3.13+, uv, and access to its Git dependencies. The in-app release check
currently uses authenticated GitHub CLI (`gh`); that is unrelated to the
`amplifier` application CLI and does not require installing it.

Open **http://127.0.0.1:8941** on the host and sign in. Existing CLI credentials
and settings are read from the shared Amplifier home. For a custom
`AMPLIFIER_HOME`, keep that same root configured in the service environment;
the generated service does not copy arbitrary shell environment variables.

The reset also removed Unified's old remote-access/TLS configuration. Reapply
your bind addresses and HTTPS origins using [the deployment guide](DEPLOYMENT.md)
before reopening a remote/PWA address. A newly created CA needs to be trusted
on client devices again; do not disable certificate checking.

## 4. Finish setup and component updates

In **Settings → Updates**, check and install available components, allowing all
stages to finish. In **AI connections**, verify the selected connection and use
its test-message action. The first fresh conversation can take a few minutes
while its runtime is prepared. Reconnect/reconfigure any Smart Tools you need.

Shared explicit bundle/module registrations, pins, and forks are intentionally
preserved. If a fresh chat still resolves a `bkrabach` source, inspect the
effective shared/workspace configuration. Do not globally replace URLs or
delete `~/.amplifier/cache` or `~/.amplifier/projects`: that would affect the
CLI too. A deliberate shared override needs a separately reviewed change, or
a new dedicated Unified workspace with a scoped configuration.

If the browser still shows the old UI, clear site data **only for Unified's
origin** and reopen it. That clears its cached frontend/login, not server-side
CLI history. An old PWA shortcut may also need reopening at the newly configured
address.

## Validation boundary

These instructions follow the old 0.19.4 and current service/tool layout and
current shared-storage/configuration contracts. Service removal, installation,
settings preservation and runtime migration have targeted automated coverage.
A full reset of every historical hackathon or custom installation, physical
Windows/WSL setup, and personal configurations have not been exercised here.
