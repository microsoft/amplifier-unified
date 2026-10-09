# Workspace execution v2 — candidate

Status: exact clause proposal encoding the [approved direction](collaborative-workspaces-decision.md), not publication of a replacement contract. Target: `execution.v1.md` as read at source `2e310274d3e401200d930c9e7a7e900563eaeaa1`. The accepted target remains unchanged. Newly authored normative wording needs the applicable decision/publication checks.

## Evidence and reason

The user reported effective cross-chat coordination in their OpenAI workflow and asked Unified to adopt a productive collaboration model, not make checkout locking its prerequisite. Read-only inspection of that installation found independent root task conversations, substantial recorded peer messaging/waiting, and command-specific worktree locations while the chat's starting directory stayed a shared project root. These observations establish actual use and placement flexibility, **not** conflict-free shared editing or measured cost of serialization.

Unified's existing `IMPLEMENTATION.md` explicitly lists cross-session conflicting-writer coordination as unfinished. Removing the universal claim therefore need not remove an implemented lease here, but other consumers' reliance has not been exhaustively checked. The cost/trade-off is explicit: cooperative sharing permits concurrent work and avoids mandatory copies, but cannot guarantee prevention of competing edits. Stable verification still needs a preserved or quiesced candidate.

## Exact replacements

### Definition paragraph

Replace the paragraph beginning “A change set groups” with:

> A change set groups one piece of ongoing work across repository checkouts or document drafts. Its execution bindings carry a revision, explicit locations, and the declared sharing mode. A conversation has one native execution owner; that ownership is not a checkout-wide write lock. An isolated working directory is not a security sandbox.

Replace the two illustrative JSON records with:

```json
{"changeSetId":"change1","sessionId":"native-session-1","mode":"workspace-folder",
 "bindings":[{"repositoryId":"repo1","baseCommit":"abc123","checkoutId":"co1","sharing":"cooperative"}],
 "executionRevision":4}
{"helperId":"helper1","parentId":"native-session-1","access":"read-snapshot",
 "snapshotId":"snapshot1","scratchId":"scratch1"}
```

### Promise 3

Replace promise 3 with:

> **Sharing is explicit.** A checkout may be shared cooperatively by independent conversations. Its reported sharing mode must not imply enforced single-writer protection. Supported version-aware write APIs refuse stale-source updates; arbitrary shell programs and external editors remain outside that guarantee. A protected execution backend, when supplied, must report its actual enforcement and refuse stale owner generations before claiming protection.

### Promise 5

Replace promise 5 with:

> **Concurrency choices belong to tasks.** Ordinary work remains in the chosen shared folder. Independent tasks may coordinate repository/file responsibilities or use separate per-repository working copies when overlap, experimentation or stable verification calls for isolation. Each chosen path and baseline is visible. Creating or attaching a conversation does not silently relocate it, include unrelated dirty work, or require an execution-mode decision before discussion.

### Promise 6

Replace promise 6 with:

> **Bindings describe actual execution.** Preserve the canonical history/configuration home separately from default execution directory, per-repository checkout bindings and actual operation cwd. Shell commands, file tools, dependencies, tests and supported preview launchers must identify the binding actually used. A claim about a preserved snapshot must not silently import a peer's mutable dependency or test changing source. Ordinary explicit paths remain subject to the executor's actual permissions.

### Not in v2

Retain the hard-isolation limitation. Replace the automatic-background-relocation item with:

> **Automatic background relocation** requires explicit task-level authority and visible placement. A declared sharing mode or cooperative file plan neither grants new filesystem permissions nor substitutes for protected execution.

### Checks

Replace P3 with:

> P3: inspect two cooperative writers and verify their mode does not claim protection; race a supported version-aware file write and require an explicit stale-source result. For any backend advertising protected ownership, restart/reassign and reject writes with the old generation. An unsupported protection check remains unsupported, not a pass.

Replace P5 with:

> P5: run coordinated tasks in the chosen shared folder and an explicitly isolated multi-repository fixture. Verify actual paths, retained original work, declared sharing modes and versioned test inputs. No communication operation alone relocates source or claims isolation.

Other checks remain unchanged; apply the revised promise 6 to P6's source/runtime provenance check.

## What does NOT change

Native history and one execution owner per conversation; passive inspection; folder attachment preservation; bounded child assignments; scope and tool permissions; private drafts; original-document protection; exact-version and dependency evidence where claimed; deliberate integration, publication and cleanup; user stop/revocation; AHP/ACP interoperability. Peer collaboration does not convert independent chats into children or grant unrestricted authority.

## Migration and acceptance

No current universal checkout fence is known in this source baseline; this is not proof that all integrations are independent of the earlier promise. Existing explicit worktree handoffs, permission checks and preservation checks stay in place. Consumers must distinguish cooperative from genuinely protected bindings and report unsupported enforcement. The first collaboration increment should retain existing folder behavior, add honest context/coordination, and not claim newly enforced write isolation.

Before publication: verify target revision and exact payload, decision coverage, affected consumers/checks and dated changelog through the supported publication guard; preserve this proposal. A refusal stops publication, not ordinary already-authorized implementation. DTU and Fable findings will be recorded against the exact implementation revision; none are claimed by this candidate.
