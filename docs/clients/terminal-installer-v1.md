# Guided AHP terminal installer v1

This is the thin local-installation boundary for the new
`microsoft/amplifier-unified-client-tui`. It does not import the old Python web
service, install an agent runtime, authenticate browser users, or publish releases.
The product Terminal owner supplies qualified artifacts and owns preparation,
enrollment, receipts, credential revocation, and actual public gateway access.

## Pure private-download renderer

`distribution/src/terminal-installer.js` exports:

```js
renderTerminalInstaller({
  preparation: {preparationId, grant, artifactId, origin, expiresAt, name, caPem},
  artifact: {release, filename, wheelBytes},
  runtimes: {python, node},
}) // {filename, contentType, bytes: Buffer, sha256}
```

`expiresAt` is Unix seconds. `origin` is the configured HTTPS origin, without a
path, credentials, query, or fragment. `caPem` is an optional **public** CA chain.
Preparation/artifact IDs are bounded opaque identifiers. The grant exists only
inside this private download; base64 is shell-safe encoding, not encryption.
Neither the rendered script nor its payload belongs in public action results,
logs, source-control fixtures, or release artifacts. Serve it authenticated with
`Cache-Control: no-store` and a private-download warning.

`release` uses the TUI owner's exact
`amplifier-unified-client-tui.release`, `schemaVersion: 1` contract. The product
owner must run full schema/semantic and selected qualification-evidence byte
validation before calling the renderer. The renderer additionally binds selected
filename, bytes, SHA256, package/version, target and AHP/runtime compatibility.
It is not an alternative release-feed validator. The schema's wheel tag includes
`py3-none-`; macOS `target.osFloor` is the compiler floor, and Linux
`qualification.minimumGlibc` is the qualified glibc floor. These remain distinct
from the OS actually tested. Wheel bytes are limited to 64 MiB, script to 96 MiB.
No published release or platform is invented when the feed is empty.

Each managed runtime descriptor is supplied by a trusted operator-qualified
configuration, never browser arguments:

```json
{
  "version": "22.0.0",
  "url": "https://reviewed-runtime-authority.example/node.tar.gz",
  "bytes": 123,
  "sha256": "64 lowercase hex characters",
  "archive": "tar.gz",
  "archiveRoot": "node-release-directory",
  "executable": "bin/node",
  "executableSha256": "64 lowercase hex characters; required for node",
  "target": {"os": "macos", "arch": "arm64"}
}
```

The example is a shape, not an available runtime. Python requires an exact
3.x.y version >=3.11 and a relocatable standalone archive with working `venv`
and `ensurepip`; Node requires an exact version >=22. Both targets must match
the selected wheel. `archiveRoot` is one safe path component, `executable` a
safe relative path. Archives are bounded to 512 MiB. The Node executable must
work independently of npm or other files from its archive. The release owner
must qualify the actual selected runtime archives on the advertised target;
version parsing alone is not runtime qualification.

## Local execution and ownership

The shell requires only Bash, curl, tar and the platform's SHA256/base64 tools.
It downloads and verifies the Node archive, extracts only its exact executable
member, verifies that member's separate digest, then runs the embedded built-in
Node helper. There is no Cargo/npm, existing Python, uv, or execution-library
requirement. Runtime downloads contain no credentials; bounded redirects remain
HTTPS. Credentials are never redirected.

The helper verifies the wheel OS/architecture/floor, reserves one original local
attempt, downloads/verifies the standalone Python archive, and streams extraction
into a new private version directory. Archive traversal, external links, special
files, duplicates and unsupported formats refuse. Internal Python links remain
supported. Expanded bytes and entries are bounded. No archive is extracted into
a prior installation.

It validates actual Python/Node versions, checks wheel metadata has the exact
package/version and **no `Requires-Dist`**, then installs that wheel into an
isolated venv with `--no-index --no-deps`. It checks packaged renderer/helper,
Python isolation, Node syntax and CLI help before enrollment. A dependency-bearing
successor requires an intentional installer contract extension; required packages
are never silently ignored. No model, account, native history or host runtime is
opened during validation.

Default root is `~/.local/share/amplifier-unified-terminal`, separate from the old
installer. `AMPLIFIER_TERMINAL_HOME` may select an empty owned root or this
installer's existing root. Unknown populated roots and nonprivate/symlink roots
refuse. Directories are 0700, credentials/CA/metadata 0600, launchers 0700. No shell
profile, PATH or unrelated command is changed. A prior launcher is reused only
if its retained ownership hashes match; otherwise the exact new connection's
launcher is printed. Previous connections and their version directories remain.

## Enrollment and interruption

The original `redemptionId` is persisted and fsynced before sending exactly:

```json
{"preparationId":"…","grant":"…","redemptionId":"…","artifactId":"…"}
```

POST target is the exact configured origin plus `/setup/terminal/redeem`, with
verified TLS and optional public CA. It accepts only the owner's exact 201
registered response, binding preparation, redemption, artifact, origin and name.
Only then are the token and saved connection written and the default selected
atomically. The token travels only in private request/response/file contents,
never in a URL or argument. The saved launcher uses the exact managed Python and
Node, plus token/CA **paths**; it permits current conversation options and rejects
credential/server overrides, including argparse abbreviations.

A consumed 409 response has no credential and cannot produce a new device. A
lost/malformed response retains `enrollment-unknown` and its original identity.
Rerunning the same setup refuses before a second enrollment attempt, including
after process restart. The installer does not infer completion from current
server state or replay a grant. Before-effect staging failure retains a small
receipt and removes only its own failed version directory. After possible
enrollment, staging and receipt remain for inspection/revocation; no cleanup
pretends enrollment did not happen. Use a new explicitly prepared setup only
after reviewing the original registration.

Local success reports **Installed locally**, with `connected: false`. Actual
connection is established by opening the TUI; browser download, device
registration, local installation, WSS connection and physical terminal use are
separate observations. This module does not prove gateway bearer acceptance or
device revocation—those belong to the product Terminal owner.

## Qualification boundaries

Run `node --test distribution/test/terminal-installer.test.mjs` on an owned host.
Tests use a tiny deterministic wheel, controlled Python/Node wrapper archives,
actual isolated venv/pip staging and locally trusted HTTPS enrollment. They cover
shell rendering/execution, private file modes, pre-enrollment dependency refusal,
unchanged previous default/commands, exact lost-reply no-replay, consumed grants,
credential redirect refusal, and safe streamed archive extraction. They make no
published runtime-archive, real TUI PTY, physical-device or production-access
claim. Those must additionally bind the TUI release owner's exact platform
artifact and the product authentication owner's gateway tests.
