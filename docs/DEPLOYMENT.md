# Deploying Unified beyond localhost

This guide is the canonical reference for making Unified available on a LAN or Tailscale network. It covers the service host, TLS trust, and Linux service management. The short path is in the [README](../README.md).

## Before you start

Unified defaults to `127.0.0.1:8941`. Remote access requires:

- an exact HTTPS origin for every address people will use;
- a certificate trusted on every client device (trust Unified's local CA when using `setup-tls`); and
- a non-loopback bind address.

Use an already trusted local console or SSH connection to configure the host. Do not disable certificate checks or enter credentials through an untrusted HTTPS connection.

## Configure the host

Replace the examples with the hostname and IP address clients will use:

```sh
amplifier-unified config set public_origins '["https://your-host.tailnet.ts.net:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["100.64.0.10", "127.0.0.1"]'
amplifier-unified doctor
```

The private server configuration is stored at `config/server.yaml` under Unified's data directory, normally `~/.amplifier-unified`. Non-loopback binds do not start until TLS and at least one exact HTTPS public origin are configured.

Additional top-level settings from local tooling or other versions do not prevent startup. Unified preserves them when saving ordinary configuration changes and does not activate features it does not implement. Known settings, including TLS, public origins and worker retention, retain their validation. Loading an existing configuration does not rewrite its file; an explicit `config reset all` resets the complete configuration.

`setup-tls` creates Unified's local CA and a leaf certificate. Run `setup-tls force` only after changing an address that must appear in the certificate; it keeps the CA and replaces the leaf certificate.

To use an existing trusted certificate and its private key, keep the bind on
loopback while configuring the pair, then enable the remote bind:

```sh
amplifier-unified config set tls.method ca
amplifier-unified config set public_origins '["https://your-host.example:8941"]'
amplifier-unified config set tls.cert '/absolute/path/server.crt'
amplifier-unified config set tls.key '/absolute/path/server.key'
amplifier-unified config set bind '["192.0.2.10", "127.0.0.1"]'
```

The certificate and key commands may be entered in either order. Until both are
configured, HTTPS cannot start and a non-loopback bind is refused. Invalid
configuration changes leave the previous saved configuration intact. To replace
an existing pair together, use one atomic setting:
`amplifier-unified config set tls '{method: ca, cert: /absolute/path/server.crt, key: /absolute/path/server.key}'`.
Starting the server also verifies that the certificate and key files can be loaded
as a matching pair; saving their paths alone is not a successful TLS check.

`amplifier-unified setup-tls status` reports the certificate/key readiness
separately from the availability of Unified's local CA. A supplied certificate
can be ready even when no app-owned CA exists to export. This check does not
establish that a client trusts the certificate, or that it covers the public
hostname.

If Unified uses a non-default data directory, pass the same `--data-dir` value to `setup-tls` and `doctor`.

## Trust the CA on each client

On the Unified host, export the public CA and record the fingerprint:

```sh
amplifier-unified setup-tls export > amplifier-unified-ca.crt
amplifier-unified setup-tls status
```

Transfer the CA over an already verified connection, then verify it before trusting it:

```sh
scp '<owner>@<host>:/absolute/path/amplifier-unified-ca.crt' .
openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256
```

Compare that fingerprint with the trusted host's `amplifier-unified setup-tls status` output. Import the CA into the user trust store on each client device, then restart the browser. On macOS, use Keychain Access's **login** keychain and trust it for SSL. Follow your organization's approved process for mobile-device certificate trust.

The `/setup` page can provide platform-specific follow-up instructions after TLS is trusted. Its displayed fingerprint or an anonymous certificate download is not independent proof of authenticity.

## Keep the host running

On Linux, including a WSL distribution with systemd enabled, install and manage
Unified with:

```sh
amplifier-unified service install
amplifier-unified service status
```

Available lifecycle commands are `install`, `start`, `stop`, `restart`, `status`, `logs`, and `uninstall`. Repeating `install` with the same configuration reuses its definition and starts an inactive service without restarting a running host. After a package update, use `amplifier-unified service restart` to load the new version.

`service status` verifies the expected installation through its configured
listener on the host. Its `Host listener` address uses the actual listener's
HTTP or HTTPS scheme. Configured public addresses are listed separately as
**not checked**: a healthy host does not prove DNS, network access or certificate
trust on another device. The authenticated status probe never follows redirects
or sends its control token to a configured public address. Normal launch still
opens the configured public address when one is set; verify it on the client.

Foreground `serve` announces the listener and schedules browser opening only
after all configured addresses bind successfully. An occupied port, unavailable
address or permission refusal gives a specific next step. It does not stop an
existing process, choose another interface, or change the saved configuration.

If the generated definition differs, rerun the same install command with `--replace` to save a timestamped private backup and restart with the new definition. The command reports the backup location. Custom service files are preserved unless `--replace` is explicit; review them before replacing. Expected service setup errors are reported without a Python traceback and do not undo a separately completed package installation.

Generated systemd services use `KillMode=mixed`: shutdown signals the host first,
allowing it to close its workers deliberately, while the service manager retains
final cleanup of the entire control group. Custom instance generators should
use the same policy. Older units using `control-group` can signal workers before
the host begins cleanup and record a false worker failure during a restart;
updating the package alone does not replace those unit definitions.

The installer supplies the selected `uv` and app executable directories to the
launcher before service registration. The service captures that `PATH` and
supplements it with the user's standard helper directories (`~/.local/bin` and
`~/.cargo/bin`; also Homebrew locations on macOS). Startup applies the same rule
to older service definitions, including after reset, without changing their
workspace, data directory, or custom configuration. Existing PATH precedence and
dispatcher symlinks are preserved. No shell startup scripts are evaluated.

If `uv` lives in a custom directory and works in a terminal but not in the app,
run `amplifier-unified service install --replace` from that terminal, preserving
any original `--data-dir` and `--workspace` options. This backs up the definition
and restarts the service; saved chats and settings remain. Confirm with
`amplifier-unified service status --wait 60`, then refresh the browser and retry
the connection. A reboot or reinstall alone does not change an older service's
captured custom PATH. `amplifier-unified doctor` checks helper discovery as well
as application settings; it does not claim provider sign-in succeeded.

Installation acceptance must include a fresh Linux user with no curl/Git/uv,
OS-correct grouped prerequisite guidance, installer-provisioned uv absent from
the original PATH, service startup with that environment, and later helper
discovery with a stale service PATH. Missing helpers during ChatGPT sign-in
must produce an actionable setup error, while arbitrary provider errors stay
redacted. These are covered by `test_installer_script.py`,
`test_host_dependencies.py`, and `test_deployment_service.py`; retain them in
the onboarding smoke checks when changing installation or service ownership.

For WSL, enable systemd in `/etc/wsl.conf`, restart WSL with `wsl --shutdown`
from Windows PowerShell, then run the Linux installation commands above.

On macOS, `amplifier-unified service install` creates and starts a per-user
launchd agent. Its standard output and error logs are private files under
Unified's data directory. Use `amplifier-unified service status` to inspect the
agent and `amplifier-unified service logs` to read its recent output.

## Installed app opens a cross-origin error

An installed PWA or external link can enter through its service worker.
Chromium's `fetch(event.request)` forwards that navigation with
`Sec-Fetch-Mode: navigate` but changes `Sec-Fetch-Dest` from `document` to the
literal `empty`. Servers that require `document` for every entry reject the
forwarded launch with `{"error": "Cross-origin requests are not permitted."}`.

The entry check permits this forwarded form only for GET/HEAD `/` and `/login`,
with no Origin header. Host validation, login/session authentication, exact
Origin checks, CSRF checks, and cross-site API/frame rejection remain in place.
Do not work around this error by disabling these protections or accepting
arbitrary origins. A server containing this fix handles already-installed
workers; reinstalling the PWA is not required for this particular failure.

The regression in `tests/test_browser_auth.py` exercises the shipped service
worker and real Chromium request metadata before and after worker control,
including sign-in and blocked API/frame navigation. It is not a physical
Android Edge installation test.

## Desktop window controls overlay

The manifest prefers `window-controls-overlay` with `standalone` fallback.
Supporting installed Chromium/Edge apps can enable it from their browser-owned
title-bar controls. Declaring it does not prove the browser has enabled it:
the frontend activates only when `navigator.windowControlsOverlay.visible`
reports a valid title-bar rectangle.

The compact header occupies that usable rectangle, reserving the native
buttons on either side. Small usable areas keep a blank draggable strip and
the header below it. Focused Canvas, dialogs, loading, login and offline pages
also preserve native clearance. Buttons, links, tabs, editors and contributed
interactive controls are excluded from dragging. Appearances supply chrome
colors; they do not supply the native geometry.

After rebuilding the frontend, run `npm run test:wco-browser` and
`npm run test:window-chrome-browser` under Node 22+. The former substitutes
native geometry, not application CSS; neither establishes native dragging.
For an optional real Linux installed-window probe with an available desktop:

```sh
AMPLIFIER_TEST_INSTALLED_PWA=1 uv run --with playwright python -m pytest -q -s \
  tests/test_browser_auth.py::test_installed_pwa_launch
```

It installs and removes only a disposable profile's app, signs in with synthetic
credentials, and records whether overlay actually became visible. It does not
enable the browser's overlay toggle or click native window controls. Keep
that result separate from a real macOS/Windows check of dragging, button
operation, appearance changes and browser fallback. A successful standalone
launch is not overlay acceptance. Deployment and physical-phone verification
remain separate from these local tests.

## Limits

Unified owns its own HTTPS listener and authentication. It does not trust a reverse proxy, shared cookies, or `X-Forwarded-*` headers. Configure exact public origins rather than broad address patterns.

The public bootstrap endpoints are limited to `/login`, `/setup`, `/api/ca`, `/ca.crt`, and `/api/health`.

## Debug-only profiling

Opaque server extensions remain unchanged on load and save. The profiling
consumer accepts only a literal boolean `true`; other extension values do not
enable capture or prevent startup.

The optional `amplifier-profiling` library must be installed in the serving host's
Python environment. Normal installations do not require it. An operator can
enable it through `amplifier-unified config set debug.profiling true`; no app
action, bundle or tool configuration can enable this setting.

`profiling.status`, `profiling.targets`, `profiling.start`, `profiling.read`,
`profiling.stop` and `profiling.release` are authenticated shared actions.
Mutations require a stable command ID. Start accepts only the process-local target
returned by targets, 0.1–60 seconds, and 1–50 Hz. Results are memory-bounded and
process-local, not retained across restart; small admission receipts prevent an
exact retry from starting another capture. An unsettled receipt after restart is
reported unavailable, never replayed. Download settled captures from the returned
authenticated URL before explicitly releasing their retention slot.

Disable through `amplifier-unified config set debug.profiling false`. While a
capture is active, the host rechecks config every 250 ms (subject to event-loop and
filesystem delays); malformed, missing or unreadable config fails closed.
Download authorization is rechecked before returning bytes; already-returned
bytes cannot be recalled. Shutdown stops the sampler.

This is cooperative sampling of all Python threads in **the serving host
process**, not arbitrary PID attachment. It records symbol metadata, not locals,
arguments, source text, prompts or event bodies. GIL delays, missed samples and
backend limitations remain visible. Function observations are wall-stack counts,
not per-function CPU percentages. Do not infer native-thread/C-stack coverage.
Profiles never enter canonical Context Intelligence capture or ordinary app-state
publication. The native event JSONL and resume transcript remain authoritative.

Enabling this setting grants the authenticated owner and its agents access to
process-wide symbol metadata. Unified is currently single-owner; do not enable
this adapter in a multi-tenant shared process without separate operator-only
authorization and tenant-isolated targets. Hosted containers need no ptrace or
root privileges for self-sampling. External/native sampling is not supplied.

## Rebuildable history metadata

The app's `native-catalog.sqlite3` stores derived metadata, not canonical
transcripts or context-intelligence event bodies. Cache loading validates
workspace path and availability types before discovery consumes them. An invalid
cache is reported as unavailable and bypassed on ordinary and forced refreshes;
original history and the rejected cache are preserved. This fallback is not
permission to delete or repair canonical history. Resume admission continues to
read native metadata rather than trusting cached classification.
