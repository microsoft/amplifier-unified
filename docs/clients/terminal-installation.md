# Terminal installation and saved connections

This implementation provides guided terminal installation through the web and
CLI. Signed desktop packages, browser deep links and automatic client updates
remain future work. The [live-session contract](live-sessions.md) owns
conversation behavior after connection.

## Install on a Mac and connect to Spark

1. On the Mac, open Spark's trusted HTTPS Unified address in your browser.
2. Visit `/setup` and follow its terminal installation link, or open **Settings →
   Advanced → Install app** and choose **Install the Terminal app on this computer**.
   Sign in if asked.
3. Choose **Mac with Apple silicon**, name the connection, and select **Prepare
   setup file**. The page shows progress while Spark prepares the download.
4. Download the file. Open your preferred terminal app (WezTerm, iTerm2, Terminal
   or another emulator), paste the displayed command, and run it.
   If your browser saves outside Downloads, use that location instead.
5. Wait for **Ready**, then run `amplifier-terminal` in that same terminal window.
   It remembers the selected service and its trust configuration. Each launch
   has its own conversation selection and draft. No separate terminal app opens.
6. If the short command is not found, use
   `"$HOME/.local/share/amplifier-terminal/launch"`. Setup prints the exact path
   for custom install locations. The optional `.terminal` launcher in your home
   Applications folder opens Apple Terminal for people who prefer a clickable file.

The Mac needs internet access but no existing Python, Rust, compiler or personal
GitHub account. Setup installs a verified runtime bootstrap, managed Python, and
the prebuilt TUI. It preserves existing TUI launchers and development checkouts.
Conversations and tools keep running on Spark after closing the terminal.
Downloading a file is not reported as a completed installation.

This is a shell setup file and a saved terminal command, with an optional macOS
`.terminal` profile (`.command` launcher on Linux), **not** a signed or notarized
desktop application. macOS security controls and terminal availability
still apply. Setup selects the newest compatible, qualified prebuilt release from
`microsoft/amplifier-app-tui`, including prereleases. Supported builds currently
target Apple silicon/macOS 26+, or Linux ARM64/glibc. Intel Macs, older macOS, native
Windows and other Linux architectures need their own published builds.

## CLI and agent access

The guided installer provides `amplifier-terminal` without installing the Unified
service on the client computer. It starts the last successfully selected saved
connection in the current terminal and forwards conversation options such as
`--session ID`, `--resume [ID]`, `--new` and `--list-sessions`. Bare `--resume`
opens the Resume picker in the launch directory. An ID or unique prefix resolves
either a native CLI session or a host conversation in that same scope. Use
`--workspace` to choose a different server directory. This requires a terminal
client with startup-picker and native-ID resolution support. The saved launcher still refuses
server/credential overrides. A command under `~/.local/bin` is added only when
that name is free or already belongs to this installation; unrelated commands
are preserved. Setup never changes shell profiles or `PATH`, and prints an
absolute fallback when the short command cannot be used. Custom install roots
keep their commands inside that root.

Where Unified is already installed:

```sh
amplifier-unified tui install
amplifier-unified tui status
amplifier-unified tui
amplifier-unified tui --session HOST_CONVERSATION_ID
```

`install` uses the saved connection when present, otherwise the configured local
service. With a local credential it downloads and runs the same setup file as
the browser. Without credentials it opens the authenticated setup page. To
choose another service, use `tui install --server https://YOUR-SERVICE`; to run an
already downloaded file use `tui install --setup-file /path/to/setup.sh`.
Explicit server selection never inherits another saved connection's credential.
`status` describes local configuration, not live service health.

Installation always affects the computer running the command. A Mac browser
never silently installs on Spark. This milestone adds no remote shell, desktop
launch, `tui update`, or `tui uninstall` operation.

Web, CLI and agents share `terminal.prepare`, `terminal.devices`, and
`terminal.revoke`. Preparation accepts a configured service origin, platform,
connection name, and command ID. An uncertain retry with the same ID/arguments
returns the same download receipt while valid. Changed arguments with that ID
fail. Receipts contain no credentials. The CLI does not restart Unified.

## Credentials and trust

The page and download require authentication. Remote setup requires trusted HTTPS;
loopback HTTP is permitted locally. Existing `/setup` instructions establish
trust in an app-owned certificate first. There is no insecure TLS bypass in the
installer or saved launcher.

The private download embeds the service address, public CA when applicable, and
a single-use enrollment grant valid for 30 minutes. Treat it as private. Setup
exchanges that grant for a credential specific to this installation. The grant
cannot authorize ordinary API operations. The host stores credential hashes.
Tokens do not appear in URLs, command arguments, action results or browser state.
Credential-bearing requests refuse redirects.

Each terminal credential has the signed-in account's API authority, including
session actions and configuration. This is revocation per installation, not
per-conversation permissions. Remove access on the setup page to reject new
commands and disconnect its existing authenticated streams. Already accepted
work continues. Prepare a new file to reconnect after revocation.

Client credentials live in owner-only files under
`~/.local/share/amplifier-terminal/connections/`, not macOS Keychain yet. Multiple
saved launchers can coexist; the last completed installation is the CLI default.
Running clients retain their own version and connection.

## Distribution and failure handling

Unified queries the canonical GitHub release catalog for each new setup request.
It chooses the highest published version with a supported wheel and a verified
qualification receipt declaring connected protocol v1. Drafts, other platforms
and other protocols are excluded. It validates GitHub's asset SHA-256 digests and
the receipt's exact wheel/version/platform, clean source commit, native load,
connected-only installation and privacy checks. A malformed or incomplete newest
candidate fails visibly; it does not silently substitute an older unverified build.
Artifact bytes are cached by digest and verified again before use. The host needs
`gh` and release access to check current metadata even when artifacts are cached.
Clients receive no GitHub credentials. The third-party runtime bootstrap remains
checksum-verified at its independently qualified version.

The downloaded script records the resolved client version and exact wheel digest.
Installation checks that version before redeeming the enrollment grant. Repeating
a still-valid setup request returns the original script and receipt, including
when a newer release appears or the catalog becomes unavailable. A new request
resolves afresh; neither action upgrades an existing connection automatically.
Existing clients, retained histories and update generations are unchanged.

Publication order: publish a qualified TUI successor whose receipt contains
`connected_protocol_version` before releasing this selector. Older receipts without
that field do not establish protocol compatibility. Missing compatible artifacts
produce a setup error before any enrollment grant is created.

Setup stages a new environment, validates the package/native executable, enrolls
the connection, writes its launcher, then atomically selects the default.
Download, checksum, TLS, expired-grant and staging failures preserve the previous
default. Unactivated failed environments are removed. A killed process can leave
staging files, but they are never selected as an installed client. If enrollment
succeeds and local saving fails, remove the unused connection from the setup
page and prepare another file. Delete downloaded setup files after use.

Browser reload does not resume a local installer. A setup file can be redeemed
only once. **Registered terminals** refreshes automatically while the page is visible and
when you return to it; **Refresh connections** remains available. A matching
registration disables reuse of that setup file and survives a page reload. The
page reports registration, not proof that local installation completed or a
terminal is currently online. Wait for the installer's **Ready** message before
running the saved terminal command. Existing enrollments without setup correlation remain listed.

Unified owns distribution/enrollment policy. Foundation keeps shared session
storage/ownership. The TUI repository owns client protocol and builds. This
milestone wraps its existing server/token-file/CA-file options in a saved launcher
that refuses server or credential overrides; no TUI source change is required.

## Remaining desktop experience

The longer-term standard still calls for signed/notarized packages, browser
pairing bound to a native verifier, OS credential storage, Open in Terminal links,
observable update/uninstall operations, and qualification of every advertised
platform. Launch requested, connected and conversation opened are distinct states.
Removing a client must preserve host conversations and other windows' drafts.

See [terminal setup validation](../validation/terminal-setup.md) for evidence and
what this milestone does not yet prove.

## Existing Mac launchers

Older `.command` launchers can fail when an interactive shell startup prompt
consumes part of Terminal's injected command. The `.terminal` launcher starts
`/bin/sh` with the saved connection script as its command, independently of the
user's login shell; it does not edit shell configuration or Terminal defaults.
An existing saved connection can use a newly generated launcher without another
enrollment or reinstall. Preserve the old launcher until the replacement has
been checked. Actual Finder/Terminal acceptance is separate from generating and
validating the profile; see the qualification record.
