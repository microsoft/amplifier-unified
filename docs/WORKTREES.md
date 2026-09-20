# Managed checkouts and task execution handoff

The Session settings page and app_control share worktree.inspect/list/create,
attach/status/remove, handoff and reconcile. Inspect binds create to the current
Git source revision. Clean committed checkout is default; including uncommitted
changes is an explicit option. The reusable library contract and limits live in
[amplifier_worktrees](../amplifier_worktrees/README.md).

A task keeps its canonical session.workspace, native identity, history, settings,
voice records, task goal, drafts and artifact references. Its workingDirectory is
the execution folder. Both paths and executionRevision appear in shared status
and UI. Handoff does not relocate sidebar projects or copy/migrate native session
metadata. Configuration remains scoped to the original home; the process cwd and
PreparedBundle session_cwd use the explicit execution folder. Naming receives the
canonical history-home capability so it cannot create a second metadata owner.
Existing file/tool permission boundaries continue to apply.

Handoff returns a durable pending receipt, blocks new task input, and requests
cooperative release through the existing Foundation owner protocol. A running
writer checkpoints and stops before release. An already parked runtime is safe
to close while a temporary native session lock proves writer ownership. Another
application's owner is not silently taken over. No provider input is submitted,
no effects are rolled back, and no uncertain operation is replayed. After release
the app changes only the execution association and increments executionRevision.
The original task resumes only on a later explicit input or separately reviewed
authorized scheduler admission. Internal handoff does not increment the user stop
epoch. Scheduled work must bind executionRevision and defer configurationBusy.

Release failure, process death or an interrupted commit is visible as unknown.
Restart never resumes the handoff automatically. Reconcile requires current
receipt revision and a user's finding; choose the old source or intended target,
confirm cooperative writer release again, and record a new receipt. Agent-mediated
reconciliation must cite a genuine user message after the uncertain attempt.
A duplicate command returns its original record. Source/target files remain
untouched by handoff. Cleanup refuses folders still used by a task or unresolved
handoff, then relies on the library's exact Git ownership/cleanliness checks.

P01's optional operations source registry exposes handoff receipts read-only as
worktree:<receipt-id>; it does not create a second lifecycle journal. These are
local execution-folder controls. Cross-host filesystem transfer, native session
migration, portable absolute-path rewriting, submodule state copying and arbitrary
Git operations are outside this feature.

Tests use actual temporary Git repositories for staged/unstaged/untracked copy,
conflicts, stale revisions, branch ownership and protected cleanup. The actual
Foundation release handshake and native SessionStore resume are tested separately
from the deterministic browser runtime. The browser exercises the real Git
library/app/controller, handoff/reload/unknown/reconcile, cleanup and mobile UI.
Live provider execution in a moved checkout and cross-device behavior remain
integration acceptance, not claims from those fixtures.

Every managed Git invocation disables configured clean, smudge and process filters,
including inspection, checkout, patch application and cleanup. Source/global config
is unchanged. These operations preserve raw Git and worktree representations rather
than running conversion programs: clean LFS checkouts contain committed pointers,
while explicitly carried local materialized bytes remain local changes subject to
the normal size limits. A filtered repository may consequently look dirty under the
raw-byte inspection even when ordinary Git hides its conversion difference. This
policy prevents passive inspection and managed creation from running repository
conversion commands or downloading external content implicitly.
