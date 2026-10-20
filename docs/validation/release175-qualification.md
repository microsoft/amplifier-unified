# 0.20.175 qualification

This patch includes the merged #828 fixture corrections and fixes a product
layout race in Chat info. Late usage reads now grow below the existing controls,
the dialog stays anchored, and its scrollbar space remains stable. The usage
refresh button precedes variable metrics. Accounting, reads and other dialogs
retain their existing behavior.

The owner also accepted reviewed correction comments and separately reviewed
later attachments as complete for #294. Those workflows were already delivered;
this release records the scope decision without introducing another mutation.

## Focused qualification

Application execution and builds used synthetic state in the owned spark-2 DTU.
The candidate client is `0.20.175 / f4235893cb248843`, based on #828 merge
`9bc3f3e5996b53fbd2d045232e2a6d9e2ba9ab8e`.

- The new browser assertion failed on shipped 0.20.174: usage completion moved
  the Questions & answers target by 323.265625px during a held click.
- The complete Questions browser scenario passed on the candidate. Desktop and
  narrow initial reads and expanded refreshes measured 0px movement in the
  history target rectangle and refresh-button position/height. Every original
  pointerup opened the disclosure. Canonical synthetic receipts grew to six,
  then twelve provider/model groups. Existing uniqueness, handoff, answer,
  retry, draft, voice-provenance and mobile checks remained enabled.
- Focused conversation-usage and execution-observability frontend tests:
  **11 passed**, no skips.
- Release notes tests: **30 passed** in 6.08s.
- Locked dependency dry-run passed. No dependency updates were applied to the
  persistent owned environment.
- Production frontend build passed; a second build produced identical file
  paths and SHA-256 hashes. Generated client assets are included.
- Independent review of product changes and the final browser proof found no
  blocker. Desktop and narrow screenshots were inspected.

The reusable [layout smoke checklist](chat-info-layout-smoke.md) records current
and desired results. Evidence is retained in the task workspace under
`output/chat-info175-20261017`; old failures remain separate from candidate
passes. ARM Chromium used process-local single-process/no-sandbox flags inside
the owned DTU. These checks do not claim live-provider or native-device coverage.

The short publication pipeline supplies installed-package, full frontend and
native-runtime qualification on the merged candidate. Broad Python, integration,
readiness and extended browser checks run after publication and are monitored
separately. Their future results are not implied by these focused passes. This
release does not authorize spark-1 adoption or restart.
