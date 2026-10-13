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
The application consumes same-origin, same-path launch URLs, queues them until
boot/attachment, and selects targets in order in the receiving window without
reloading its document. Cold URL/launch-queue duplicates are ignored. Warm failed
launches report the existing navigation error; browsers without this API retain
ordinary URL navigation. Existing installations may need their browser to refresh
the installed manifest before the launch mode changes.

Implementation reference: [Chromium Launch Handler API](https://developer.chrome.com/docs/web-platform/launch-handler).
The DTU browser test covers two warm launches, document preservation, exact target,
independent windows and no chat submission; pure tests cover pre-boot delivery and
foreign/path-mismatched targets.
