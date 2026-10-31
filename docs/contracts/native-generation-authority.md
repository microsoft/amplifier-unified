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

The host execution snapshot supplies this revision to the worker's private
command envelope at admission. The worker retains it beside the input's existing
context binding and copies it into the native generation before publication.
Retries of an already accepted input keep its original binding. Internal native
child/job-result inputs inherit the worker's captured startup revision; they do
not consult the current host. Stop closes that worker, and admission waits for
its confirmed exit before a replacement can receive the new startup revision.
Parking and remounting preserve the original startup capture.

`Collaboration.active_binding` retains strict root-principal validation by default.
An effect owner may explicitly opt into a descendant's trusted native bridge
binding. That bridge must preserve the actual descendant principal and supply its
assigned owner generation and inputs; it cannot impersonate the root. The shared
check validates current generation and Stop revision, while the effect owner
checks its required delivered-input membership and ordinary authority. Root reply
and provenance consumers keep the strict default.

`AccessMode.capture_full` shares fresh Full capture for feedback and observations.
It uses that native check and records the actual bounded input observation;
missing or empty transport is unavailable coverage, never a verified empty
assignment or a reason to deny Full. Supplied malformed or foreign inputs refuse.
Fresh trusted system work needs no native principal; a supplied native envelope
must remain valid. Effect owners retain placement, payload and delayed-write
checks in their existing receipts. Passive reads do not capture new authority.

Child invocation captures its app-access installer synchronously, before provider
resolution or session mounting can await. The retained installer carries the
original generation, inputs and Stop revision into the eventual child bridge.
Nested children keep their parent's assigned tuple. A first mount that finishes
after newer root work or Stop cannot borrow that newer execution's authority.

The host consumer and its synthetic transport tests do not qualify the native
producer. The captured-epoch producer and assigned-child generation stamping must
ship with consumers that require them; this host seam alone is not a complete
runtime release.
