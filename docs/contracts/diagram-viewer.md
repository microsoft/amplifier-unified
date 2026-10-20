# Diagram viewing

Approved viewer improvements for #675, October 17, 2026.

Mermaid and DOT remain source-authored documents. The everyday viewer provides
immediately visible zoom/fit controls, local pointer-centered wheel zoom, drag,
touch and keyboard pan, readable layout names and optional node details. Viewer
preferences never edit document source. Node details start collapsed, leaving
the drawing most of the pane. Embedded Markdown diagrams retain ordinary page
scrolling rather than capturing wheel or touch gestures.

The paper surface and default typography, spacing, edges and colors are designed
for readable diagrams. Explicit DOT attributes and Mermaid styles take priority
over defaults. Full Mermaid syntax and DOT layout engines remain supported by
the existing renderers; no conversion to a narrower editable graph format occurs.
Diagram editing is a separate product decision.

Retain strict Mermaid security, sanitized SVG in an image boundary, the 50,000
character source limit, Graphviz worker isolation and timeout. Diagram interaction
renders locally and persists settled view changes, never one command per move.
Changing layout changes only that artifact's view; saved source stays identical.

Browser qualification must cover source/style preservation, accessible visible
controls with Canvas options closed, valid/invalid diagrams, pan/zoom persistence,
desktop/narrow fit, embedded scrolling and touch cancellation. Existing benchmark
and security assertions remain required rather than being replaced by screenshots.
