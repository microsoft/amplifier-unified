# Collaborative workspaces direction — 2026-10-05

## Source decision

The user approved the concrete Collaborative workspaces design, Canvas artifact `cf6f64090d234323882ba303e2539302`, version 4, in conversation `e1667af2-0125-4ce0-8c68-304b3a59ca52`. Its pre-decision labels remain historical; the approval below followed it.

User message `153af900-fda6-4668-a772-25fb98d5c8e2`:

> Ok, I like it, make it so please.  Get another perspective from fable via the second-opinion skill, then go ahead and implement and test in a DTU and then have fable also review the outcome of that and the code and then if all is good, lets PR it.

## Accepted meaning

- Shared folder-first workspaces, independent peer chats, durable agent-created root task chats, and actual delegated children remain distinct relationships.
- Task-scoped, attributed ongoing collaboration includes authorized messaging, necessary idle starts and durable task creation without asking again for each in-scope reply. User stop, permissions, configuration and publication boundaries remain effective.
- Cooperative shared-folder execution replaces the proposed universal checkout lease as the intended default; optional per-repository worktrees/copies support overlapping work and stable verification. This does not promise conflict-free shared edits or a filesystem sandbox.
- Existing native history, task/admission/receipt owners, and AHP/ACP boundaries remain authoritative. No competing transcript store or mandatory manager hierarchy.
- Implementation, DTU verification, independent Fable code/outcome review, and a PR if satisfactory are authorized. Merge and deployment are not.

The exact wording of `execution.v2-candidate.md` is a **derived encoding** of this accepted meaning, not text the user already reviewed. Accepted v1 files remain unchanged pending the applicable publication/ratification checks. Passing replacement-behavior tests does not establish v1's unimplemented single-writer guarantee.

## Review before implementation

Fable (`claude-fable-5-1`), review session `0000000000000000-30f87d0ef70741bc_self`, reviewed source `7a8c6a81337324895b323be5293ea763993372a7` and the supplied design brief. It required centralized equivalent-route authorization, model-visible host attribution, and a precise correlated-final-response definition before implementation. It did not run tests or verify a live collaboration. Implementation starts from `2e310274d3e401200d930c9e7a7e900563eaeaa1`; the intervening change is the v0.20.50 bundle-default release.

Review advice can refine implementation but does not silently reduce the accepted result. Runtime unsupported capabilities must be explicit rather than simulated by cancellation/replay. Implementation and qualification evidence belongs in the existing task-coordination and workspace implementation documentation; this decision record is not evidence of completion.
