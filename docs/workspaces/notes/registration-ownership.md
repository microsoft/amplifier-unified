# Registration metadata ownership

Workspace discovery supplies observations; it does not own the application's
registrations or conversation history. `amplifier_workspace_metadata.reconcile`
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
source of saved native conversations. The metadata package also owns
list/search/paging over those rows; the host
passes the same canonical registrations and retains demand and pinned-scope
selection. It adds no registry, access decision or persistence owner.

[amplifier-platform-workspaces](https://github.com/bkrabach/amplifier-platform-workspaces)
owns the standalone metadata API and direct tests. Unified packages
only its selected published artifact as the sole installed owner. Author changes,
independent publication and artifact adoption remain separate; this is not the
complete workspace domain. Follow the
[artifact selection instructions](../../../components/README.md#select-a-workspace-metadata-artifact);
the removed temporary author project must not be restored as another owner.

Current registration IDs still derive from canonical paths, with native-project
placeholder IDs for unresolved folders. A registration's `aliases` are display
names, not a durable identity mapping. A reported path is an observed location,
not a filesystem permission grant.

The identity/location direction retains each resolved workspace's existing ID
as its stable identity, without allocating a replacement or treating display
aliases as identity aliases. Unresolved native-project placeholders remain an
explicit exception: the existing reconciliation reports their ordered remaps.
Host ID derivation and those remaps are unchanged by the metadata interpretation.

`local_location(registration)` interprets the legacy row as one local location.
Its immutable `LocalLocation` contains the unchanged workspace ID, exact path,
and recorded availability. Missing, `None` or empty paths yield no known
location; an unavailable path remains known. The observation is scoped to the
caller's existing installation/account, not a new host or location identity.
The top-level `path` remains the only writable source. The derived snapshot does
not create a persistent `locations` field or interpret custom fields by that name.

The host's workspace and conversation selection paths consume a fresh
`local_location` snapshot when projecting a registration into the default folder.
They keep the existing registration ID and exact stored path, including a known
but unavailable folder. Selecting an unresolved registration keeps the previous
default folder. This does not change a saved conversation's workspace or worker
directory; selection, pins and Canvas effects remain host-owned.

This is a pure metadata API, not a location-binding migration. Future explicit
associations must preserve current path-based configuration and Personalization
scopes, native history locators, saved pins, session and Canvas references,
authority and recovery. Metadata relinking alone must not move history, change
live execution directories or grant access. Multi-location and cross-host
journeys remain separate work; no portable workspace registry is introduced.
