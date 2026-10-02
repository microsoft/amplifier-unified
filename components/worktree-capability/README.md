# Unified worktree capability

This adapter owns selected-session indexes and durable effect receipts. It uses
the separately installed `amplifier-worktrees` Python library for Git operations
and the host's public relocation and directory-guard APIs for session authority.
It does not import AppService, native runtimes, sibling source trees, or clients.

```js
import {createWorktreeCapability} from '@amplifier/unified-worktree-capability';
const owner=createWorktreeCapability({
  directory:'/private/owned/worktrees', python:'/owned/venv/bin/python',
  inspectSession, relocateSession, directoryInUse, withDirectoryGuard,
  readUserMessage, onChanged
});
```

The Python interpreter must contain the public `amplifier-worktrees>=0.1,<0.2`
package. The Node package includes its small one-shot worker; no permanently
running Git process or native session is needed to display a checkout page.
Install the library from its published package or an independently built wheel.
Do not add a sibling checkout to `PYTHONPATH`.

## Public host contract

- `inspectSession(uri,{clientId?})` authenticates the session and returns
  `{session,historyHome,workingDirectory,executionDirectory,executionRevision,
  configurationBusy?}`. `historyHome` is the original canonical workspace, not
  a private transcript directory. `workingDirectory` is a compatibility fallback.
- `relocateSession(uri,{commandId,target,expectedExecutionRevision,reconciles?,
  evidence?})` owns idle admission, native writer release, durable location CAS,
  history identity, and configuration fences. Success is
  `{applied:true,executionDirectory,executionRevision,receipt}`. Preflight refusal
  is `{applied:false,executed:false,reason}`. An error after admission carries a
  durable receipt and remains unknown. `reconciles` invokes evidence inspection,
  never replay of the old relocation.
- `directoryInUse(path)` returns `{inUse,sessions:[{session,reason}],pending}`.
  `withDirectoryGuard(path,async remove=>...)` checks indexed history/execution and
  unresolved transition references and excludes overlapping host admissions
  until the callback finishes. The callback performs Git cleanup only. If this
  authority is absent, cleanup fails closed.
- `readUserMessage(uri,id)` returns actual durable input provenance. Agent
  reconciliation requires a newer user/voice message, excluding question replies
  and scheduled inputs. An authenticated UI may explicitly provide its own finding.
- `onChanged('worktrees',uri)` invalidates only this topic and session.

The host must authorize the adapter's managed checkout directory as an execution
root. This does not change catalog visibility or migrate history to that root.
One owner process has exclusive responsibility for one state directory. Call
`close()` on orderly shutdown; it awaits admitted work and never retries it.

## Wire and storage

The manifest advertises the original eight `worktree.*` operations and an
evidence-only `worktree.resolve` operation through the
versioned capability contract. Exported `actionSchemas` support client and agent
discovery. Ordinary editor text, source-ref choices, inspection results, paging,
and reconciliation drafts stay on the client. Only explicit shared actions cross
the protocol boundary.

The `worktrees` topic contains one selected-session map with at most 25 checkout
summaries, 25 handoffs, and 25 other receipts. `worktree.list` has collection-scoped
keyset cursors and a maximum of 50 items. No read walks all Git records or sessions.
Named `amplifier-worktree://records/<uuid>?session=<uri>&offset=<n>` resources expose
the original manifest in pages of 100 files. Git source status and inspection
lists are bounded, with counts/truncation explicit. Source evidence remains in the
library's private durable storage. No full manifest appears in the topic.

SQLite indexes commands by session, status, and reservations. Receipts are saved
before effects. Restart changes pending receipts to unknown in one indexed SQL
update. A duplicate command returns its original known/unknown receipt without
rerunning Git or native work. Failed cleanup never invokes `--force`. Unknown
cleanup is not automatically repeated. Explicit follow-on work requires inspection.
An uncertain Git command retains its deterministic library record ID before
dispatch. `worktree.status` can inspect that exact command's record even when its
acknowledgement was lost, without scanning storage or recreating the checkout.
`worktree.resolve` records an explicit user finding: completion additionally
requires matching durable library evidence; abandonment preserves all files and
evidence. Neither outcome runs Git effects. Unknown handoffs instead use the
host's native-location reconciliation contract.

The Python worker accepts one allowlisted 64 KiB request, returns at most 1 MiB,
has a 6-minute deadline, and uses fixed Git argv through the library. Concurrent
read workers are capped at eight; queued mutations are capped at 32. The owner
has no filesystem watchers, historical record scan, polling loop, or warm runtime.

## Qualification

```sh
uv venv .venv
uv pip install --python .venv/bin/python /release/amplifier_worktrees-0.1.0-py3-none-any.whl
npm test
```

Tests use real temporary Git repositories and an installed wheel. They preserve
staged/unstaged/untracked source data, verify clean removal and history-home
retention, fence unknown outcomes, enforce scope and provenance, and query an
indexed 25,000-record fixture. Native relocation itself is qualified by the host
and native adapter; the owner suite labels its host callbacks as fixtures.
