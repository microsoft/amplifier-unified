# Configuration authority

Configuration administration uses the existing shared actions, command receipts,
approval cards and settings owners. A root can request named effects and scopes;
the user approves once, and the root can reuse that grant across later turns.
The user can also issue the same grant directly through `capability.grant`.
No extra settings form is required for the agent. Direct user administration and
ordinary own-chat selection of an existing provider/model keep their existing
paths.

`capability.read` inspects grants without executing anything. In Advanced >
Model & tools, **Agent permissions for settings** shows the same records and
offers revision-checked revocation. `capability.request` uses the ordinary
Allow/Deny card. Native root `app_control` registers a waiter in the existing
worker approval map before publishing that card; the tool remains pending until
the decision, cancellation or expiry. The committed decision wakes that tool,
which reads current authority before returning. The agent must then deliberately
perform its next action. Approval never replays a previously rejected write.

Raw API requests are nonblocking and return a pending receipt. After a worker is
lost, approval still saves the decision but does not revive or replay its turn.
The root can read/reuse the authority on a later explicit input. Already active
grants are reusable; natural turn completion does not revoke them. Stop,
workspace change, explicit revocation or optional expiry invalidates them.

## Effect and ownership mapping

| Effect | Shared routes | Scope |
| --- | --- | --- |
| File access | `permissions.save` | Explicit local, project or global |
| Connections | Provider save/remove/move/reorder/login | Explicit local, project or global; connection sources can execute code and credentials can change |
| Model defaults | Provider finish-setup/image defaults, routing use/save | Explicit local, project or global |
| Modules | Module and source save/remove/validate | Explicit local, project or global; validation can execute code |
| Bundles | Registered bundle add/toggle/remove/move/reorder/save, bundle default | Global registered storage; workspace default is local |
| Bundles | Bundle switch/fork | Session |
| Runtime configuration | Configuration apply, runtime configuration apply/toggle | Session |
| Connections | Runtime configuration provider login | Session |
| App defaults | Settings update and runtime retention update | Global; a settings bundle field also requires the bundle effect |

Session grants belong to one actual native root, with its workspace and
interruption revision. Shared host dispatch validates the principal and current
generation. Supplying a root session ID in model arguments does not confer that
identity. Explicit global configuration targets work for an authorized root;
non-global grants do not write another workspace's settings.

A lower-authority peer request cannot borrow a recipient's grant. The host
intersects current sender/recipient grants through bounded retained input
lineage. Revoking a sender also prevents a queued recipient effect. Host-created
returns to the root's own saved dependency preserve that root's original
authority and original input lineage. A human may explicitly approve the exact
current peer request. Peer-authored instructions or reply IDs do not constitute
approval.

## Consumer audit

| Consumer | Implemented boundary and evidence |
| --- | --- |
| Native root | Actual mounted `app_control` can request, wait, resume and perform a granted global write; unsupported/absent/stale principal or generation is denied. |
| Finite and resumed children | Core/Foundation-mounted children carry their own principal. Parent grant borrowing is denied on first and resumed execution. |
| Persistent and nested children | Same native bridge installation; children and grandchildren cannot inherit root grant identity. |
| Recipe child | Tested the shared in-process recipe-compatible child spawn seam. The optional full recipe module was not mounted. Subprocess recipe execution remains unsupported by the existing adapter. |
| Commissioned root | Existing session/configuration creation does not copy grants, which remain session-keyed command records. Independently explicit user administration remains available. |
| New-task schedule | Actual schedule/session-creation fixture creates a new root with configuration but no grant; its attempted configuration write is denied. Assignment membership does not transfer grants. |
| Same-root schedule | Scheduling retains its independent source authorization. A later root configuration action still checks that root's grant, expiry and Stop revision; scheduler-origin dispatch has no authority bypass. This is source/shared-route coverage, not a real-provider schedule run. |
| Background children | Retain native child principal; no root grant inheritance. |
| Background management | Captured authority follows the existing task/ContextVar path. Queue, validation and runtime-preparation waits recheck it before supported effects. Provider login activation uses guarded settings/credential sinks. |
| Queued configuration | Existing pending configuration retains grant/revision provenance across host restart. Revoked/stopped/expired work is marked failed and its requested plan is preserved; nothing is replayed. |
| Direct dispatch and attached clients | Shared service/app bridge/per-session HTTP routes expose the same actions and scope checks. HTTP grant, exact retry and scoped revoke are covered. |
| User settings UI | Existing user administration remains available without an agent grant. Browser approval/read/revoke uses the shared actions and preserves private drafts. |

The implementation has no second permission database or transcript. Grants,
approval decisions and exact command retries use existing SQLite command
receipts. Reads are paginated and active/pending grants are bounded per root.
Historical expired/revoked grants do not exhaust a lifetime quota.

## Delayed effects and limits

Configuration actions retain the selected grant revision and interruption
revision. The host checks them after waits, before guarded settings writes,
credential writes, executable validation admission and runtime-control admission.
Settings writes recheck their concrete scope and workspace inside the canonical
mutator. Already admitted validators, external authentication processes or native
controls can finish; revocation cannot undo those effects. Activation after an
awaited login is still checked. Passive grant reads never execute pending work.

These effects name administration domains, not mutually isolated privileges.
Module settings include provider configuration; bundle and mount-plan changes
can replace tools or providers and alter file-tool policy. Executable sources can
run code with the worker's ambient access. App defaults include update automation.
The approval labels disclose those broad effects. A narrow model-default grant
does not authorize arbitrary provider configuration: those routes preserve the
existing provider settings, and routing values reject credential material.

This is configuration control authority, not an operating-system sandbox.
Arbitrary shell programs and external modules can bypass supported settings
APIs. General update/install/deploy authority and real-time provider effects are
not covered by this grant slice. Neither a root grant nor assignment membership
grants stop/steer/settings authority over an independent peer.

## Read-history audit: separate existing behavior

Configuration grants do not grant or restrict history access. The current
single-user host exposes registered own-history through several independent
routes:

- `coordination.list` defaults to caller workspace; an explicit workspace can
  broaden discovery. `coordination.wait` accepts explicit registered targets.
- Exact-message `coordination.read` applies same-workspace authorization; its
  transcript-window variant delegates to history query with `scope=all`.
- History query and `recall.search(scope=all)` can traverse registered sessions;
  internal/child history has separate inclusion flags. `recall.read` checks the
  source index revision and registered source identity.
- Memory has task/workspace/global scopes, including explicit cross-scope reads.
  User-memory writes retain their separate attributable-human-source checks.
- Shared peer-read action policy includes registered session/history inspection.

These routes are not one consistent cross-workspace read-capability boundary.
They have existing registration, attribution, pagination and source-validation
mechanisms; no new read permission platform was added here. Cross-account or
multi-tenant isolation is not established by this local configuration work.

## Qualification boundary

Tests include positive user and agent administration, denied effects/scopes,
revocation while queued or preparing, durable approvals/restart, exact retries,
lost acknowledgments, Stop, peer lineage, and new scheduled roots. Native tests
mount Core/Foundation and the real app-control tool; their orchestrator is
scripted, without a model/provider call. The browser uses production assets and
the actual service with a controlled runtime. It covers Allow, grant reuse,
revoke, reload, retained private drafts and a narrow layout. The native waiter is
qualified separately from the scripted browser adapter.

Source, native, browser and installed-wheel evidence are distinct. These checks
do not establish real-provider cooperation, provider-login acceptance, optional
recipe integration, release publication, installed-host adoption or OS isolation.
