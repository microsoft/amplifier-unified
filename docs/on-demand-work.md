# On-demand work details

Collapsed work groups ship status, timing, counts, usage and an opaque detail
validator. No execution nodes ship in the initial browser projection. Deploy the rebuilt
frontend with the service and reload open browser/PWA clients. The full internal
agent state interface remains unchanged.

Opening a visible group reads `/api/conversation/detail` with its session ID,
`part=nodes`, and group ID. Pages contain at most 100 steps. Earlier-step cursors
also carry the detail revision so an edit cannot mix two versions of a group.
Large step inputs/outputs retain their separate field readers. Group summaries
page independently with `part=groups`; their accounting covers the entire group.

The browser limits concurrent group reads to four, coalesces progress bursts,
rejects stale responses, supports retry, and releases fetched steps when the
group closes or its chat unmounts. Detailed presentation defers off-screen
reads. Opened groups can retain explicitly requested earlier pages while open.
Chat navigation caches only metadata and drafts, showing the existing loading
indicator until fresh selected-chat state arrives. Dirty canvas navigation
barriers remain intact.

Detail validators are process-local, not persisted identities. They include the
full source node values, including same-length result edits. Python caches
string hashes, avoiding repeated encoding or hashing of unchanged large inline
results; summary construction still walks the loaded execution tree.

## Validation

Owned DTU only, synthetic data, no provider requests. Browser acceptance covers
zero collapsed detail reads, paged expansion, independent output loading, failed
load/retry, closing during a request, reopening, live updates, and returning to a
cached chat while navigation is delayed. Backend coverage includes complete
accounting, cursor invalidation, independent clients.

A synthetic 125-step, three-group example with 8.14 MB of source execution JSON:

| Measurement | Before | After |
| --- | ---: | ---: |
| Initial execution payload | 87,402 bytes | 949 bytes |
| Inline steps | 100 | 0 |
| Median projection + serialization, 10 samples | 3.60 ms | 0.29 ms |

These are execution-payload measurements, not whole-chat memory or total CPU.
The server still retains the source execution history and computes summaries
from it. Native history reads, model context and compaction are separate paths.

## Next service reduction

The desired working set is active work plus open/recent chats and projects,
independent of total library size. Use small catalog/search indexes, paged library
queries and evictable detail caches. Do not load full histories merely to search
or compute sidebar counts. Active runs, approvals and unsaved state must remain
protected. Existing cold-display retirement already protects active/subscribed
chats and eight recent chats; measure eager startup view hydration and navigation
accesses before adding another cache or lowering that bound. Test at 24,000
sessions and 4,000 projects, measuring loaded body counts, RSS, CPU and latency.

Selected canonical Work reconstruction now has a bounded process/read-model owner.
See [selected history projection](selected-history-projection.md) for exact source
fences, conservative append recognition, cache and process limits, and the remaining
cold-tree materialization boundary.

### Resident live steps

The bundled browser keeps the initial live source slice at 100 resident nodes.
Explicit older reads inspect at most 100 preceding resident nodes per request;
imported canonical/native nodes remain excluded, and no transcript or child log
is opened. Empty filtered ranges still provide a progressing cursor. These views
report unknown totals and never claim complete historical coverage.

Each client’s open group retains only accepted step IDs and source-range receipts, not node
bodies. Across clients, up to eight groups, eight 100-row ranges per group, and 800 accepted step
IDs per group are admitted. This matches eight explicit detail pages while
bounding live-update reconstruction independently of the source graph's size.
A request beyond that budget fails explicitly and preserves the existing view;
it never silently drops prior steps or reports an exhausted history. Closing and
reopening starts a fresh bounded page; chat-interest departure retires receipts.

Cursors bind the host-owned source, bounded grouping facts, and publication fence.
Live sync reconstructs only admitted ranges plus the current tail. A new message
or reconstructed resident list can advance the open receipt when every retained
step still has the same ID, resident position, and group under the same session
and source owner. Terminal status and usage updates therefore settle without
closing the group. Reordered, removed, replaced, or reanchored retained steps
require reopening; sync never searches the full execution graph to recover them.
Explicit older cursors remain strict and sync issues a fresh cursor after a valid
advance. A source replacement or expired receipt requires reopening the group. Field
readers resolve only admitted resident ranges and retain exact digest validation.
Canonical execution, accounting, and independent complete APIs are unchanged.
