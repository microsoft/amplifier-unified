# Client state scope map

Design note supporting [CS](../../contracts/client-state.v1.md), not an additional
contract or a statement that every synchronization channel exists today.
"Local" always needs a qualifier: execution host, workspace, device or client view.

## Proposed ownership and subscriptions

| State | Authority / scope | Who follows it | Client effect |
| --- | --- | --- | --- |
| Accepted messages, progress, questions, ownership | Host + conversation | Detail viewers; pending command/decision consumers | Reconcile shared conversation; preserve local draft/scroll |
| Name, activity summary, unread/attention source | Canonical conversation metadata and host summary projection | Interested catalog/attention views | Update small summary; do not load every transcript |
| Workspace registration, host paths, repository availability | Execution host | Workspace/catalog consumers | Identify the host; do not interpret a remote path as local |
| Global defaults and provider configuration | One host's selected shared configuration root | Settings viewers and dependent effective configurations | Recompute affected inherited values; no automatic cross-host sync |
| Shareable project defaults | Project configuration on host | That project's settings and dependent sessions | Respect higher-priority overrides |
| Private workspace-local settings | Host-local configuration for workspace | Authorized workspace consumers | Never imply that committing project files includes these values |
| Explicit session settings | Canonical session scope where portable, otherwise declared host capability | That conversation's viewers/runtime | Show override provenance and applied/pending state |
| Effective merged config | Derived from the supported scope chain | Consumers whose resolved inputs changed | Keep separate from persisted source documents |
| Mounted runtime config | Actual prepared worker | That conversation's viewers/operator | Show current applied revision independently of saved defaults |
| Catalog results and refresh status | Host cache/probe owner | Viewers of that catalog | Show freshness/updating state; a result is not a mounted or reachable provider |
| Selection, per-chat/pre-chat draft, pending attachments, scroll, open panels | One independent client view; storage may be on host | That view and explicitly targeted authorized agent inspection | Copy on resume when requested; never couple concurrent windows |
| Unsaved settings fields and ordering previews | Targeted editor view | That editor; authorized agent-visible nonsecret fields where supported | Preserve across remote saves; expose a conflict instead of overwriting |
| Theme/layout preference | Explicitly declared shared preference or client override | Consumers of that declared scope | Do not assume all presentation preferences are private; current shared theme differs from local preview |
| Credentials, raw secret form values | Host/device credential store or transient local input | Secret-aware mutation path only | Publish configured/available metadata, never raw values in snapshots |
| Mic, clipboard, camera, notification permission | Specific device/client | Targeted effect handler | Permission and effect outcome stay local; accepted session content may be shared |
| Uncertain command/outbox entry | Originating command identity + client recovery storage | Originating client and host receipt lookup | Preserve exact retry identity; do not transfer it by copying a view |

Browser and TUI storage implementations may differ. A draft saved in a host DB
can still be client-scoped; it does not become shared conversation content merely
because it is persisted. Likewise, an agent-visible editor is not automatically
public to every connected client. Authentication remains separate from client IDs.

## Configuration example

On one host, project P inherits provider defaults from its shared root. Session A
inherits P's model; session B has an explicit model override. Two clients edit P.

1. The first saves a new model against the source revision it read.
2. Both settings viewers see the new persisted value and provenance. The second
   editor's unsaved changes remain intact and its stale base is identified.
3. A's effective configuration changes; B's model override remains effective.
   Other inherited fields are evaluated separately rather than treating the
   presence of any override as immunity from all project changes.
4. A mounted worker reports its current applied configuration and the host's
   supported apply point: future preparation, idle application, queued change or
   explicit restart. Saving alone never claims that A switched immediately.
5. A corresponding workspace on another host does not change unless a separate,
   explicit configuration distribution feature applies the same update there.

Provider priority, bundle composition precedence and per-role routing candidate
preference are different inputs. A client must not represent them as one order.
Current settings owners are documented in the [work plan](development-plan.md).

## Subscription example

Two tabs and one TUI view A; another TUI views B. All show the workspace catalog.
An assistant update in A produces A detail for its three viewers and a bounded
summary change for catalog consumers. It does not rebuild B's transcript or
unrelated settings. Closing A's three viewers removes their subscriptions, while
A keeps working and saving. Returning obtains a current snapshot before following
new updates. A slow viewer can receive coalesced snapshots without slowing A.

This is a target for scoped publication. Current source still performs broad
publication; [CS3's assessment](assessment.md) records that gap explicitly.
