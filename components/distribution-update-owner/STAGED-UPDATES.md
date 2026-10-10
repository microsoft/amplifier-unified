# Explicit prepared application updates

`owner.prepare(commandId, releaseId?)` prepares and verifies one inactive signed
candidate. Its terminal receipt is `succeeded` / `prepared`; no host fence is
acquired and no process is stopped or started. `inspect().staged` exposes only
`{commandId, target: ReleaseIdentity, expectedCurrentId: string | null}`.

`owner.activate(commandId, {preparedCommandId, targetDigest, expectedCurrentId})`
requires the exact successful preparation receipt, latest staged slot, target
digest and current baseline. A different preparation, changed digest, or changed
current release is refused. It verifies saved bytes again, acquires the existing
held idle admission, reobserves current source/channel eligibility, then uses the
same durable restart and reconciliation path as direct install. It never falls
back to the latest release or prepares a different candidate. Retrying the same
command retrieves its receipt; lost outcomes must not become another command.

The slot persists across supervisor reopening and contains only one candidate.
A successful new preparation supersedes it. While it exists, automatic checks
continue but automatic installation waits; saved preferences are unchanged.
Successful manual install, explicit activation or rollback clears the slot.
Direct `install` remains the ordinary immediate prepare-and-install action.

Supervisor RPC operations are `prepare` and `activate`. The authenticated AHP
facade exposes `updates.application.prepare` and `updates.application.activate`
with the same arguments and request `commandId`. `prepare`/`activate` are
mutations and obey host authorization/intake. Staged metadata and durable
receipts remain available through passive reads. Opaque paths and candidate
handles are never included in their public projection.

F12.14 is a distinct application control. Native runtime preparation/selection
is owned separately; its generation cannot be selected by these operations.
Browser controls and actual managed service deployment have separate acceptance.
