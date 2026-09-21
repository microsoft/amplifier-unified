# Terminal app distribution and connection (DRAFT)

This is a proposed installation experience and implementation contract, not a
description of shipped installer controls. The connected TUI is available in
`microsoft/amplifier-app-tui`; the existing `amplifier-unified tui` launcher requires
the optional package. Installation actions, device pairing and desktop installers
described below are new work. The [live-session contract](live-sessions.md) owns
conversation behavior after connection.

## The experience

A person chooses **Terminal app** in Unified, installs it with ordinary operating
system controls, and opens the conversation they were viewing. They do not need
to understand Python, Rust, Git, access tokens, certificate files or shell paths.
The web client remains a complete entry point; the terminal is an optional way
to work with the same service, conversations and tools.

Every install and launch identifies two separate places:

| Place | What it means | Example |
| --- | --- | --- |
| App location | Computer on which the terminal interface opens | This Mac |
| Connected service | Computer on which conversations and tools run | Spark |

“Install on this computer” must never mean “run an installer on the remote server.”
The browser cannot install a native program without the operating system's normal
download/install interaction. Host administration remains a separately labelled
choice. Installation does not relocate workspaces or acquire a session lock.

## Ordinary journeys

### From the web on a laptop

1. Open **Terminal app**. The primary action is **Download for this computer**.
   Show the detected platform, with an accessible choice for other supported ones.
2. Run the signed platform installer. It includes the client runtime and native
   renderer, and creates an ordinary application shortcut. A compiler, GitHub
   login and a developer environment are not prerequisites for end users.
3. Open the app. It starts a short **Connect to Unified** browser flow. The
   signed-in browser identifies the service and the device being connected.
4. Complete connection once. The app opens the requested conversation, or the
   conversation picker when no target was provided. Opening never sends a draft.
5. Later, **Open in Terminal** opens that same conversation in an independent
   terminal window. Existing windows keep their own selection and drafts.

For a TUI, the shortcut can open the platform's terminal with the packaged client;
an embedded terminal renderer is not a prerequisite. Installers must account for
terminal availability and desktop launch errors without exposing command syntax
as the ordinary recovery path. A launched process is not proof of connection.

### Install on the Unified host

The secondary web action is **Install on Spark**, using the service's actual host
label. It installs the client for the service account on that host. Afterward,
show **Installed on Spark**, never **Open on this Mac**. A headless host cannot
open a terminal on the browser's computer. Explain how to launch from that host
without implying a new browser terminal or remote shell capability.

For people already using a terminal, proposed commands are:

```sh
amplifier-unified tui install
amplifier-unified tui
amplifier-unified tui update
amplifier-unified tui status
amplifier-unified tui uninstall
```

These command names are proposals; only the plain `tui` launcher exists today.
Install/update/uninstall affect the machine on which the command runs, regardless
of a remote connection setting. Reject ambiguous combinations with `--server`.
The current launcher already discovers local service port, token and app-owned
CA; retain that behavior. A standalone client on another machine uses pairing.

## Observable promises

| ID | Promise | A failure that disproves it |
| --- | --- | --- |
| TI-01 | Every action names its install location and connected service. | A Mac browser silently installs on Spark, or a remote path is treated as local. |
| TI-02 | Supported platforms install prebuilt, versioned artifacts with verified digests and provenance. | Installation falls back to compiling source or silently installs an older standalone client. |
| TI-03 | Web, CLI and agent installation actions use the same host installation manager and operation records. | A second click or second client starts a competing install, or agent actions bypass validation. |
| TI-04 | Progress is visible immediately and survives navigation/reconnection. | The button appears idle during download, or closing a dialog leaves an unknown installation. |
| TI-05 | Updates stage and validate separately before changing the selected client version. | An interrupted download removes the usable client, or updating the TUI restarts Unified. |
| TI-06 | Installed, launch requested, connected and conversation opened are distinct observations. | A download is shown as installed, or opening a URL is shown as a successful connection. |
| TI-07 | Each device has a revocable credential; browser links carry no durable control credential. | Installing the client requires copying the host control token or leaks it into URL history. |
| TI-08 | Uninstall detaches/removes the client without deleting host conversations or stopping their work. | Removing the client deletes sessions or kills execution; drafts disappear without an explicit data-removal choice. |
| TI-09 | Platform and host compatibility are explicit. | Windows is advertised as supported by requiring unexplained WSL setup, or an incompatible host silently downgrades features. |
| TI-10 | Existing user installations and development launchers are preserved. | Installing overwrites an unrelated launcher, checkout or environment without naming the replacement. |

These promises remain DRAFT. Passing one platform's check does not qualify
another platform or ratify this document.

## Installation mechanism

Unified owns host installation policy; Foundation does not need an installer.
The TUI repository owns platform builds, package identity and native launch.

The host manager resolves a compatible published release from a controlled
manifest (version, protocol compatibility, OS/architecture/minimum OS, asset,
digest and provenance). Never accept arbitrary package URLs or shell commands
from a browser action. Prerelease selection is explicit; `0.4.0rc1` is the first
connected candidate, while `0.3` releases are the standalone product.

Install into a private, versioned client environment separate from Unified's
running environment. Probe the installed Python adapter and native executable,
then atomically select it. Failed staging leaves the prior version selected.
Launch resolves a specific version; defer cleanup while old clients use it.
Uninstall never deletes a user's development checkout or unrelated `uv` tool.
Retain the existing `[tui]` extra as a compatibility path, but new managed installs
must not make every Unified update rebuild the client from a Git branch.

Proposed shared actions are `terminal.install`, `terminal.update`,
`terminal.uninstall` and `terminal.status`. A request returns an operation ID;
identical retries return the existing operation, and conflicting operations are
serialized. State includes target host, requested/installed version, stage,
progress when measurable, error/recovery action and activation outcome.
On service restart, reconcile staged files with the selected version before
offering another install; an interrupted operation is never assumed successful.

The settings card uses checking/downloading/installing/verifying/installed/error
states, disables only conflicting controls and retains content during refresh.
Status updates go to interested clients. Progress is not communicated only by
colour or animation; reduced-motion and assistive-technology states work too.
Closing the card leaves a host job observable later. Dismissal and cancellation
are separate actions; explicit cancellation preserves the previous installation.
Keep the card independent of the settings navigation layout.

## Pairing and trust

Installation and connection are separate. The first version may install and
launch locally using the host's existing private credential reference; remote
device pairing requires a service capability and acceptance tests of its own.
Do not disguise copying the current host-wide token as finished device pairing.

A native client creates a short-lived pairing request bound to its own verifier.
An authenticated browser authorizes that device and named service. Redemption
is single-use, expires, and yields a per-device credential stored in the OS
credential store (or an owner-only store on a headless host). Revocation prevents
new commands and terminates that device's authenticated streams without stopping
accepted session work. Presentation client IDs remain separate from credentials.

Ordinary open-conversation links contain a service identity and conversation
reference, not tokens, shell text or local executable paths. Parse and validate
them as data. A second click does not submit work or replace another window's
unsent input. Capability checks decide whether a requested conversation can open.

Verify remote HTTPS. For app-owned certificates, enrollment needs an authenticated
trust bootstrap through the already trusted browser or an explicit fingerprint
check; downloading an unknown host's CA over that same untrusted connection is
not verification. Do not add an insecure bypass to make onboarding appear smooth.

## Distribution and platform gaps

The repository and its releases are currently private. A normal end user should
not need a personal GitHub developer login. Provide an approved download channel:
an organization-managed distribution service, or an authenticated Unified
download endpoint backed by narrowly authorized release access. Keep repository
credentials on the distribution host; do not grant browser users raw GitHub keys.
Publishing the repository or granting redistribution rights is a separate decision.

For macOS, ship a Developer ID signed and notarized package/application. For
Windows, qualify the actual native client and ship an appropriately signed
installer; current WSL2 instructions do not meet the information-worker journey.
Linux packages/launchers need their own platform qualification. The current
connected `0.4.0rc1` artifacts cover Linux ARM64 and macOS ARM64 (macOS 26 wheel),
not a general Intel/Windows or older-macOS release.

Official distribution references: [Apple Developer ID](https://developer.apple.com/developer-id/)
and [Microsoft App Installer](https://learn.microsoft.com/en-us/windows/msix/app-installer/app-installer-file-overview).
Signing identities and approved package hosting must be provisioned before we
claim a production desktop installer is ready.

## Delivery order and acceptance

1. **Canonical source.** Copy existing published branches/tags/releases to
   Microsoft without rewriting their history; verify every asset's checksum.
   Keep original PR/issue discussion linked. Update active installation references.
2. **Managed host install.** Implement the shared manager and CLI commands, then
   the settings card. Qualify Mac and Spark with no compiler, double-click/retry,
   offline/download failure, restart during staging, update rollback, existing
   launcher preservation and uninstall with conversations still running.
3. **Desktop installation and pairing.** Build and sign supported packages, add
   device enrollment/revocation and Open in Terminal. Test from a clean machine
   with no Python/uv/Git/Rust/GitHub login, wrong/expired pairing, unavailable
   service, TLS trust failure and simultaneous independent client windows.

Completion requires a user to start from the web on one machine, open the same
host conversation in the installed terminal, exchange messages from either view,
close/reopen without replay, update the client without restarting Unified, and
remove it without losing the conversation. Host-only installation is a useful
first delivery, not proof of this complete desktop journey.
