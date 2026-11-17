# Shared update presentation

User-authorized direction, November 16, 2026. This document specifies the
coordinated service/Mac/Windows candidate, not deployed compatibility evidence.
The shared promise is DC1; observations and native pairing retain their owners.

The owning account on each service shares dismissal receipts across device
credentials. Today's host has one owner account. This does not federate separate
hosts or imply a new multi-account identity system. A future multi-account host
must key these receipts by its actual authorization principal, never client ID.

Supported observation discovery advertises `dismissals:{version:1,
action:"presentation.dismiss"}`. Every attention row and active/preparing session carries a
`dismissal:{id,fingerprint,dismissed}` descriptor for its exact current state.
Idle sessions have no activity descriptor.
IDs/fingerprints are opaque; clients must not derive them from text, time or title.
Attention receipts use the exact canonical notice fingerprint. Activity receipts
use a host-retained execution/action incarnation and lifecycle. Progress alone
must not resurrect dismissed work. Unknown/new activity does not inherit an old
receipt. Host-owned read, required-action and aggregate counts remain unchanged.

Use the existing `POST /api/actions` command envelope with a stable `id`,
`action:"presentation.dismiss"`, and `args:{items:[{id,fingerprint}]}` (1–100).
The host validates every target before writing. A stale or disappeared state
returns `409 presentation_changed`; clients refresh and never retarget the intent.
Replaying the same intent is safe and cannot suppress a newer fingerprint.
Revoked credentials cannot submit; already accepted receipts remain shared.
No general action authority is granted to native credentials by this operation.

Clients persist intent before sending and hide only that exact state while
synchronization is pending. Failed/offline sync is visible in connection status;
no claim of remote completion until acceptance. A complete new observation
retires confirmed or obsolete intent. A reconnect may retry an exact dismissal
receipt; this does not change the separate no-automatic-replay policy for chats.
Historical local attention dismissals may migrate only when their exact current
notice identity/fingerprint is still observed. Never upload blanket read receipts
or translate guessed work labels into a host-issued state identity.

Observation collections remain independently coherent and bounded. Dismissal
changes invalidate presentation-bearing session/attention generations, not the
canonical count meaning. Old leased pages retain their original receipt view.
Public display hints are bounded plain text from host-owned projections, not raw
tool input/output or an entire transcript. Device-specific truncation stays local.

Cutover order: qualify and adopt the generic service and both native clients,
then adopt the adapter-removal successor. This successor deletes the adapter
module, route and authorization branch; native isolation and authority checks now
use generic resources. Retained historical qualification receipts describe their
original source and are not relabeled as current feed evidence. No compatibility
fallback is retained. Merge/publication of the removal successor must wait for
client adoption evidence; source removal alone is not completed rollout.


Avatar selection, palette, size, placement, notification visibility/hover mode,
service grouping, sound and OS notification permission stay device-local. Shared
read/pin facts retain their existing host owners. A legacy local followed-chat
exception remains local during migration; pinning the chat is the existing
shared opt-in for agent-created work. Separate service installations remain
separate accounts/authorities even when aggregated in one desktop surface.
