# Shared landing pages — accepted follow-up, October 20

The product owner requested a Home icon in the sidebar’s existing first row,
consistent landing experiences for All workspaces, All chats and a workspace’s
main view, preservation of their capabilities, shared components, and correction
of the unhelpful Projects rename. The owner also supported the transcript review’s
orientation and filter recommendations. This record supplements the earlier
Home/sidebar decision; it does not rewrite the historical proposal.

The implementation scope is navigation and presentation, with existing catalog
queries and commands. Preserve independent drafts, selection, scope guards,
permissions, file handling, workspace inspection and bounded discovery. Preserve
the prior choice to omit redundant Home text when already on Home.

A clearer header Home control and a sidebar Home icon address observed discovery
difficulty. Visible status filters and a stable layout selector address the need
for coaching during the walkthrough. Display detail remains independent from
layout. Workspaces is the canonical name throughout these surfaces and pickers.

The owner confirmed that fresh windows open on Home, while refreshes, reconnects
and direct chat links preserve their destination. This retains the existing
web-view ownership boundary rather than introducing another startup policy.
Related-work inspection uses the existing Workspace work surface; grouping by
inferred dependencies or additional processing remains outside this change.

Review desktop, tablet and 320px views; find Home without coaching; return with a
status filter still selected; compare all three layouts; preserve each surface’s
filters and actions. Exercise archives, pins, creation/attachment, folder browsing,
plans and Workspace work. Evidence must distinguish synthetic acceptance from
human review with familiar data. Implementation and publication are separate.

## Implementation evidence

The retained editable DTU preview was checked on main base
`a17b62ee67a217d254bb14853640776a3bbf8cba`, with client build
`0.20.195 / 3700a91a0a8ecfcb`. This identifies a development build, not a release.
The focused checks passed: 113 frontend tests, 39 workspace-navigation tests,
the shared-landing, Home/sidebar and Focus browser workflows, and an authenticated
walkthrough of the LAN preview with retained synthetic chats.

The checks cover later-page Focus return, independent layout choices, retained
search when clearing status, existing action menus, workspace Files/Details,
Workspace work access, responsive and enlarged text layouts, keyboard/forced
colors, new-window arrival, refresh and direct chat links. Browsing did not start
runtimes or send model requests in the synthetic fixture. These are functional
checks; designer review with familiar live work remains separate.

Before the PR handoff, main was integrated through
`6e9743b91778a4fc7b3e678f716ba814232e7c93` and the same 113 frontend tests,
39 backend tests and three browser workflows passed again against
`0.20.199 / c92bddfe5110354f`. Two builds produced the same 311 client files.
This version identifies the development base; release publication is handled
separately from the retained preview and the user's live-instance adoption.
