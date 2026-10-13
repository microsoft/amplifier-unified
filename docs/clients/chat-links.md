# Open an exact chat in the browser

An authenticated owner can open `/?chat=<URL-encoded-session-id>` from any client.
The browser selects that chat only in the opening window, then removes `chat`
from the URL after successful selection. Other query parameters remain intact.
Reload restores that window's selection without repeating the link action.

An unauthenticated navigation goes through the existing sign-in flow and retains
its destination. Missing or deleted targets display the existing navigation error
and leave the target in the URL; they do not select a substitute chat. Opening a
link never submits input, acknowledges attention, approves an action, or changes
another window's draft/selection. Existing selection may schedule ordinary host
preparation, just as selecting a chat in the web UI does.

This feature has no dependency on optional observation endpoints, native client
installation, or the AHP/ACP design. The host's existing authentication, TLS,
Host and Origin protections continue to apply. Do not put credentials or commands
in the link. The parser accepts only a nonempty chat ID up to 200 characters with
no control characters; the host resolves and validates its actual availability.

Acceptance: `frontend/tests/chat-deep-link.test.mjs` and
`frontend/tests/companion-browser.mjs` cover parsing, exact target, query cleanup,
reload, independent windows, missing/deleted targets, sign-in, and no submitted
turn or ordinary-browser companion polling. Run application/browser tests only
in the project's owned DTU.

Installed browsers supporting the Launch Handler API use `focus-existing`.
The application consumes same-origin, same-path launch URLs and retains the exact
chat target in this window's URL using `history.replaceState`, without reloading
the document. It waits for boot/attachment, then selects the latest target. Cold
URL/launch-queue duplicates are ignored. A newer launch supersedes an older one.

Failed selection leaves the target in the URL and displays **Retry opening chat**
and **Dismiss chat link**. Retry attempts that exact navigation once; missing,
deleted and forbidden targets never enter an automatic retry loop. Reconnect or
React rebinding alone does not retry. Reload/sign-in can recover through the
ordinary chat-link URL path. Success, explicit dismissal, or a newer user chat,
workspace or new-chat navigation clears the old intent. A late result from an
older attempt cannot clear or report failure for a newer intent. Navigation uses
the existing per-window dispatch queue; retry cannot submit input or replay an
execution command. External links keep the current view, draft and canvas mounted
until selection is accepted; a failed launch must not destroy local iframe input. Drafts and mounted
canvas state remain owned by that window.

Browsers without this API retain ordinary URL navigation. Existing installations
may need their browser to refresh the installed manifest before the launch mode
changes.

Implementation reference: [Chromium Launch Handler API](https://developer.chrome.com/docs/web-platform/launch-handler).
The DTU browser test covers two warm launches, document preservation, exact target,
independent windows, failed warm launches with explicit retry, retained composer
and iframe input, missing/deleted targets, newer user navigation, and no chat
submission. Pure tests also cover pre-boot delivery, foreign/path-mismatched
targets, forbidden targets, rebinding, dismissal and stale in-flight results.
