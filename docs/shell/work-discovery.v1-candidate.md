# Home and sidebar contract — v1 (DRAFT)

Target: `docs/shell/work-discovery.v1.md` (new). Clause prefix: **WD**.
These proposed promises govern the standard web client's Home and chat sidebar.

## Who builds against this

People returning to their work, designers, web-client builders, and catalog owners.
Shared meanings remain with [CX](../../contracts/client-experience.v1.md);
state ownership with [CS](../../contracts/client-state.v1.md); resource limits
with [WS](../../contracts/working-set.v1.md); workspace actions with
[workspace experience](../workspaces/contracts/experience.v1.md).

## What it is

Home helps a person choose what to engage with. The sidebar helps them reach
familiar work or inspect its status while keeping their current context.

```text
Home: Focus / Brief / Studio + current search and scope
Sidebar: Browse / Status + New chat and Search chats
Browse: Pinned / Projects / Recent
Status: Needs you / Ready for you / Working / Pick up again
Choose a chat -> existing conversation and its existing action controls
Return Home -> remembered view, search, scope, reading position and Focus pass
```

## The promises

1. **WD1 — Use the same work facts.** Both surfaces use the canonical catalog's
   attention, unread-response, current-execution, and other-saved-chat meanings.
   Broken: “Ready” implies accepted work, idle means complete, or the two surfaces
   contradict the same observed state. Affected: people deciding what to review.
2. **WD2 — Offer detail without changing authority.** Appearance supplies the
   starting view; explicit view choice and available space stay independent.
   Essential actions remain reachable under the [Appearance contract](appearance-contract.md).
   Broken: choosing Focus hides the only required action or changes permissions.
3. **WD3 — Return without rebuilding context.** Home remembers its view, search,
   scope and pages; its reading position and Focus pass survive visits in the same tab.
   Sidebar mode, expansion and pages remain independent under CS2's ownership.
   Broken: returning or switching sidebar mode resets the person's other surface.
4. **WD4 — Keep navigation consistently available.** Browse/Status shares one row;
   the sidebar toggle stays in the header; sidebar Search and New chat stay available
   on Home. Home is a breadcrumb elsewhere, with no stale chat highlight on Home.
   Broken: scrolling one surface removes the other surface's common actions.
5. **WD5 — Browse a pass without dismissal.** Focus offers previous/next, position,
   and restart across bounded pages of its group. Its cursor follows chat identity.
   Stepping never answers, approves, marks read, archives, or starts work.
   Broken: skipping an item disposes of something the person meant to revisit.
6. **WD6 — Make scope a truthful filter.** Project scope uses registered workspace
   identity and existing creation rules; filtering never moves work or changes access.
   A pinned or unavailable scope cannot silently widen or create elsewhere.
   Broken: the person acts on another project after a delayed or failed query.
7. **WD7 — Keep unknown distinct from empty.** Pending, failed, unavailable, and
   empty results remain distinguishable. Counts describe the current scoped query.
   Cards open existing chats; action labels explain the existing reason for attention.
   Broken: a failed read says nothing needs review or stale rows acquire a new scope.
8. **WD8 — Pay only for needed discovery.** Use the existing bounded catalog;
   identical queries share its projection and hidden/closed Status groups release demand.
   Browsing reads no transcripts and starts no dormant worker or added model/summary job.
   Broken: merely opening this UI adds execution or work proportional to all history.

## Not in v1

- Logical projects spanning folders or moving chats between them. Promote after
  a separate membership and preservation proposal is accepted.
- New decision/deliverable records, review screens, lifecycle, or classification.
  Promote only when a demonstrated journey needs them and their owner is agreed.
- Fixed typography, breakpoints, column counts, or permanent card arrangements.
  Revisit through comparative review while preserving the promises above.
- Additional inference. An existing summary's input/output may be adjusted
  without extra calls; more processing requires a separate cost/scope decision.

## How the kit checks it

- WD1/WD7: seed attention, unread, working, idle, failure, and delayed-query cases;
  compare both surfaces with canonical facts and check scoped counts and labels.
- WD2/WD4: exercise all views and both sidebar modes with keyboard, enlarged text,
  narrow screens and forced colors; keep required actions and return focus usable.
- WD3: leave while typing a search, visit a chat/project, return and reload;
  compare Home, sidebar, draft, and Canvas state independently.
- WD5: pass over an item, cross a page, reorder/remove an item, return and restart;
  verify attention, read state and runtime counters stay unchanged while stepping.
- WD6: race scope/pin changes and unavailable projects with reads and creation;
  assert existing identity guards refuse incorrect targets.
- WD8: observe requested resources and runtime counters with hidden/closed groups;
  inspect query reuse and the absence of added inference in the changed paths.

## Open questions

- Which labels best distinguish unread responses from work ready for acceptance?
- How much surrounding context helps each view with a person's real work?
- Does “Projects” communicate the existing scope without suggesting regrouping?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-20 | Draft for joint product and designer review. | [Review basis and limits](../ux/review.md); no ratification or new runtime qualification. |
