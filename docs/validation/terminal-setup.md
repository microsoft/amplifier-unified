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
