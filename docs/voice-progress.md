# Voice progress and status

Voice receives a compact public status projection for its pinned conversation:
lifecycle, public phase, tool names, active worker/approval counts, timestamps and
whether a response is pending. Raw errors, tool arguments/results and private
reasoning are excluded. Elapsed time in passive context is measured as of the
last activity timestamp. The JSON stays complete and bounded to 6,000 characters.

Ordinary work has a 10-second quiet grace period. Afterwards the host supplies a
short factual notice only when public progress changes, with 15/30/60/120-second
backoff. Unchanged and idle states schedule no progress timer. Approval, failure
and stop transitions bypass ordinary pacing. Confirmed results suppress a
redundant notice for that same state; an idle manager with active workers is not
reported as all work completed.

Realtime notices wait while the user is speaking or a response/audio is active.
A queued notice is revalidated before delivery and discarded if its fact changed.
Explicit user questions can be answered from the latest projection immediately;
missing facts or deeper investigation still require delegation. Progress updates
never run tools, restart work, or replay the user's request.

Live receives paced commentary and the same quiet-wait instructions; physical
speech timing and model adherence require a live voice acceptance check. The
regressions cover host scheduling, status privacy, sideband messages, interruption
and completion precedence using protocol fixtures, not paid provider calls.
