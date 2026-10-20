# Home and sidebar contract — v1 (RATIFIED 2026-10-20)

These promises govern the standard web client's Home and chat sidebar.

## Who builds against this

People returning to their work, designers, web-client builders, and catalog owners.
Shared meanings remain with [client experience](../../contracts/client-experience.v1.md);
state ownership with [client state](../../contracts/client-state.v1.md); resource limits
with [the working-set contract](../../contracts/working-set.v1.md); workspace actions with
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

1. **Use the same work facts.** Both surfaces use the canonical catalog's
   attention, unread-response, current-execution, and other-saved-chat meanings.
   Read/unread actions reflect the current state and use the existing action owner.
   Broken: unread offers Mark unread, “Ready” implies accepted work, or idle means complete.
2. **Offer detail without changing authority.** Appearance supplies the
   starting view; explicit view choice and available space stay independent.
   Essential actions remain reachable under the [Appearance contract](appearance-contract.md).
   Broken: choosing Focus hides the only required action or changes permissions.
3. **Return without rebuilding context.** Home remembers its view, search,
   scope and pages; its reading position and Focus pass survive visits in the same tab.
   Sidebar mode, expansion and pages remain independent under client-state ownership.
   Broken: returning or switching sidebar mode resets the person's other surface.
4. **Keep navigation consistently available.** Browse/Status shares one row;
   the header owns its toggle; sidebar Search and New chat remain available on Home.
   Both sidebar views reuse chat controls and honor the same display preferences.
   Home is a breadcrumb elsewhere, with no stale chat highlight on Home.
   Broken: a view ignores workspace-label settings or loses common actions.
5. **Browse a pass without dismissal.** Focus offers previous/next, position,
   and restart across bounded pages of its group. Its cursor follows chat identity.
   Stepping never answers, approves, marks read, archives, or starts work.
   Broken: skipping an item disposes of something the person meant to revisit.
6. **Make scope a truthful filter.** Project scope uses registered workspace
   identity and existing creation rules; filtering never moves work or changes access.
   A pinned or unavailable scope cannot silently widen or create elsewhere.
   Broken: the person acts on another project after a delayed or failed query.
7. **Keep unknown distinct from empty.** Initial loading, failed, unavailable,
   and empty results remain distinguishable; counts describe the current scoped query.
   Routine refresh keeps usable content silently, without inserting status text or shifting it.
   Cards open existing chats; stale rows never acquire a newly selected scope.
   Broken: a refresh makes content jump or a failed read says nothing needs review.
8. **Pay only for needed discovery.** Use the existing bounded catalog;
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

- Status meaning and uncertainty: seed attention, unread, working, idle, failure, and delayed-query cases;
  compare both surfaces with canonical facts, including read/unread actions.
  Delay a refresh and assert no new refresh text, row displacement, or loss of controls.
- Detail and navigation: exercise all views and both sidebar modes with keyboard, enlarged text,
  narrow screens and forced colors; verify shared row preferences and return focus.
- Return context: leave while typing a search, visit a chat/project, return and reload;
  compare Home, sidebar, draft, and Canvas state independently.
- Temporary passes: pass over an item, cross a page, reorder/remove an item, return and restart;
  verify attention, read state and runtime counters stay unchanged while stepping.
- Project scope: race scope/pin changes and unavailable projects with reads and creation;
  assert existing identity guards refuse incorrect targets.
- Bounded discovery: observe requested resources and runtime counters with hidden/closed groups;
  inspect query reuse and the absence of added inference in the changed paths.

## Open questions

- Which labels best distinguish unread responses from work ready for acceptance?
- How much surrounding context helps each view with a person's real work?
- Does “Projects” communicate the existing scope without suggesting regrouping?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-20 | Adopt the Home/sidebar promises, with plain names and quiet refresh/shared-row corrections. | [Product-owner decision](../ux/decisions/20261020.md); implementation evidence remains separate. |
