# Independent shell contribution

This example imports only the public SDK and uses host React. It adds compact
spacing controls, a conversation-status widget, or a custom Settings section.
It is an authoring example, not a default installed component.

Build it after installing the frontend toolchain:

```
node frontend/scripts/build-shell-controls-example.mjs
```

Stage `dist/manifest.json` and `dist/module.mjs` with `shell.packages.stage`, run
host validation, then add an instance to one of its declared slots using the
existing composition preview/apply flow. For `app.actions`, explicitly include
`builtin.app-actions` too if you want to keep the standard header buttons.

The Settings instance demonstrates durable module state and a pending edit
that blocks replacement. Its state does not modify the conversation draft.
See [the component contract](../../docs/shell/components.md) for capability,
client targeting, generation and native-code trust boundaries.
