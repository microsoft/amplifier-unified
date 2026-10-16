# New-chat handoff: current limitation and proposed acceptance

Issue [786](https://github.com/microsoft/amplifier-unified/issues/786) reports a
person asking an agent to create an independent chat in another project folder
and submit a short reading brief. Main at
`e4139b8fff97252debdb7d4fb72a9d1ca396f894` deliberately rejects raw agent
`session.create`. Opening `session.draft` does not create a conversation or
submit work. `coordination.create` inherits the caller's workspace and actual
execution placement; it is not a substitute for the requested independent chat.

The immediate correction changes action descriptions and the refusal's next
step. Discovery previously instructed callers to follow `session.draft` with
raw creation, despite the gate rejecting that call. It now explains what each
route can actually do. No creation authority, schema, selection, or delivery
behavior changes. Issue 786 remains open for the larger workflow.

## Canonical ownership and decision

The [shared client vision](../clients/VISION.md) calls for meaningful public
operations and authority that survives interface changes. The
[collaborative-workspaces decision](../workspaces/contracts/collaborative-workspaces-decision.md)
distinguishes independent peer chats, agent-created task chats, and delegated
children. Extending the task route with an unreviewed folder would blur those
relationships. The existing host session-creation and collaboration owners
should compose any new handoff; a second session store or client-only creation
path is unnecessary.

Proposed interaction, pending a product decision:

1. A user says “Start a chat in project B, read these files, then wait for me.”
2. The host binds the requesting user input, canonical target folder, exact
   initial brief, configuration choice, and whether the user asked to open it.
   A model-supplied `authorized: true` is not sufficient. Decide whether the
   existing human-input provenance can authorize this directly or whether the
   client must present a small “Create chat” review.
3. One durable operation reports creation and initial-input delivery separately.
   “Brief accepted” does not mean the files have been read. An unknown outcome
   is inspected using the original identity, never automatically replayed.
4. The user can open and continue the independent chat. Unrequested navigation
   leaves the current client selection and draft intact.

The decision must cover file/configuration authority and target availability,
not just a new button or alternate name for the rejected action. No new workflow
is implemented or approved by this document.

## Additive smoke checklist

These rows supplement the frozen 199-workflow / 669-outcome inventory; they do
not change its historical coverage counts or claim the proposed feature passed.

| Case | Current result / witness | Desired result |
| --- | --- | --- |
| Discover `session.draft`, `session.create`, and `coordination.create` | Descriptions now state the direct-agent limitation and inherited placement; schema paging remains covered by `test_agent_actions.py`. | Callers select a supported route without a predictably refused creation attempt. |
| Raw agent create after opening a draft | Existing creation-inheritance tests assert refusal with no new session, accepted command, runtime input, or lost draft/attachment. | Preserve refusal until a reviewed host-mediated route exists. |
| Human create with conflicting inherited configuration | Existing tests reject before creation; matching/empty choices retain the reviewed configuration. | Keep configuration review meaningful for every route. |
| Create an independent chat in another folder and deliver its brief | Reported user workflow remains unsupported; not counted as fixed. | Exact target workspace and actual execution folder; one durable chat, one initial input; accepted versus completed reading distinguishable. |
| Change target/configuration after handoff review | Not implemented or run. | Refuse stale or forged authorization without silently changing placement or widening file access. |
| Retry after lost acknowledgement or reconnect | Not implemented for the proposed route. | Inspect/deduplicate the original creation and delivery identities; no duplicate chat or replayed brief. |
| Preserve other clients' unfinished input and selection | Existing creation tests cover current draft preservation; proposed joined workflow remains unrun. | Only explicitly requested navigation changes; unrelated windows and drafts remain intact. |
| Stop, denied target, and partial/unknown delivery | Not implemented for the proposed route. | Clear bounded receipt and next action; no hidden partial creation or automatic retry. |

The focused qualification passed **35 tests in 17.62 seconds** with synthetic
state in the owned spark-2 DTU: creation inheritance, managed chats, and action
catalog behavior. One optional real programmatic-tool integration test skipped
because its separately owned dependency is absent from this environment.
No newly skipped test is represented as a pass.
It does not exercise a model, make network/provider calls, or establish the
missing end-to-end user workflow.
