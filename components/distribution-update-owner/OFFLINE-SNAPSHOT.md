# Offline supervisor snapshot

`withOfflineSupervisorSnapshot(request, capture)` is an explicit offline operation.
The application must have a qualified stopped receipt and the supervisor must be
closed. It never stops, adopts, resumes or reconciles a service. It refuses live
owner leases and holds SQLite writer transactions on both existing ledgers until
the capture callback returns. Separate read connections export coherent standalone
SQLite files; copying a live database without its WAL is not equivalent.

The request binds the trusted inventory digest, service identity, stopped command
and exact participant IDs. New stop receipts retain `qualifiedOwners` from the
authenticated held host fence. Earlier receipts remain valid for existing resume
behavior but do not qualify this archive operation. Every declared participant
must have been covered, the exit proof must match, and the stopped receipt must
not have been used for resume. PID liveness only refuses another owner; process
absence never establishes stopped proof.

Capture receives the proof, private temporary ledger paths, and unchanged owner
state. Temporary files are removed after capture and writer locks are released
even on failure. Historical unknown update receipts are preserved verbatim.
Archive membership, native artifacts, external-writer assumptions, credential
review and inactive restore belong to the installer archive owner. This primitive
alone makes no complete-product backup claim.
