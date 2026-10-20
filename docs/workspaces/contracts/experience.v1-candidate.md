# Workspace navigation amendment (DRAFT)

Target: `docs/workspaces/contracts/experience.v1.md`

Base: `5da1e1ce137866b780c37d0ebb9a4b63aecfcadc`

Target SHA-256: `0e0c7ecab735a12f9441a9714947da990cd8b3a0b8b2f9b6e6d9725244e948e5`

This proposes an amendment to the existing document, preserving its identity
and promise numbers. The candidate filename does not create a new API version.
The target remains unchanged pending review of the exact wording.

## The exact change

### Show both organizations

Current text:

```````markdown
```text
New chat
Pinned         selected conversations                 (collapsible)
Workspaces     workspace → its full conversation list (collapsible)
Recent         unpinned conversations, all locations   (collapsible)

Chat details → Outputs · Sources · Agents · Changes · Preview
Workspace details → Files · Chats · Directions · Settings
```
```````

Replacement:

```````markdown
```text
Browse organization
  Pinned       selected conversations                  (collapsible)
  Projects     registered workspace → full chat list   (collapsible)
  Recent       unpinned conversations, all locations   (collapsible)
Status organization
  Existing attention / unread / working / other saved chats

Chat details → Outputs · Sources · Agents · Changes · Preview
Workspace details → Files · Chats · Directions · Settings
```
```````

### Scope P2 to Browse and recognize Status

Current text:

```markdown
2. **Navigation uses shared controls.** Pinned, Workspaces, and Recent are the
   three collapsible sections. One rich chat row renders each pin consistently;
   one workspace explorer provides recent folders, folder browsing, and chat
   drill-in. Pins are excluded from global Recent and remain available in a
   workspace's full chat list. Helpers stay with their parent chat. Additional
   filters sit behind a disclosure. Collapse state persists independently.
```

Replacement:

```markdown
2. **Navigation uses shared controls.** Browse groups Pinned, Projects (registered
   workspaces), and Recent in shared collapsible sections. An alternate Status
   organization uses existing chat activity, not another work store; the web
   [Home/sidebar contract](../../shell/work-discovery.v1.md) owns its presentation.
   One rich chat row renders pins consistently; one workspace explorer provides
   recent folders, folder browsing, and chat drill-in. Pins remain excluded from
   global Recent and present in their workspace's full list. Helpers stay with their parent chat. Additional
   Browse filters use disclosure; collapse state persists independently.
```

## The evidence

P2 currently presents three sidebar sections as the sole organization and
puts additional filters behind disclosure. The product owner explicitly chose
a one-row Browse/Status switch, now shipped in v0.20.188. Those two descriptions
cannot serve as an unambiguous agreement without naming Browse's scope.
This amendment records the approved UI distinction while keeping the existing
workspace and native-chat owners.

## What does not change

Workspace membership, files, placement, execution, history, creation,
permissions, and the meaning of attention remain unchanged. P1 and P3–P8 retain
their numbers and wording. Project naming is a presentation of registered
workspaces, not a new cross-folder grouping feature. Workspace work, combined
checks, and review-before-continuing behavior are outside this amendment.

## Dependencies

Accept the new Home/sidebar contract at `docs/shell/work-discovery.v1.md` before applying its link.

## Decision

Pending. Record the exact reviewed revision, decision-maker, date, and accepted
wording: ratified / ratified with edits / declined / later. On acceptance,
apply only the agreed replacements and a dated changelog entry; retain this
proposal and its decision. No acceptance or implementation result is inferred.
