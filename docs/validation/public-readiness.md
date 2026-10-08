# Public-readiness stabilization

Baseline: main 0.20.83, `8c2b0e726724941416a699b2f0a258b4c223d470`.
Scope: existing main application; AHP/ACP remains a separate secondary track.

## Changes

- Saved recovery notices are grouped and initially collapsed. Internal observations remain hidden. Saved tool results use the existing authenticated, paginated detail API and load only when opened.
- Restart/process-exit recovery marks unfinished work interrupted. Warming a replacement worker cannot turn that outcome into success or dismiss its notice. Starting an explicit new generation clears the prior notice. Settled cold histories remain unloaded.
- A chat with a worker-startup failure can preview and select a replacement bundle without first loading its unavailable old root. The replacement is validated from its composed characteristics, rechecked under Foundation's writer lock, and journaled. Failure/cancellation restores the original files. Model-pin incompatibility requires the existing explicit reset choice; no message or tool call is replayed.
- Chat info is remounted for each selected session so old paths, errors and late responses cannot leak into the next chat.
- The Public readiness workflow runs focused Python checks, the full frontend unit suite and three real UI/API browser regressions on PRs and main. Release publishing additionally requires that workflow at the selected immutable release revision. Repository status-check enforcement is separate from workflow execution.

## Verified in the owned DTU

Environment: `dtu-unified-public-readiness-20261008`, Ubuntu 24.04 ARM64,
Python 3.13.16, Node 22.23.3, Chromium. Source, dependencies, account, service,
state and ports are private to this environment. No shared app was changed.

| Check | Result |
| --- | --- |
| Final focused Python suite (20 modules) | 550 passed, 2 skipped |
| Full frontend unit suite | 511 passed |
| Recovery-notice browser | Groups, unknown outcomes, lazy saved result, technical details, narrow layout; passed |
| Chat-menu browser | Session ID/path details, rename/auto-name, diagnostics copy/download, advanced destinations, light/dark and mobile; passed |
| First-chat browser | Empty real API/storage, persisted draft, attachment, definite creation rejection, explicit retry, exactly one synthetic delivery, reload without replay; passed |
| Production frontend, wheel and source archive | Built successfully; existing bundle-size advisory |
| Fresh wheel install and user service | Installed as a fresh synthetic Linux account; doctor/status passed |
| Installed-app sign-in | Real PAM rejected an incorrect password and accepted the synthetic account; unauthenticated API rejected; authenticated composer opened |
| Actual reset to final candidate wheel | Service stopped, package repaired, restarted, doctor/status passed; nine sampled config/auth/attachment/session-evidence files byte-identical |
| Update behavior | Focused replacement/readiness/deployment tests passed, including preservation and durable activation |

The skipped tests are the optional Python Playwright module (not installed in
this test environment) and the opt-in uv installation test. Actual installed
sign-in and reset were separately exercised as described above. Node browser
checks ran with single-process Chromium because multiprocess Chromium crashes in
this ARM container; hosted CI uses normal Chromium.

Fixtures were corrected to exercise the current bootstrap and callback
interfaces. A definite creation rejection uses HTTP 409: HTTP 503 correctly
leaves delivery uncertain and must not be represented as permission to resend.
The first-chat test waits for draft-save acknowledgement before reload; it does
not qualify an immediate reload during the debounce interval.

## Remaining promotion acceptance

This is evidence for stabilization, not a claim that every platform, account or
feature has been qualified. Before broader promotion:

1. Run the candidate's hosted checks and release qualification, and require the
   Public readiness status on main through repository rules.
2. Exercise the final released artifact on macOS/Edge PWA and Windows/WSL:
   clean install, sign-in, first chat, update, restart/repair and diagnostic export.
   Native title-bar/PWA behavior needs real-device evidence.
3. Exercise real supported provider connections, a real failed-root replacement
   and a long conversation/recovery on the final artifact. Deterministic worker
   fixtures prove failure handling, not live model or ecosystem compatibility.
4. Verify an actual prior-release-to-candidate update; the DTU reset test is a
   same-version repair, not a substitute for that update acceptance.

The stabilization release candidate is 0.20.86. Separate draft feature PRs
currently propose 0.20.84 and 0.20.85; they are not included here. Existing
release authorization does not authorize AHP/ACP adoption.

Local evidence is retained by the owning workspace in `output/public-readiness`.
`WORKSPACE-MANIFEST.json` records the DTU and cleanup command. The private
installed preview remains on DTU loopback port 8941. Host ports 8443 and 9543
were not used. No live account state or credentials were copied.
