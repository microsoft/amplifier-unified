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

## Not established by these checks

- Publication or availability on the production Spark/Mac services. Those remain
  under the coordinated release owner's review and rollout.
- A clean physical Mac's Gatekeeper/Finder double-click behavior or a signed,
  notarized desktop installer. This milestone explicitly uses a shell setup file.
- Native Windows, Intel/older Macs or a new Linux installation. The manifest's
  Linux ARM64 wheel is an existing release; this installer still needs its own
  Linux end-to-end qualification before calling that platform fully verified.
- A model-backed conversation through this install, or broader client parity.
  Those are separate from installing and authenticating the existing client.
- Automatic update/uninstall, OS credential-store integration, or browser deep
  links. The UI and guide do not claim those capabilities.

Existing personal TUI launchers, development checkouts, service installations,
and conversation data were left intact. Release approval is separate from this
qualification record.
