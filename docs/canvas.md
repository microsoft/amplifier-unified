# Workspaces and agent canvas

Workspace registrations are durable app state. `workspace.add` accepts an existing
folder and optional name; `workspace.select`, `workspace.rename`, and
`workspace.remove` operate on its ID. Selection sets the new-chat default.
Removing a registration never deletes its folder or conversations. Keep at least
one workspace registered. `workspaces` and `selectedWorkspaceId` appear in the
same state visible to the agent and user.

`canvas.show` takes `kind` (`text`, `markdown`, `code`, `image`, or `a2ui`) and an
optional title. Text kinds accept `content` (up to 1 MB) or a UTF-8 file `path`.
Image kind accepts a workspace file path or a base64 image data URL (up to 5 MB);
PNG, JPEG, WebP, and GIF are supported. File paths must resolve inside the selected
workspace, including symlink targets. HTML and SVG are never executed or embedded
as active documents. The canvas does not fetch external content.

A2UI is an initial **snapshot subset**, not a full streaming implementation.
It uses the [A2UI v0.8 adjacency-list component shape](https://a2ui.org/specification/v0.8-a2ui/):

```json
{
  "kind": "a2ui",
  "title": "Next step",
  "surface": {
    "surfaceId": "next-step",
    "root": "root",
    "components": [
      {"id": "root", "component": {"Column": {"children": {"explicitList": ["intro", "button"]}}}},
      {"id": "intro", "component": {"Text": {"text": {"literalString": "Review the plan."}}}},
      {"id": "button", "component": {"Button": {"child": "label", "action": {"name": "review"}}}},
      {"id": "label", "component": {"Text": {"text": {"literalString": "Review"}}}}
    ]
  }
}
```

Supported components: `Text` with literalString and optional usageHint; `Row`
and `Column` with explicitList children; `Card` with child; `Button` with child
and an action containing name; `Divider` with no properties. Surfaces support up
to 100 components in a tree and 20 nesting levels, with a maximum 100 KB JSON snapshot.
Bindings, templates, URLs, arbitrary styles, scripts, and full JSONL protocol
messages are not yet supported. Invalid or cyclic surfaces are rejected.

Buttons dispatch `canvas.event` with `surfaceId`, `componentId`, and `name`.
These become bounded, timestamped `canvas.events` visible through the app bridge.
They **do not automatically execute commands or send a chat turn**. The agent
can inspect events during its next interaction and update the surface with
`canvas.show`. `canvas.close` preserves the last content. Layout controls such
as `canvasWidth`, `navPinned`, and `navExpanded` are in shared `view` state.
