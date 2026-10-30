# Current native generation authority

The existing `collaborationGeneration` record owns current native work identity.
It retains the owner's `interruptionRevision` alongside the generation ID,
delivered input IDs and terminal state. Stop invalidates the generation immediately,
even before a native terminal event arrives. A later status update cannot renew it.

The native producer must supply `interruptionRevision` on `generation.started`,
captured from the host's admitted input authority before execution. Normalization
must preserve that captured value. Looking up the owner's current revision when
the event is received is invalid: a delayed start could otherwise cross Stop.
Missing epochs can only represent revision zero, before the owner's first Stop.
Missing or mismatched epochs after Stop cannot establish new generation authority.

A duplicate start preserves the existing generation, including its inputs,
terminal state and original Stop revision. A known older generation in the
bounded retained generation history cannot replace the current owner. Fresh work
after Stop needs a new generation ID with the correctly captured current epoch;
it does not require a human input role or a new permission grant.

`Collaboration.active_binding` retains strict root-principal validation by default.
An effect owner may explicitly opt into a descendant's trusted native bridge
binding. That bridge must preserve the actual descendant principal and supply its
assigned owner generation and inputs; it cannot impersonate the root. The shared
check validates current generation and Stop revision, while the effect owner
checks its required delivered-input membership and ordinary authority. Root reply
and provenance consumers keep the strict default.

The host consumer and its synthetic transport tests do not qualify the native
producer. The captured-epoch producer and assigned-child generation stamping must
ship with consumers that require them; this host seam alone is not a complete
runtime release.
