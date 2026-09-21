---
name: amplifier-shell
description: "Customize Amplifier Unified presentation or build shell extensions. USE WHEN changing layouts, themes, sidebar modules, or artifact viewers, interactive conversation surfaces, and validating hot-loaded components. DO NOT USE WHEN changing AmplifierSession tools, providers, or orchestration; use runtime bundle guidance."
---

# Amplifier Unified shell

Use this skill for the web shell. A shell module changes presentation; an
Amplifier behavior changes the agent's runtime configuration. This skill is
delivered by a behavior so agents can learn the shell's separate extension API.

## Discover the target

1. Read app state using `app_control(operation="get_state")`. Identify the
   user's intended browser client from `visibleUI.clientId`; never assume a
   global presentation or choose an arbitrary attached client. Follow
   `$statePath` references with bounded reads when the overview omits details.
2. Request current action schemas using `app_control(operation="list_actions",
   args={"prefix":"shell."})` and, for artifact views,
   `args={"prefix":"canvas.views."}`.
   Inspect the relevant shell or view before preparing a change. For shell
   components, use the returned `slots`, `registry`, `resolvedInstances` and
   `componentCommands`; only author against contracts that host exposes. Discover
   theme actions separately when a CSS skin is requested.
3. If working outside an app session, use the host's authenticated
   `GET /api/actions` and `POST /api/actions` interfaces through the user's
   configured connection. If no connection exists, author locally and report
   activation as unverified. Never read credential files to invent a connection.

**Success criteria:** An explicit target client and the current supported
actions are known, or work is clearly limited to an offline package.

## Choose the workflow

- **Change existing presentation:** Read [presentation.md](presentation.md).
  Reuse installed modules, layouts and themes before creating code.
- **Create interactive UX for a conversation:** Read
  [conversation-surfaces.md](conversation-surfaces.md). Keep one surface ID and
  refine its existing tab, preserving shared user/agent state.
- **Build or replace a component:** Read [authoring.md](authoring.md), then the
  relevant contract in [the shell guide](../../docs/shell/README.md).
  The public SDK is [packages/shell-sdk](../../packages/shell-sdk/index.d.ts).

These paths are relative to this skill's directory. The same resource layout
is included in installed app packages; a development checkout is not required
to read the guide, SDK, or examples. Locate companions relative to the path
returned by `load_skill`, not the conversation's workspace.

## Preserve ownership and verify

Use shared actions and the public SDK. Canonical conversations, drafts,
sessions, resources and tool execution belong to the host. Keep passive
navigation passive. Read-only browsing must not select a chat or send context.
Declare unsaved component edits with `host.setDirty`; retain dirty components
until their edits are resolved. Never use recovery to silently discard edits.

Use current revisions and view generations from inspection. Retry uncertain
mutations with the same request ID and identical arguments. Validation approval
is distinct from activation, and activation is distinct from user acceptance.
Finish with the changed client/module/view, browser evidence, preservation
checks, and a usable revert path. State any missing live validation explicitly.
