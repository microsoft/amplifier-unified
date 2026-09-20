# Remaining issue followups

## Review acknowledgement during a pending send (#41)

The browser reproduction held a conversation send at admission, opened Activity,
and clicked Mark reviewed for another conversation's completion. Before the fix,
the review never reached the server while the send remained held. The corrected
UI uses a separate ordered queue only when an attention.read request contains
explicit item IDs and their observed fingerprints. Requests that depend on the
current conversation or mutate work retain their existing ordering.

The Settings review button now sends fingerprints too. A late review of an older
occurrence cannot mark a newer completion read. Browser coverage verifies the
pending-send case, newer-occurrence protection, original send target, and no
duplicate send. Existing backend attention/client/projection tests also pass.

## Typing audit (#38)

The confirmed conversation-name race was fixed in 0.10.9. Twelve additional
fields were exercised with delayed acknowledgements and concurrent state events:
provider name, bundle source/name/description, import title/bundle, allowed and
denied folders, skin name/CSS, and feedback title/body. Every field retained all
text; fields supporting selection also retained insertion/caret position. Older
receipts did not erase newer edits. The main composer regression still covers
debounce, rejection, concurrent updates, explicit send, and stale receipts.

This is evidence for these current controls, not a reproduction of the reporter's
entire device/session. No further production typing change was justified.

## Large retained history and recovery (#47)

A disposable production-server fixture contains ten conversations and exactly
1,159 retained messages, including 520 and 549 messages in the two large active
conversations, plus 800 tool nodes. The canonical fixture is about 14.25 MB; its
browser projection is about 864 KB and initially renders 60 selected messages.

Verified with production assets:

- A stalled detail request leaves Settings and Maintenance accessible.
- A failed detail request shows an error and can be retried successfully.
- Earlier history loads without duplicate message IDs; full scoped reads still
  return all 520 and 549 messages. All ten conversations and 1,159 messages remain.
- The event stream can render the shell while the initial HTTP state read stalls.
- When both state sources fail, a retry appears after ten seconds and recovers.
- Existing full-message/tool-detail retrieval and feedback diagnostics pass.

Measured local shell startup was about 364–427 ms. These are controlled fixture
measurements, not a production latency guarantee or validation of the original
Tailnet/Linux deployment.

## Other dispositions

#53's independent-window behavior shipped in 0.11.1. The production two-client
suite verifies separate drafts, selections, panels, reconnect/reload, shared
partial/final replies, and a terminal client's idempotent command retry.

#44's operational status indicators shipped in 0.10.9; a global chooser remains
unplanned. #42, #43, and #48 are unaccepted product proposals, not defects in
supported behavior. #50 could not be reproduced for ordinary numbered lists in
the earlier twelve-case audit; ordered checkboxes are a distinct existing style.
An exact Markdown/device/skin example is needed to reopen that report.

All fixtures use synthetic history and local runtimes. No real provider call,
feedback submission, user-data cleanup, or deployed-service restart is used as
validation.
