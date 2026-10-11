# Reviewed backup removal

The Recovery coordinator uses the same complete product maintenance fence as
workspace cleanup. Native alone inventories and removes its completed private
archive artifacts. Coverage is explicitly limited to those archives, not every
backup or retained reset undo directory on the machine. The newest completed
archive and archives referenced by saved restore plans remain protected.
Unfinished or uncertain capture/restore work prevents removal.

A short-lived preview binds the archive identity and bounded inventory. Removal
requires the exact reviewed artifact identity and persists its command before
any filesystem effect. Native validates the artifact description and regular
files, refuses links and changed inventories, and never changes canonical
history. Repeated commands inspect their original receipt; uncertain removal
is not replayed and does not silently enable a new review.

The browser retains the pending original job before dispatch and reconciles it
passively after reload. Approval is not retained across reload. Previewing is
read-only; the final destructive action remains an explicit user action.

Installed composition qualification covers two actual archives, protection of
the newest, removal of the reviewed older archive, unchanged canonical history,
and release of the complete product fence. Browser review and deployed-instance
acceptance are recorded separately.
