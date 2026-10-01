# Deploying Unified beyond localhost

This guide is the canonical reference for making Unified available on a LAN or Tailscale network. It covers the service host, TLS trust, and Linux service management. The short path is in the [README](../README.md).

## Before you start

Unified defaults to `127.0.0.1:8941`. Remote access requires:

- an exact HTTPS origin for every address people will use;
- Unified's local certificate authority (CA) to be trusted on every client device; and
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

If Unified uses a non-default data directory, pass the same `--data-dir` value to `setup-tls` and `doctor`.

## Trust the CA on each client

On the Unified host, export the public CA and record the fingerprint:

```sh
amplifier-unified setup-tls export > amplifier-unified-ca.crt
amplifier-unified doctor
```

Transfer the CA over an already verified connection, then verify it before trusting it:

```sh
scp '<owner>@<host>:/absolute/path/amplifier-unified-ca.crt' .
openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256
```

Compare that fingerprint with the trusted host's `doctor` output. Import the CA into the user trust store on each client device, then restart the browser. On macOS, use Keychain Access's **login** keychain and trust it for SSL. Follow your organization's approved process for mobile-device certificate trust.

The `/setup` page can provide platform-specific follow-up instructions after TLS is trusted. Its displayed fingerprint or an anonymous certificate download is not independent proof of authenticity.

## Keep the host running

On Linux, including a WSL distribution with systemd enabled, install and manage
Unified with:

```sh
amplifier-unified service install
amplifier-unified service status
```

Available lifecycle commands are `install`, `start`, `stop`, `restart`, `status`, `logs`, and `uninstall`. Repeating `install` with the same configuration reuses its definition and starts an inactive service without restarting a running host. After a package update, use `amplifier-unified service restart` to load the new version.

If the generated definition differs, rerun the same install command with `--replace` to save a timestamped private backup and restart with the new definition. The command reports the backup location. Custom service files are preserved unless `--replace` is explicit; review them before replacing. Expected service setup errors are reported without a Python traceback and do not undo a separately completed package installation.

The service installation captures the current shell's `PATH` so Unified can find `uv`. If the location of `uv` changes, rerun installation with `--replace` from a shell where `uv --version` works, preserving any original `--data-dir` and `--workspace` options.

For WSL, enable systemd in `/etc/wsl.conf`, restart WSL with `wsl --shutdown`
from Windows PowerShell, then run the Linux installation commands above.

On macOS, `amplifier-unified service install` creates and starts a per-user
launchd agent. Its standard output and error logs are private files under
Unified's data directory. Use `amplifier-unified service status` to inspect the
agent and `amplifier-unified service logs` to read its recent output.

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
