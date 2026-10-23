# Registered own-history scope

The authenticated app instance belongs to one user. Its mounted app-control
capability already exposes that user's registered history, including explicit
cross-workspace search. `scope: all` selects that existing library; it is not a
grant, assignment privilege, new permission database or tenant boundary.

Agent reads default to the calling conversation's workspace. When other owned
workspaces are relevant, the agent can explicitly select `scope: all` without
another approval. A `workspace` argument filters that selected scope; it cannot
silently widen it. Native identity/registration, paging, source revision and
internal/child inclusion checks remain independent.

History query discovery diagnostics describe global index coverage, not errors
limited to the requested chat or workspace. List, search and read return the
loading/error state, total issue count, reconciliation status and number of
retained issue samples. `complete` keeps its coverage meaning even when samples
are omitted. Detailed samples are opt-in with `include_discovery_issues: true`
(the index retains at most 100); `issuesOmitted` and `detailsHint` make that
omission explicit. This changes no discovery work, scope, paging or saved state.
Search failures for the requested sessions still appear in `errors`.

| Route | Scope and retained checks |
| --- | --- |
| `app_control` history list/search/read | Shared workspace/all selector; native project/identity disambiguation, bounded text/pages, internal/child discovery flags; scope rechecked after history I/O. |
| `coordination.list` | Same selector. An explicit foreign workspace requires all. Root/worker inclusion and advertised mutation rights remain independent. |
| `coordination.wait` | Each target is checked before history loads and again when assembling a response after waiting. Out-of-scope targets return per-target errors. Cursors do not confer authority. |
| `coordination.read` | Exact-message and transcript-window reads use the same workspace/all selector. No hidden all-scope window bypass or workspace-only exact-message restriction. |
| `recall.search/read` | Shared selector, with the existing additional task scope. Search references include the chosen scope; source reads require it again and verify the indexed source revision. Search results recheck registered scope after index I/O. |
| Agent `history.list`, `history.shared.list/view` | Resolve through the same passive registered-history query and bounded result/pagination shape. Human legacy/shared-history UI keeps its existing explicit library browser. |
| Agent `session.inspect/history/titlePreview`, `message.copy`, `conversation.delivery`, `history.export` | Shared scope check before domain work and at the service mutation boundary. Existing availability, ownership, export and client-transport checks still apply. |
| `coordination.result` | Accepts the declared scope but retains the exact request participant and saved workspace checks even with all. A successful read is neither an artifact-library grant nor a new task instruction. |
| `outputs.*` and image resources | Existing caller-owned output library and exact identity/hash checks. All does not authorize reading a peer's library. Request artifact references remain references, not a broader library capability. |
| Attached per-session HTTP | Authenticated user-client routes retain the explicit path session as the target. A different body target is rejected even with all. Reading a chosen registered conversation needs no new approval. |

Native roots and finite, resumed, persistent, nested and in-process
recipe-compatible children reach this shared policy through their actual mounted
app-control tools. Children can read the same explicitly selected own-history
library; this does not lend them their parent's mutation principal. The optional
full recipe module is not claimed qualified; subprocess recipe execution remains
unsupported by the existing adapter.

Same-user UI selection of an exact registered conversation keeps its current
library access. It does not require the browser's previously selected workspace
to match. Scope selection does not alter the current conversation, private draft,
native writer ownership or execution. Explicit all never authorizes a send,
stop, configuration change, assignment membership change or source replay.

Other APIs retain their own domains: memory has task/workspace/global and
explicit cross-scope review plus attributable human authorization for writes;
saved sharing/export flows preserve their source/owner checks; schedule/status
and settings inspection retain their existing same-user read contracts. The
general UI-state catalog is not a secrecy boundary. This scoped retrieval
consistency is not OS confinement or a claim that an app capability hides all
other registered same-user data.

Qualification covers shared dispatch, actual native tool mounts, cross-workspace
allow/deny, moves during reads, indexed revision checks, request participation,
peer output refusal and authenticated session HTTP. It uses controlled native
orchestrators and no provider model. Source/package evidence does not establish
release, deployed-host adoption or cross-account isolation.
