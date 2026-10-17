# Saved MCP App contract review

Approved implementation scope: October 17, 2026, issues #807 and #808. This
extends the saved-view contract in [Smart Tools](../SMART-TOOLS.md), without
changing MCP, account consent, device permissions or the self-contained sandbox.

Saved HTML, its launch arguments/result and the domain object they identify are
durable history. Reconnecting must not execute the launcher, recreate domain
objects, replay a previous call or automatically replace a mounted document.
Explicit historical selection remains read-only.

New saved bindings retain bounded definition snapshots in the resource store.
Older fingerprints cannot reconstruct prior definitions: reviews label that
comparison unavailable. Discovery generations are not schema changes. New tools
remain outside the saved allow-list; descriptions-only changes are shown and
still require explicit review, rather than inferring compatibility.

`smartTools.reviewView` reads the current resource through the already connected
MCP server and returns a bounded comparison and exact review fingerprint. It does
not call tools, grant actions or replace a document. Configuration and account
differences remain separate blocking dispositions and require Settings review.
Changed resource identity is blocked. Requested CSP/device permission changes are
shown, but no additional permission is granted by either review or application.

`smartTools.updateView` requires a human caller, the inspected binding revision
and exact review fingerprint bound to the reviewing client and selection generation.
Navigating away and back requires a new review; another client cannot reuse the
approval fingerprint. It re-reads the resource and revalidates selection,
account, configuration, catalog, schemas and document bytes before committing.
Existing action names are retained only while still app-visible. Added actions
are never granted. A contract-only acceptance preserves the existing iframe and
input. Adopting current HTML requires an additional explicit acknowledgement
that this caller's unfinished tool input will be reset. It saves a new immutable
version of the same artifact, with the same launch result/arguments/domain IDs.
Other clients already displaying that artifact retain their old document as a
read-only saved version, including their local iframe input. They choose when to
open Latest. A changed review, lost source, navigation or revoked authority fails
without a tool call or partial replacement.

The generic host cannot make an arbitrary old saved app consume a notification.
Existing documents receive official SDK list-change signals after verified
reconnection. Recovery qualification must identify the actual immutable app
bytes and distinguish cooperative startup recovery from old app behavior. A
guarded, explicit reload is a document replacement, never a silent retry of work.
