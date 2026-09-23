# Ordered in-app updates

Updates check the published app first. An available release stops component
inventory/checks until installation and verified restart; the new process reads
its own shipped dependency declarations. A failed app check also defers component
checks rather than assuming the installed app is current.

Next are included components: the declared worker dependencies and Git sources
in shipped app behaviors. Missing source caches or an uninitialized worker lock
are installation candidates even when no existing cached row exists. Local
source overrides, pins and protected edits retain the existing update policy.
After included updates activate, the updater inventories and checks remaining
sources against the new generation. Tiers with no updates are skipped.

Check now remains read-only. Install remembers intent for the whole sequence,
including across an app restart, without changing the user's automatic-install
setting. Automatic installation uses the same sequence. Installation errors stop
progress; work and calls must be idle for activation. A fresh process must prove
its installation before component checks continue. Rollback cancels continuation
and retains the existing rule that automatic installation is disabled.

The application status box summarizes included and other component availability,
missing installations, unresolved checks and waiting phases. Detailed source
inventory remains below the box. Frontend and agent callers share updates.check,
updates.install, and the sequence/summary state.

Validation covers app-first early return, empty-tier progression, included-check
failures, explicit install intent after a verified restart, missing-source
revision validation, read-only inventory, preserved source policies, and a
real two-repository staged update. In that staged test, active work holds the
first activation; after idle, included and optional source copies advance in
order while both original repositories remain unchanged.
