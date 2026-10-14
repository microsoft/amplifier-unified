# Reviewed automatic-update preferences reset

The supervisor exposes `appReset`, also available as
`SupervisorClient.appReset`, with owner ID `updates` and the sole part
`updates.preferences`. The interface is structurally compatible with the
recovery capability's registered `AppResetOwner.perform(operation, args,
fence, context)` port. It is an internal authenticated owner port, not a
new browser RPC or an authorization bypass. Product recovery owns user review,
authorization, the full held host census, aggregation and fence release.

The reset target is `{autoCheck:false, autoInstall:false, intervalMs:14400000}`.
It preserves installed/current/previous/staged releases, rollback history,
signed release trust, source configuration, update commands and their receipts,
shared settings, credentials and conversations. It is not a full app reset.

## Trusted composition

Register `supervisor.appReset` with product recovery's explicit reset owners.
The product must allow owner `updates` to own exactly `updates.preferences`.
Supply `serveHostControl({recoveryOwners, ...})` with the complete configured
required-owner census from trusted composition. This list is copied at startup;
clients cannot provide or alter it. Without it, reset refuses.

`createProductionSupervisorPorts` wires the authenticated host-control verifier
automatically. Custom supervisor factories must provide
`verifyRecoveryFence(fence)`, backed by equivalent independently observed host
authority; they must never echo caller assertions. The supplied verifier checks
the live held recovery fence, original instance and data scope, exact complete
unique owner census, closed intake, zero admissions and continuations, and the
original admitted host receipt. Unknown, changed-instance, missing-owner and
extra-owner states refuse. This verifier does not acquire or release a fence.
The root recovery owner must keep the full host maintenance lease held across
each awaited prepare/apply/restore call, as with other reset owners.

## Review, apply and restore

1. `prepare` accepts `commandId`, `parts:['updates.preferences']` and
   `privateContentReviewed:true`. It returns `{receipt}` with an immutable
   redacted review in `receipt.result`, valid for ten minutes. Review `expiresAt`
   is Unix seconds, matching the other AppReset owners for aggregation and
   browser display. Receipt `createdAt` and `settledAt` remain Unix milliseconds.
   No preference
   values or private before-image appear in the review.
2. `inspect({preparedId,reviewHash}, fence)` checks applicability against the
   exact current revision under the same full held recovery contract.
3. `apply({commandId,preparedId,reviewHash}, fence)` repeats expiry and revision
   checks, then atomically saves the disabled preferences, private before-image
   reference, consumed review and original command receipt in the supervisor's
   private SQLite ledger. Ordinary writes, including no-op writes and A-B-A
   changes, invalidate an older review. Activation/rollback preference changes
   also advance the revision.
4. Restoring requires a new `prepare` with `restoreCommandId` identifying a
   succeeded original apply. It is available only while that apply's exact
   post-reset revision is current. The new review declares `restore`.
   `restore` then requires its own `commandId`, new `preparedId`/`reviewHash`,
   original `resetCommandId`, and `expectedPostResetRevision`. It repeats CAS
   and atomically restores only the private preference before-image.
5. `inspect({commandId})` passively returns the exact original receipt without a
   fence, after lost responses or supervisor restart. Reusing a command with
   changed arguments refuses. An exact repeat returns the original receipt,
   never repeats effects. It does not reconstruct interrupted work.

No reset is queued behind ordinary update work. Existing pending/uncertain
commands or an ongoing service lifecycle operation cause a no-effect refusal.
An in-flight reset blocks new ordinary commands and service stop. Reset cancels
the automatic-check timer without cancelling accepted work. Restored automatic
preferences resume at the next normal authenticated host idle notification;
the reset call itself does not trigger a check or install while recovery is held.

Receipts for refused/running/unknown states contain no arbitrary result or private
diagnostics. A lost transport/storage outcome remains unknown to the caller;
inspect the original command instead of replaying work or releasing its fence.
The update ledger retains original reset receipts and private before-images.

## Acceptance boundary

Tests cover real authenticated supervisor/host-control RPCs, separately installed
package consumption, actual public-host held census, forged and incomplete
fences, stale and expired reviews, ordinary-write ABA, separately reviewed
restore, timer/queue races and a process exit after commit without clean shutdown.
The owner does not claim assembled product UI acceptance or any live reset.
