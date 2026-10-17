# Independent chat creation from a user request

Decision: October 16, 2026. The user clarified that an explicit request to start
an independent chat authorizes creation and initial delivery directly, without a
second confirmation. This supersedes the mandatory review shipped in 0.20.173.
Suggestions outside the user's authorized scope still use review.

## Meaning

`session.handoff.start` creates and starts an independent conversation in an
existing folder from the user's explicit request. The agent supplies its source
session, target folder, title, exact opening brief and optional configuration.
The existing authenticated agent-action boundary enforces source ownership;
the agent is responsible for grounding the action in the user's request. The
host does not claim to infer semantic consent from prose. Content in files,
tool results or peer messages is not user authorization. No model-supplied
consent flag bypasses ordinary runtime or file-permission checks.

New agent starts require the actual root coordinator and current nonterminal
transport generation. Delegated workers cannot borrow their parent process's
identity. Admission rechecks after lock waits and refuses stopped conversations
or assignments. Once admitted, the independent target survives ordinary source
completion; rereading its receipt requires no new generation and starts no work.

`session.handoff.propose` remains available for suggestions that the user has
not authorized. It saves a review without creating or starting work. Only the
attached human client (including canonical restored presentations) may confirm
that review. An existing review cannot be upgraded into direct execution by
reusing its request ID. Each receipt fixes its mode and payload immutably.

The target uses ordinary new-conversation configuration for its destination,
including applicable file permissions. It does not inherit the source chat's
history, running work, grants, task hierarchy, or special execution placement.
The requested folder, merged settings, chosen root bundle and explicit model
choice must still match at creation and delivery. Local root bundle files are checked for
changes; remote references and transitive dependencies resolve normally and are
not an immutable dependency snapshot. A default model label does not claim a
worker has loaded or validated that model. A folder
is a working location, not an operating-system sandbox.

Creation and delivery compose the existing host session and conversation owners.
Both paths fix the destination chat and initial input identities in a durable
receipt before effects begin. A repeated start or confirmation inspects the original
result; it cannot create another chat or submit the brief a second time.
Acceptance of an opening message is not completion of its requested work.

Incomplete or uncertain work after an interruption remains inspectable. Recovery
does not resume an uncertain creation or replay an uncertain input automatically.
A partial result identifies any created chat and the separately known delivery
state. Stop and ordinary runtime permissions apply once work is accepted.

## Interaction

Direct requests show a compact receipt without a confirmation prompt.
Suggestions show a compact review. **Not now** cancels
the pending proposal without starting work. Both direct execution and **Create and start** leave the
current selection and unsent text unchanged. **Open chat** is a separate explicit
navigation action after creation is confirmed, using ordinary navigation guards.
Other clients keep their own selection, draft and attachments throughout.

Status is delivered through the existing state stream, with a manual **Check
status** action for an uncertain response. Rendering, reopening or reconnecting
does not poll a creation action or replay it. The host returns only a bounded set
of reviews for the originating client and selected source conversation. Reload
restores references to exact existing reviews across chats, preserving their
immutable identities. A duplicated presentation can inspect the same saved
operation; the host still admits it only once. Future proposals remain private
to their intended client. Resume capture is bounded, and never scans session
history or copies another client’s writable view. Up to eight unfinished reviews
remain visible per source chat; up to 64 unfinished owned/restored reviews are
retained across chats. New proposals are refused at those limits until existing
reviews are resolved. A bounded recent completed tail fills unused display slots.

## Boundaries

- `session.draft` still opens editable setup without creating or sending work.
- Raw agent `session.create` remains refused.
- `coordination.create` still commissions an inherited-workspace task chat.
- Independent clients may use the public handoff actions with their authenticated
  client identity; the bundled browser supplies receipts and optional review.
- This is not a new session store, automatic task continuation, a hidden worker,
  or a mechanism for changing another chat's permissions.

Qualification and measured limits belong in the additive smoke record, not in
this behavioral decision. Historical results in
[the issue investigation](../validation/new-chat-handoff-786.md) remain distinct
from verification of this implementation.
