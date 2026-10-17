# Settings browser isolation and unsaved-state coverage

The settings, settings-completeness and settings-drafts journeys start their own
synthetic fixture on an operating-system-selected loopback port. Readiness comes
from that child process's PID and address, after the application has started.
An unrelated listener's health response cannot satisfy fixture readiness.

The October 17 full smoke run exposed the previous fixed-port problem: a retained
preview already occupied port 8957, the new child failed to bind, and the health
probe accepted the retained preview. The settings test reached its sign-in page.
Its diagnostic handler then obscured that original failure with a missing view
field. Failure screenshots and state diagnostics are now best effort; the actual
assertion is always reported and rethrown. Product authentication and assertions
remain unchanged.

`settings-drafts` is an explicit additive scenario in `browser-checks.json`.
It covers unsaved provider input across Close, agent-requested dismissal, review
and save, narrow-screen browser Back, cancel/discard and model-picker management.
The original 65-scenario migration hash stays frozen. The source ownership union
grows from 125 to 126 scripts without duplicating another lane.

Desired outcomes: two concurrent fixture starts have different child identities
and listener addresses; the existing preview remains untouched; the three
journeys pass with private synthetic state and no browser errors. Execution
receipts record the actual source, ports and results separately. This headless
acceptance does not qualify physical device or installed-PWA behavior.
