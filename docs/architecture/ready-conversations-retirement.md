# Ready conversations controls — approved retirement

The user explicitly approved this retirement on 2026-10-04 after the cold
first-send tradeoff, automatic host reuse and history/active-work preservation
were explained. This supersedes the earlier F18 pending-decision notes.

## Decision

Retire F18.01–F18.06: warm conversation count, idle lifetime in hours, prepare on
selection, their input validation, save/saving flow and zero-limit explanation.
Keep all six ledger IDs, original requirements and prior evidence. Record
`retired-by-user-decision`, not an implementation qualification. These controls
are no longer required for parity; other gaps remain open.

The host owns bounded runtime reuse and safe idle retirement. Selecting or
reading history does not request runtime preparation. An admitted execution may
reuse a prepared runtime or incur cold startup. Removing these controls must
preserve conversation identities, native transcripts, pending decisions, active
work and unknown receipts. It neither deletes history nor replays work.

## Current source and implementation boundary

Read-only inspection of [Host 543da73](https://github.com/microsoft/amplifier-unified-host/tree/543da73c5d53d14659f54f5650ceaff8a40a164f)
shows existing session-runtime reuse in `ensureAgent`, bounded admission and
native graceful retirement in `retireIdle`. It does not prove an adaptive cache
policy, broad performance improvement or a historical prewarm pool. New-session
creation and explicit runtime controls have their own admission behavior; they
are not made cold-history reads by this decision. This inspection does not establish
that every current client history-view path avoids initialization; separate causal
review must establish that implementation boundary. A short-lived passive ACP
process used to read history is distinct from an AmplifierSession execution worker.
Explicit conversation creation and selected runtime/model controls can initialize
an execution worker; this decision does not promise that only Send can do so.

Web owns removal of the legacy controls and navigation-interest preparation.
This documentation does not certify that source successor, its tests, release
or live adoption. The existing preview is unchanged. No host cache was cleared,
no live store was rebuilt, and no runtime policy was changed by this record.

## Evidence and acceptance

[The decision record](evidence/ready-conversations-retirement-20261004.json)
appends to each current ledger row without replacing earlier qualification
indexes or audit snapshots. Historical checkpoint counts remain historical.
The current ledger has 226 qualified, 98 partial, one implemented-unqualified
and six retired-by-user-decision rows; no rows are newly qualified and full parity
is false.

Implementation review must separately verify removal of the controls and their
requests, history selection without preparation, admitted cold execution/reuse,
and preservation of active work, history and original receipts. Those are
acceptance requirements, not passes established by this decision.
