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
4. Download the file. Open Terminal, paste the displayed command, and run it.
   If your browser saves outside Downloads, use that location instead.
5. Wait for **Ready**. Use the generated Amplifier Terminal launcher in your home
   Applications folder for future visits. It remembers Spark and its trust
   configuration. Each launch has its own conversation selection and draft.

The Mac needs internet access but no existing Python, Rust, compiler or personal
GitHub account. Setup installs a verified runtime bootstrap, managed Python, and
the prebuilt TUI. It preserves existing TUI launchers and development checkouts.
Conversations and tools keep running on Spark after closing the terminal.
Downloading a file is not reported as a completed installation.

This is a shell setup file followed by a `.command` launcher, **not** a signed or
notarized desktop application. macOS security controls and terminal availability
still apply. The current release is `microsoft/amplifier-app-tui` `0.4.0rc1`:
Apple silicon/macOS 26+, or Linux ARM64/glibc. Intel Macs, older macOS, native
Windows and other Linux architectures need their own published builds.

## CLI and agent access

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

Unified obtains fixed assets through the host's GitHub release access and checks
pinned SHA-256 digests. The host needs `gh` and access to the private Microsoft
repository, or previously verified cached assets. Clients receive no GitHub
credentials. The runtime bootstrap is pinned and checksum-verified too.

Setup stages a new environment, validates the package/native executable, enrolls
the connection, writes its launcher, then atomically selects the default.
Download, checksum, TLS, expired-grant and staging failures preserve the previous
default. Unactivated failed environments are removed. A killed process can leave
staging files, but they are never selected as an installed client. If enrollment
succeeds and local saving fails, remove the unused connection from the setup
page and prepare another file. Delete downloaded setup files after use.

Browser reload does not resume a local installer. A setup file can be redeemed
only once. **Refresh connections** shows authorized installations after setup,
not proof that their terminals are currently online.

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
