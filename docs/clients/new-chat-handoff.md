# Reviewed independent chat creation

Decision: October 16, 2026. The user approved completing issue 786 with a compact
review of the destination folder and opening message before **Create and start**.
This implements the shared client vision's meaningful public operations and
independent attention without weakening the existing agent creation boundary.

## Meaning

An agent can propose a new independent conversation in an existing folder. A
proposal creates a saved review, not a conversation or a model request. The
originating client displays its exact destination, title, bundle, model choice
and opening message. Only an explicit human confirmation in that client, or a presentation restored
through the canonical client-resume path, can authorize creation and initial delivery. A model's assertion of consent is not
authority, and proposing a different payload requires another review.

The target uses ordinary new-conversation configuration for its destination,
including applicable file permissions. It does not inherit the source chat's
history, running work, grants, task hierarchy, or special execution placement.
The reviewed folder, merged settings, chosen root bundle and explicit model
choice must still match when confirmed. Local root bundle files are checked for
changes; remote references and transitive dependencies resolve normally and are
not an immutable dependency snapshot. A default model label does not claim a
worker has loaded or validated that model. A folder
is a working location, not an operating-system sandbox.

Creation and delivery compose the existing host session and conversation owners.
One durable handoff identity fixes the destination chat and initial input
identities before effects begin. A repeated confirmation inspects the original
result; it cannot create another chat or submit the brief a second time.
Acceptance of an opening message is not completion of its requested work.

Incomplete or uncertain work after an interruption remains inspectable. Recovery
does not resume an uncertain creation or replay an uncertain input automatically.
A partial result identifies any created chat and the separately known delivery
state. Stop and ordinary runtime permissions apply once work is accepted.

## Interaction

The compact review appears in the requesting conversation. **Not now** cancels
the pending proposal without starting work. **Create and start** leaves the
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
  client identity; the bundled browser supplies the inline review.
- This is not a new session store, automatic task continuation, a hidden worker,
  or a mechanism for changing another chat's permissions.

Qualification and measured limits belong in the additive smoke record, not in
this behavioral decision. Historical results in
[the issue investigation](../validation/new-chat-handoff-786.md) remain distinct
from verification of this implementation.
