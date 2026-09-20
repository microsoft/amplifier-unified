# Amplifier web frontend

React SPA served by the Python host. The compiled application is committed in `../amplifier_web/static/`; installing or launching the Python package does not require Node.

```
npm install
npm run build
npm test
```

For frontend development, run the Python server on port 8765, then `npm run dev`.

## Shared interaction surface

All controls issue commands through `POST /api/actions`. The server owns app state and publishes complete snapshots over `/api/events`. The same commands are available through `window.amplifier.getActions()` and `window.amplifier.dispatch(name, args)`. `window.amplifier.getState()` includes the latest server snapshot plus visible controls, selected text, and visible screen text. The page posts that view to `/api/view` for the backend agent bridge. Inputs, drafts, open dialogs, layout, and theme editor state are included in the shared view.

Browser effects (call media, notification permission, and file download) are delivered in `deviceCommands`, with command IDs deduplicated against HTTP responses. Existing effects are not replayed when opening the app. Microphone and notification grants remain browser permission prompts.

## Themes

`src/unified.css` is the complete Amplifier Unified skin. It includes an embedded Amplifier logo from microsoft/amplifier and has no external asset dependencies. The build always links a real CSS file in HTML; theme application does not depend on enabling a disabled stylesheet or on `@scope` support. The same source is copied to `static/unified.amplifier.css` for reset and export.

The stable root is `#amp-one`. Named `data-part` surfaces include `header`, `brand`, `session-heading`, `workspace`, `conversation`, `modalities`, `voice`, `notification`, `messages`, `composer`, `work-column`, `workers`, `approvals`, `context`, `overlay`, and `dialog`. A skin can replace variables or any component styles. The Appearance panel supports editing, importing, previewing, applying, reverting, and exporting the full CSS file. Applied skins are validated by the Python service; use embedded data assets rather than remote URLs.

## Validation

The test suite verifies same-origin JSON commands, surfaced server errors, packaged stylesheet delivery, asset existence, and standalone theme consistency. Production bundling validates JSX/imports. Browser visual automation was unavailable in the current environment, so a browser-rendered layout inspection remains outstanding.
