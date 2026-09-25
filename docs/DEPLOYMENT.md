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
