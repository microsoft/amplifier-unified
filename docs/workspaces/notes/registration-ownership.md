# Registration metadata ownership

Workspace discovery supplies observations; it does not own the application's
registrations or conversation history. `amplifier_web.workspace_registrations`
reconciles those observations into caller-owned registration rows and hidden IDs.
It preserves existing row objects, display names and custom fields, and reports
ordered old-to-new reference remaps with the destination path observed at each
step. It has no filesystem, database, session, Core or Foundation dependency.
The same owner applies manual add, rename, archive, restore and remove rules to
those rows. Callers retain the list and existing row objects; adding an existing
folder changes its name only when an explicit name was supplied.

`AutomaticHistory` still owns the application lock and applies those remaps to
selection, pins, resident sessions and Canvas. Its existing scoped persistence
and publication remain the only write boundary for this refresh.
`workspace_selection` retains initialization, folder resolution and validation,
filesystem creation, and manual selection/settings/pin/Canvas effects. It adapts
registration errors to the existing application errors; the service's lock,
authority, receipts and persistence are unchanged. Native discovery remains the
source of saved native conversations. This split adds no registry, public API,
access decision or independent package.

Current registration IDs still derive from canonical paths, with native-project
placeholder IDs for unresolved folders. A registration's `aliases` are display
names, not a durable identity mapping. A reported path is an observed location,
not a filesystem permission grant.

The workspace vision's durable logical identity and host/account location
bindings remain a future design. That design must account for current path-based
configuration and Personalization scopes, native project aliases, saved pins,
session and Canvas references, and recovery before changing any identity. This
internal boundary neither implements that migration nor promises a portable
workspace registry.
