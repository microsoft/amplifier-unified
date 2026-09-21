# Terminal setup qualification

The first guided installer adds `/setup/terminal`, revocable terminal credentials,
`tui install/status`, and saved launchers. It targets the existing connected
Microsoft TUI release `0.4.0rc1`; the TUI source was not changed.

## Verified in an isolated Mac environment

- Full backend suite after rebasing onto main 0.19.16: 1,448 passed,
  18 skipped. Focused setup/auth/CLI suite: 77 passed, 1 skipped. The final
  credential lifecycle refinements additionally passed all 17 setup tests. Coverage includes authenticated downloads, one-use grants,
  expiry, transport/origin rejection, idempotent preparation, persistent hashed
  credentials, revocation of active SSE without cancelling accepted work, distinct device
  identities when a preparation ID is reused, and web/agent action parity.
- Frontend production build passed. Python wheel build passed and contains the
  setup HTML/JavaScript, shell bootstrap and standalone installer implementation.
- Headless Chromium used the real isolated HTTPS app and authenticated session:
  prepare showed immediate disabled/progress state, downloaded the generated
  file, displayed its matching command, and fit a 375-pixel viewport.
- The downloaded installer ran on ARM64 macOS with PATH limited to operating
  system utilities. It installed pinned uv, managed Python 3.13 and the actual
  prebuilt TUI without using an existing Python/Rust/GitHub setup on its PATH.
- Enrollment verified the fixture's app-owned CA. The saved client successfully
  listed sessions through that HTTPS connection. No model request was made.
- Running the consumed setup file failed without changing the previous default
  or retaining a failed candidate. The saved launcher refused a server override,
  including an abbreviated option. A simulated certificate-verification failure
  also preserved the previous installation.

The browser fixture used Playwright's HTTPS exception for its temporary test
certificate only. The shell installer and saved client used ordinary certificate
verification and the embedded CA, with no insecure option.

Private grants, cookies, downloaded scripts and client credentials are deliberately
excluded from this repository. Local task evidence includes browser screenshots,
redacted qualification receipts and install logs.

## Linux ARM64 end-to-end qualification

On Spark's Linux aarch64/glibc 2.39, the generated installer was run in a fresh
private temporary root with PATH restricted to `/usr/bin:/bin`. It used the real
pinned Linux wheel and managed Python. The isolated HTTPS fixture ran on the Mac
through a loopback-only SSH tunnel; it was not Spark's production service.

The saved launcher authenticated and listed sessions. An actual Linux PTY launch
rendered the connected native interface and detached cleanly. Reusing the consumed
grant and substituting an incorrect wheel checksum both failed while preserving
the selected environment and removing failed candidates. Server override was
refused, token permissions were 0600, and existing personal launchers were unchanged.
No model request, production restart or development-checkout change occurred.
The redacted [Linux receipt](terminal-setup-linux.json) records these checks.
The fixture, SSH tunnel and temporary Linux installation were removed afterward.

A standalone native `--version` probe was initially rejected because this renderer
requires its connected bridge. It was replaced with the real PTY launch above;
this was a qualification-harness correction, not a product change.

## Latest-main integration

Rebased on main 0.19.17 (`d1fb5621`), preserving its settings/provider changes.
Conflicts were confined to generated frontend assets; these were regenerated
from the combined source. Terminal installer/authentication source is unchanged
from the independently reviewed and Linux-qualified head. The rebuilt wheel
contains matching setup assets and frontend identity. All 219 frontend unit tests
passed; the full rebased backend suite passed 1,458 tests with 54 skipped.
Skipped runtime/environment gates remain outside this qualification.

## Not established by these checks

- Publication or availability on the production Spark/Mac services. Those remain
  under the coordinated release owner's review and rollout.
- A clean physical Mac's Gatekeeper/Finder double-click behavior or a signed,
  notarized desktop installer. This milestone explicitly uses a shell setup file.
- Native Windows, Intel/older Macs, or Linux architectures/libc versions beyond
  the ARM64/glibc 2.39 environment qualified above.
- A model-backed conversation through this install, or broader client parity.
  Those are separate from installing and authenticating the existing client.
- Automatic update/uninstall, OS credential-store integration, or browser deep
  links. The UI and guide do not claim those capabilities.

Existing personal TUI launchers, development checkouts, service installations,
and conversation data were left intact. Release approval is separate from this
qualification record.


## Follow-up: Mac launch and registration feedback

A user completed installation and enrollment, then Finder opened the `.command`
launcher in Terminal while oh-my-zsh displayed an update question. The first
character of the injected absolute path was consumed by that question, leaving
`Users/...` and a file-not-found error. The launcher file itself remained valid;
the same saved client subsequently authenticated to Spark through a read-only
request. No conversation content was included in the check's output.

The replacement is a `.terminal` profile with `RunCommandAsShell=true`, invoking
`/bin/sh` and the saved connection script directly. Terminal's bundled
`TTAppPreferences.nib` binds the visible "Run inside shell" checkbox to
`selection.RunCommandAsShell` using `NSNegateBoolean`: true selects direct command
execution. No personal shell or default Terminal settings are changed, and the
existing `.command` file and enrollment are preserved.

Focused backend checks cover registration correlation, legacy device records,
reused setup IDs, direct launch command execution, existing-launcher preservation
and the unchanged Linux launcher. The headless browser fixture uses actual
setup/enrollment routes with a synthetic wheel: registration appears without
manual refresh, used downloads become unavailable, reload reconciles a non-secret
receipt, unchanged polls retain focus, failed refresh preserves last-known data,
revocation updates the page, and an older attempt cannot complete a newer setup.
No client is installed by this fixture.

**User-confirmed native acceptance:** the user reported that the replacement
`.terminal` launcher worked on their Mac. They then requested that their existing
terminal (WezTerm) be the primary entry point. The computer-use tool rejects
Terminal access in this environment; no substitute UI-control method was used.
The user's result establishes that specific launcher path, not a general
Gatekeeper or clean-device certification.

The installer now also creates a terminal-neutral `amplifier-terminal` command.
It reads the selected saved connection at launch time, forwards view arguments,
and never opens an emulator. Its managed base Python stays outside disposable
candidate environments. Tests cover switching the saved default, removal of a
failed candidate, spaces/arguments, malformed defaults and preservation of an
unrelated command. The short command is optional when `~/.local/bin` is not on
PATH; setup always prints a usable absolute path and does not edit shell files.

Final focused run: **93 passed, 1 skipped** across terminal setup, authentication,
server authentication, CLI deployment and setup-page checks (the separate Python
Playwright setup-page probe is unavailable in this environment). The real-route
headless browser check passed. A built wheel contains byte-identical updated
installer, setup page/script and device-registration code. These checks leave
broader emulator/clean-device acceptance outside the stated evidence.

The new short command was added to the user's existing installation without
reinstalling or changing enrollment. `amplifier-terminal --list-sessions`
authenticated to Spark successfully; output was captured and only exit status
and byte counts were reported. A server override was refused with exit code 2.
Shell configuration was unchanged. No new physical WezTerm inspection is claimed;
the command executes in its caller's terminal and does not launch an emulator.

## Latest compatible client selection

New setup requests resolve the canonical release catalog; prepared retries retain
identical installer bytes and do not require another catalog request. Version
ordering includes prereleases and pagination; selection excludes unsupported
platforms/protocols and refuses incomplete or corrupt candidate artifacts.
GitHub asset digests and the qualification receipt must agree before enrollment.

- 53 focused selector/setup tests pass, including changed releases, verified cache
  reuse/repair, bad digests, incomplete publication, incompatible protocols,
  pagination bounds, offline retries, version validation before redemption, and
  preservation of the prior installation after TLS/enrollment failures.
- Headless Chromium against the isolated real app passes preparation, exact
  registration, refresh failure preservation, reload, focus retention, revocation
  and narrow layout. The browser fixture supplies synthetic artifact bytes and
  does not install a client or contact production.
- Read-only canonical GitHub lookup confirms current receipts lack an explicit
  `connected_protocol_version`; preparation correctly refuses them. A newly
  qualified TUI release declaring that field must be published before deploying
  the dynamic selector. This is a publication dependency, not evidence that a
  successor is already available.

No existing client installation, pending update generation, service process or
saved conversation was changed by these checks. Native-platform qualification
and host rollout remain owned by the coordinated release process.
