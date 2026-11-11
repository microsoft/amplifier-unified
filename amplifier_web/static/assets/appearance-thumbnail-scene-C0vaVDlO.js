import{R as a}from"./client-5nj4ZH6s.js";import{d as o,s as i,e as n,f as r,g as p,r as s,h as l,i as d}from"./app-mm-51YsD.js";const c=`html,body,#root{margin:0;min-height:100%;width:100%}body{background:var(--boot-bg,#e8eeff)}button,input,textarea,select{font:inherit}button:disabled{cursor:not-allowed!important;opacity:.48}.boot{font:16px system-ui;padding:64px;color:var(--boot-ink,#263459)}.boot img{width:56px}.boot button{padding:10px 18px;margin-top:12px;border-radius:12px;border:1px solid #ccc;background:white}
.a-updates{margin-top:24px;padding-top:20px;border-top:1px solid var(--border,#dce3ef)}
.a-update-options{display:grid;gap:12px}.a-update-options label{display:flex;align-items:center;gap:10px;margin:0}.a-update-options input[type=checkbox]{width:18px;height:18px;flex-shrink:0}.a-update-list{padding:0;list-style:none;max-height:300px;overflow:auto}.a-update-list li{display:grid;gap:3px;padding:12px 0;border-bottom:1px solid var(--border,#dce3ef);overflow-wrap:anywhere}.a-update-list small,.a-update-list span{opacity:.75}.a-update-count{font-size:var(--a-text-base,14px);padding:3px 7px;border-radius:12px;background:#e0e7ff;color:#3730a3}.a-settings-dot{width:7px;height:7px;border-radius:50%;background:#6750e8;position:absolute;top:4px;right:4px}

.a-diagnostic-streams{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem;margin:.8rem 0}.a-diagnostic-destinations{display:grid;gap:.5rem;margin:.8rem 0}

.a-diagnostic-streams .a-inline-checkbox{margin:0!important;min-height:44px;align-items:center}
`,m=`/* Neutral shared foundation. Gallery appearances supply palette, type and art
   deltas; this file remains available as the non-gallery recovery identity. */
#amp-one {
  color-scheme: light dark;
  --a-bg: light-dark(#f2f4f4, #151a1d);
  --a-surface: light-dark(#ffffff, #202629);
  --a-soft: light-dark(#e9edee, #292f33);
  --a-ink: light-dark(#1d2528, #e9eff0);
  --a-muted: light-dark(#566368, #aeb9bc);
  --a-line: light-dark(#cbd3d5, #414c50);
  --a-accent: light-dark(#48646d, #a4d5df);
  --a-on-accent: light-dark(#ffffff, #10272d);
  --a-tint: light-dark(#e2edef, #263a40);
  --a-green: light-dark(#21644e, #8dd7b4);
  --a-danger: light-dark(#a63f4c, #ffadb5);

  --a-font-ui: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --a-font-heading: var(--a-font-ui);
  --a-font-chat: var(--a-font-ui);
  --a-font-code: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --a-font: var(--a-font-ui);

  --a-background-art: none;
  --a-navigation-art: none;
  --a-conversation-art: none;
  --a-canvas-art: none;
  --a-chrome-bg: var(--a-bg);
  --a-chrome-ink: var(--a-ink);
  /* The host clamps the optional native-header decoration strip to 24px. */
  --a-header-decoration-height: 0px;

  --a-radius: var(--a-card-radius, 12px);
  --a-panel-gap: 0px;
  --a-panel-radius: 0px;
  --a-panel-border-width: 0px;
  --a-panel-line: var(--a-line);
  --a-panel-shadow: none;
  --a-navigation-pad: 16px;
  --a-card-radius: 12px;
  --a-control-radius: 8px;
  --a-composer-radius: 14px;
  --a-message-radius: 16px;
  --a-message-gap: 24px;
  --a-composer-pad: 12px;
  --a-card-pad: 14px;
  --a-card-line: var(--a-line);
  --a-surface-shadow: none;

  color: var(--a-ink);
  background-color: var(--a-bg);
  background-image: var(--a-background-art);
  background-repeat: no-repeat;
  background-position: center;
  background-size: cover;
  font-family: var(--a-font-ui);
  font-size: var(--a-text-base, 14px);
  line-height: 1.5;
  scrollbar-color: var(--a-line) var(--a-bg);
}

#amp-one[data-theme-scheme="light"] {
  color-scheme: light;
}

#amp-one[data-theme-scheme="dark"] {
  color-scheme: dark;
}

#amp-one :where(*, *::before, *::after) {
  box-sizing: border-box;
}

#amp-one [hidden] {
  display: none !important;
}

#amp-one .a-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

#amp-one :where(button, input, select, textarea) {
  font: inherit;
}

#amp-one :where(button) {
  color: inherit;
  border: 0;
  background-color: transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
}

#amp-one :where(svg) {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}

#amp-one :where(button:not(:disabled), [role="button"]:not([aria-disabled="true"])) {
  cursor: pointer;
}

#amp-one :where(input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="hidden"]), select, textarea) {
  min-width: 0;
  max-width: 100%;
  border: 1px solid var(--a-line);
  border-radius: var(--a-control-radius);
  color: var(--a-ink);
  background-color: var(--a-surface);
}

#amp-one :where(input[type="checkbox"], input[type="radio"], input[type="range"]) {
  accent-color: var(--a-accent);
}

#amp-one :where(.a-primary, .a-send) {
  color: var(--a-on-accent);
  background-color: var(--a-accent);
}

#amp-one :where(.a-user > .a-message-body) {
  background-color: var(--a-tint);
}

#amp-one .a-attention-badge {
  color: var(--a-on-accent);
  background-color: var(--a-accent);
}

#amp-one :where(h1, h2, h3, h4, h5, h6, .a-brand) {
  font-family: var(--a-font-heading);
}

#amp-one :where(pre, code, kbd, samp) {
  font-family: var(--a-font-code);
}

#amp-one [data-part="header"] {
  color: var(--a-chrome-ink);
  background-color: var(--a-chrome-bg);
}

#amp-one :where([data-part="navigation"], [data-part="conversation"], [data-part="canvas"], [data-part="composer-surface"]) {
  color: var(--a-ink);
  background-color: var(--a-surface);
}

/* The viewport stays fixed while chat panes own bounded, independent scroll. */
html,
body,
#root {
  height: 100%;
  height: 100dvh;
  overflow: hidden;
}

#amp-one.a-chat-shell {
  width: 100%;
  max-width: none;
  height: 100dvh;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

#amp-one.a-chat-shell .a-layout {
  position: relative;
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

#amp-one .a-nav-slot {
  position: relative;
  flex: 0 0 52px;
  width: 52px;
  min-width: 52px;
  min-height: 0;
  align-self: stretch;
  z-index: 15;
}

#amp-one.a-chat-shell .a-layout[data-pane-layout] .a-nav-slot[data-docked="true"] {
  flex: 0 0 var(--nav-width);
  width: var(--nav-width);
}

#amp-one .a-nav-rail {
  position: absolute;
  inset: 0 auto 0 0;
  width: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

#amp-one .a-canvas-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  align-self: stretch;
}

#amp-one.a-chat-shell .a-layout[data-pane-layout]>.a-canvas-panel {
  flex: 0 0 var(--canvas-width);
  width: var(--canvas-width);
  max-width: none;
}

#amp-one.a-chat-shell .a-layout[data-canvas-overlay="true"]>.a-canvas-panel {
  position: absolute;
  inset: 0;
  z-index: 20;
  width: auto;
  flex: none;
}

#amp-one.a-chat-shell .a-layout[data-pane-layout]>.a-canvas-panel[data-focused="true"] {
  position: fixed;
  inset: 0;
  z-index: 60;
  width: auto;
  max-width: none;
  height: 100dvh;
}

#amp-one.a-chat-shell .a-conversation {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

#amp-one.a-chat-shell .a-layout[data-pane-layout] .a-conversation {
  container-type: inline-size;
  container-name: conversation;
}

#amp-one.a-chat-shell .a-layout[data-canvas-overlay="false"]:has(>.a-canvas-panel:not([hidden]):not([data-focused="true"])) .a-conversation {
  min-width: var(--chat-min);
}

#amp-one.a-chat-shell .a-messages {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  max-height: none;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

#amp-one.a-chat-shell .a-messages > * {
  min-width: 0;
  max-width: 100%;
  flex-shrink: 0;
}

#amp-one.a-chat-shell .a-composer {
  flex: 0 0 auto;
  min-width: 0;
}

/* Shared modal primitives; settings and viewport-specific layouts layer on top. */
#amp-one .a-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 24px;
  background-color: color-mix(in srgb, var(--a-ink) 24%, transparent);
}

#amp-one .a-dialog {
  position: relative;
  width: min(620px, 100%);
  min-width: 0;
  max-width: 100%;
  max-height: 90dvh;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 26px;
  border: 1px solid var(--a-line);
  border-radius: var(--a-composer-radius, 16px);
  color: var(--a-ink);
  background-color: var(--a-surface);
  box-shadow: var(--a-surface-shadow, none);
}

#amp-one .a-dialog.wide {
  width: min(860px, 100%);
}

#amp-one .a-dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 22px;
}

#amp-one .a-dialog label {
  display: block;
  margin: 16px 0 6px;
  color: var(--a-muted);
  font-size: var(--a-text-base, 14px);
}

#amp-one .a-dialog :where(input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"]):not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="reset"]), select, textarea) {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  padding: 10px 12px;
  background-color: var(--a-soft);
}

#amp-one .a-dialog textarea {
  min-height: 90px;
  resize: vertical;
}

#amp-one .a-dialog .a-css-editor {
  min-height: 220px;
  font: var(--a-text-base, 14px) / 1.6 var(--a-font-code);
  tab-size: 2;
}

#amp-one .a-dialog p {
  font-size: var(--a-text-base, 14px);
  line-height: 1.65;
}

#amp-one .a-dialog-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  margin-top: 20px;
}

/* The native action, not an overlay or its label, owns the touch target. */
@media (pointer: coarse) {
  #amp-one .a-dialog-actions button {
    min-height: 44px;
  }
}

#amp-one .a-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

#amp-one .a-form-grid > * {
  min-width: 0;
}

#amp-one .a-caption {
  margin-top: 12px;
  color: var(--a-muted);
  font-size: var(--a-text-sm, 13px);
  line-height: 1.5;
}

/* Inline menus anchor to their control; portaled composer menus supply measured coordinates. */
#amp-one.a-chat-shell .a-model-control {
  position: relative;
  min-width: 0;
}

#amp-one.a-chat-shell .a-model-trigger {
  max-width: 100%;
  min-width: 0;
  min-height: 36px;
  gap: 6px;
  padding: 4px 8px;
  border-radius: var(--a-control-radius);
  text-align: left;
  font-size: var(--a-text-base, 14px);
}

#amp-one.a-chat-shell .a-model-trigger > span {
  min-width: 0;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

#amp-one.a-chat-shell .a-model-trigger small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--a-text-xs, 12px);
  line-height: 1.1;
}

#amp-one.a-chat-shell .a-model-trigger svg {
  width: 13px;
  height: 13px;
}

#amp-one.a-chat-shell .a-model-popover {
  position: absolute;
  bottom: calc(100% + 12px);
  left: 0;
  z-index: 20;
  width: min(390px, calc(100vw - 24px));
  max-width: calc(100vw - 24px);
  max-height: min(520px, 65dvh);
  overflow: auto;
  overscroll-behavior: contain;
  padding: 18px;
  border: 1px solid var(--a-line);
  border-radius: var(--a-card-radius);
  color: var(--a-ink);
  background-color: var(--a-surface);
  box-shadow: var(--a-surface-shadow, none);
  font-size: var(--a-text-base, 14px);
}

#amp-one.a-chat-shell .a-model-popover.a-compact-popover {
  position: fixed;
  bottom: auto;
}

#amp-one.a-chat-shell .a-model-popover .a-settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

#amp-one.a-chat-shell .a-model-popover p {
  margin: 8px 0;
}

#amp-one.a-chat-shell .a-model-popover label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 14px 0 6px;
  font-size: var(--a-text-base, 14px);
}

#amp-one.a-chat-shell .a-model-popover :where(select, input) {
  width: 100%;
  min-width: 0;
}

#amp-one.a-chat-shell .a-model-popover input[type="range"] {
  height: 22px;
  padding: 0;
  border: 0;
  background: transparent;
}

#amp-one.a-chat-shell .a-effort-labels {
  display: flex;
  justify-content: space-between;
  color: var(--a-muted);
  font-size: var(--a-text-sm, 13px);
}

#amp-one.a-chat-shell .a-model-popover .a-dialog-actions {
  gap: 8px;
  margin-top: 18px;
}

/* Shared attachment cards; component CSS retains its larger touch targets. */
#amp-one.a-chat-shell .a-composer .a-file-input {
  display: none;
}

#amp-one.a-chat-shell .a-attachments {
  display: flex;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  max-height: 110px;
  overflow-x: auto;
  padding: 3px 0 9px;
}

#amp-one.a-chat-shell .a-attachment {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: min(260px, 100%);
  flex: 0 0 auto;
  padding: 7px;
  border: 1px solid var(--a-line);
  border-radius: var(--a-card-radius);
  background-color: var(--a-surface);
}

#amp-one.a-chat-shell .a-attachment img {
  display: block;
  width: 46px;
  height: 46px;
  flex: none;
  border-radius: var(--a-control-radius);
  object-fit: cover;
}

#amp-one.a-chat-shell .a-attachment > :where(.a-attachment-preview-name, .a-link, a:not([download])) {
  display: flex;
  flex: 1 1 auto;
  align-items: flex-start;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  overflow-wrap: anywhere;
  text-align: left;
}

#amp-one.a-chat-shell .a-attachment > a:not([download]) {
  color: var(--a-ink);
  text-decoration: none;
}

#amp-one.a-chat-shell .a-attachment small {
  display: block;
  color: var(--a-muted);
  font-size: var(--a-text-xs, 12px);
}

#amp-one.a-chat-shell .a-attachment :is(a[download], .a-icon) {
  flex-shrink: 0;
}

#amp-one.a-chat-shell .a-attachment .a-icon {
  width: 25px;
  height: 25px;
}

@media (max-width: 600px) {
  #amp-one .a-overlay {
    padding: 12px;
  }

  #amp-one .a-dialog {
    max-height: calc(100dvh - 24px);
    padding: 20px;
  }

  #amp-one .a-form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* Keep selection paint on the row wrapper so child controls cannot clip it. */
#amp-one :where(.a-nav-chat:not(.a-nav-chat-rename), .a-workspace-row) {
  display: flex;
  align-items: center;
  min-width: 0;
  box-sizing: border-box;
  padding-inline-start: 8px;
  padding-inline-end: 4px;
  border-radius: var(--a-control-radius);
}

#amp-one :where(.a-nav-chat-select, .a-workspace-select) {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  text-align: left;
}

#amp-one :where(.a-nav-chat.is-selected, .a-nav-chat[aria-current="page"], .a-workspace-row.is-selected) {
  box-shadow: inset 3px 0 var(--a-accent);
}

#amp-one .a-message-body {
  min-width: 0;
  max-width: 100%;
  color: var(--a-ink);
  font-family: var(--a-font-chat);
  overflow-wrap: anywhere;
}

#amp-one :where(.a-message-actions, .a-message-attribution, .a-message-channel, .a-message-date) {
  color: var(--a-muted);
  font-family: var(--a-font-ui);
}

/* Delivery recovery remains host-owned when the appearance foundation changes. */
.a-message-delivery{display:inline-flex;align-items:center;gap:.4rem;font-size:.8rem;color:var(--a-muted);flex-wrap:wrap}.a-message-delivery>svg{width:14px;height:14px;flex-shrink:0}.a-message-delivery .a-link{font-size:inherit}.a-inline-checkbox{display:flex;gap:.5rem;align-items:center;font-size:.85rem}.a-inline-checkbox input{width:auto}
#amp-one .a-message-actions:has(.a-message-delivery){flex-wrap:wrap}.a-delivery-detail,.a-delivery-confirm{flex:0 0 100%;min-width:0;max-width:100%;box-sizing:border-box;overflow-wrap:break-word}.a-delivery-detail{margin:.35rem 0}.a-delivery-confirm{padding:.75rem;border:1px solid currentColor;border-radius:.75rem}
#amp-one .a-delivery-content{margin:0;min-width:0;max-width:100%}
#amp-one .a-delivery-confirm .a-dialog-actions{margin-top:.5rem}
#amp-one .a-delivery-content button{max-width:100%;overflow-wrap:normal}
#amp-one .a-delivery-detail,#amp-one .a-delivery-confirm{text-align:left;font-size:var(--a-text-sm,13px);color:var(--a-ink);line-height:1.5}
#amp-one .a-delivery-confirm{border-color:var(--a-line);background:var(--a-surface)}
#amp-one .a-message-actions .a-delivery-confirm .a-primary{background:var(--a-accent);color:var(--a-on-accent);padding:8px 12px;border-radius:var(--a-control-radius)}
#amp-one .a-message-actions .a-delivery-confirm .a-soft{background:var(--a-surface);color:var(--a-ink);padding:8px 12px;border:1px solid var(--a-line);border-radius:var(--a-control-radius)}
@media(pointer:coarse){#amp-one .a-delivery-content button,#amp-one .a-message-delivery button{min-height:44px}}

.a-delivery-warning{min-width:0;max-width:100%;flex:1 1 100%;color:var(--a-muted);font-size:.8rem;text-align:left}
.a-delivery-warning>summary{cursor:pointer;width:fit-content}.a-delivery-warning>summary[hidden]{display:none}
.a-delivery-warning[open]>summary{margin-bottom:.4rem}

#amp-one .a-markdown {
  max-width: 100%;
  color: var(--a-ink);
  font-family: var(--a-font-chat);
  font-size: var(--a-text-lg, 16px);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

#amp-one .a-markdown p {
  margin: 0 0 0.8em;
}

#amp-one .a-markdown .task-list-item {
  list-style: none;
}

#amp-one .a-markdown .task-list-item > input[type="checkbox"] {
  margin-inline-end: 0.5em;
}

#amp-one .a-markdown :where(h1, h2, h3, h4, h5, h6) {
  margin: 0.9em 0 0.45em;
  font-family: var(--a-font-heading);
  line-height: 1.3;
}

#amp-one .a-markdown a {
  color: var(--a-accent);
  text-decoration: underline;
  text-underline-offset: 0.15em;
}

#amp-one .a-markdown code {
  padding: 0.1em 0.3em;
  border-radius: 4px;
  color: inherit;
  background-color: var(--a-soft);
  font-family: var(--a-font-code);
  font-size: 0.9em;
  font-variant-ligatures: none;
}

#amp-one .a-markdown pre {
  max-width: 100%;
  overflow: auto;
  padding: 0.75rem 1rem;
  border: 1px solid var(--a-line);
  border-radius: var(--a-card-radius);
  color: var(--a-ink);
  background-color: var(--a-soft);
}

#amp-one .a-markdown pre code {
  display: block;
  padding: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
}

#amp-one .a-markdown blockquote {
  margin-inline: 0;
  padding: 0.2em 0 0.2em 1em;
  border-inline-start: 3px solid var(--a-accent);
  color: var(--a-muted);
}

#amp-one .a-markdown hr {
  border: 0;
  border-block-start: 1px solid var(--a-line);
}

#amp-one .a-table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

#amp-one .a-markdown table {
  border-collapse: collapse;
  max-width: 100%;
}

#amp-one .a-markdown :where(th, td) {
  padding: 0.55em 0.75em;
  border-block-end: 1px solid var(--a-line);
  text-align: start;
}

#amp-one :where(a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="tab"], [role="textbox"], [tabindex]:not([tabindex="-1"])):focus-visible {
  outline: 2px solid var(--a-accent);
  outline-offset: 2px;
}

#amp-one ::selection {
  color: var(--a-on-accent);
  background-color: var(--a-accent);
}

@media (forced-colors: active) {
  #amp-one {
    color-scheme: light dark;
    --a-bg: Canvas !important;
    --a-surface: Canvas !important;
    --a-soft: Canvas !important;
    --a-ink: CanvasText !important;
    --a-muted: CanvasText !important;
    --a-line: ButtonBorder !important;
    --a-accent: Highlight !important;
    --a-on-accent: HighlightText !important;
    --a-tint: Canvas !important;
    --a-green: CanvasText !important;
    --a-danger: CanvasText !important;
    --a-chrome-bg: Canvas !important;
    --a-chrome-ink: CanvasText !important;
    --a-background-art: none !important;
    --a-navigation-art: none !important;
    --a-conversation-art: none !important;
    --a-canvas-art: none !important;
    --a-panel-shadow: none !important;
    --a-surface-shadow: none !important;
    background-color: Canvas !important;
    background-image: none !important;
    scrollbar-color: auto;
  }

  #amp-one :where([data-part="header"], [data-part="navigation"], [data-part="conversation"], [data-part="canvas"], [data-part="composer-surface"], .a-nav-rail, .a-canvas-chrome) {
    background-image: none !important;
  }

  #amp-one :where(.a-primary, .a-send) {
    color: ButtonText !important;
    background-color: ButtonFace !important;
    border-color: ButtonBorder !important;
    box-shadow: none !important;
  }

  #amp-one button.a-soft {
    color: ButtonText !important;
    background-color: ButtonFace !important;
    border-color: ButtonBorder !important;
    box-shadow: none !important;
  }

  #amp-one .a-attention-badge {
    color: HighlightText !important;
    background-color: Highlight !important;
  }

  #amp-one :where(.a-nav-chat.is-selected, .a-nav-chat[aria-current="page"], .a-workspace-row.is-selected) {
    color: CanvasText !important;
    background-color: Canvas !important;
    box-shadow: inset 3px 0 Highlight !important;
    outline: 1px solid Highlight;
    outline-offset: -2px;
  }

  #amp-one :where(a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="tab"], [role="textbox"], [tabindex]:not([tabindex="-1"])):focus-visible {
    outline-color: Highlight !important;
  }

  #amp-one ::selection {
    color: HighlightText;
    background-color: Highlight;
  }
}
`,h=`#amp-one .a-sidebar-section{margin:12px 0 20px;min-width:0}
#amp-one .a-sidebar-section-heading{display:flex;align-items:center;gap:6px;margin-bottom:6px}
#amp-one .a-sidebar-section-heading>button:first-child{display:flex;align-items:center;gap:7px;min-width:0;flex:1;padding:8px 6px;text-align:left;background:transparent;border:0;color:var(--a-muted);font-size:var(--a-text-sm,13px);font-weight:600}
#amp-one .a-sidebar-section-heading>button:first-child:hover{color:var(--a-ink);background:var(--a-soft)}
#amp-one .a-sidebar-section-heading small{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:400}
#amp-one .a-sidebar-section-heading svg{width:15px;height:15px;flex-shrink:0}
#amp-one .a-sidebar-section-heading svg.is-open{transform:rotate(90deg)}
#amp-one .a-sidebar-section-heading>.a-icon{width:28px;height:28px;flex-shrink:0}
#amp-one .a-sidebar-section [hidden]{display:none!important}
/* Recent's fallback focus target needs the same visible keyboard cue as a row.
   Pointer recovery should not leave the browser's native dark rectangle. */
#amp-one .a-recent-shortcuts:focus{outline:none}
#amp-one .a-recent-shortcuts:focus-visible{outline:2px solid var(--a-accent);outline-offset:-2px}
#amp-one .a-sidebar-filters{margin:4px 0 10px}
#amp-one .a-sidebar-filters>summary{padding:6px;color:var(--a-muted);cursor:pointer;font-size:var(--a-text-sm,13px)}
#amp-one .a-sidebar-filters[open]>summary{margin-bottom:10px}
#amp-one .a-sidebar-workspace-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 6px 12px;color:var(--a-muted);font-size:var(--a-text-sm,13px)}
#amp-one .a-pinned-chats .a-reorder-grip,#amp-one .a-pinned-drag .a-reorder-grip{width:20px;height:32px;flex:0 0 20px;padding:2px;opacity:.55;margin-right:2px}
#amp-one .a-pinned-chats .a-reorder-grip svg,#amp-one .a-pinned-drag .a-reorder-grip svg{width:15px;height:18px}
#amp-one .a-pinned-chats .a-nav-chat:hover .a-reorder-grip,#amp-one .a-pinned-chats .a-reorder-grip:focus{opacity:1}
#amp-one .a-pinned-drag .a-nav-chat{margin:0;background:transparent}
#amp-one .a-pinned-drag .a-navigation-more{opacity:1;visibility:visible}
#amp-one .a-pinned-chats .a-workspace-row,#amp-one .a-pinned-drag .a-workspace-row{display:flex;align-items:center;min-width:0}
#amp-one .a-pinned-chats .a-workspace-select,#amp-one .a-pinned-drag .a-workspace-select{flex:1;min-width:0}

#amp-one .a-nav-chat-rename .a-nav-name-help{flex-basis:100%;color:var(--a-muted);white-space:normal}

/* Reclaim hidden action space without removing controls from keyboard order.
   Hover, focus and an open details panel restore the existing action widths.
   Detailed mode, narrow and touch navigation keep their existing targets. */
#amp-one[data-interface-detail="detailed"] .a-quiet-sidebar :is(.a-nav-chat,.a-workspace-row)>.a-navigation-more{opacity:1;visibility:visible}
@media(min-width:761px) and (hover:hover) and (pointer:fine){
 #amp-one:not([data-interface-detail="detailed"]) .a-quiet-sidebar :is(.a-nav-chat,.a-workspace-row):not(:hover,:focus-within,.is-peeking)>:is(.a-navigation-more,.a-workspace-new-chat){width:0;min-width:0;flex-basis:0;padding-inline:0;margin-inline:0;overflow:hidden;opacity:0}
}
.a-chat-shortcut{position:absolute;left:4px;top:50%;transform:translateY(-50%);z-index:2;display:grid;place-items:center;width:22px;height:24px;border:1px solid var(--a-line);border-radius:6px;background:var(--a-surface,#fff);color:var(--a-ink);box-shadow:0 1px 4px #0002;font:600 12px/1 system-ui;pointer-events:none}
#amp-one .a-chat-list-views{display:flex;align-items:start;flex-wrap:wrap;gap:16px;margin:12px 0;min-width:0}
#amp-one .a-chat-list-views>label{display:grid;gap:4px;min-width:0;flex:1 1 150px}
#amp-one .a-chat-list-views select{max-width:100%;min-width:0}
#amp-one .a-chat-age-filter{flex:0 0 auto;min-width:0;margin:6px 12px 6px;color:var(--a-muted);font-size:var(--a-text-xs,12px);line-height:1.4}
#amp-one .a-chat-age-filter>label{display:flex;justify-content:space-between;gap:8px;margin:0}
#amp-one .a-chat-age-filter>label strong{font-weight:500;font-variant-numeric:tabular-nums}
#amp-one .a-chat-age-filter input[type=range]{display:block;box-sizing:border-box;width:100%;min-width:0;height:24px;min-height:24px;padding:0;margin:0;border:0;background:transparent;accent-color:var(--a-muted)}
#amp-one .a-chat-age-track{position:relative}
#amp-one .a-chat-age-stops{position:absolute;inset:calc(50% + 4px) 8px auto;display:flex;justify-content:space-between;pointer-events:none}
#amp-one .a-chat-age-stops>span{width:1px;height:5px;background:var(--a-muted)}
@media(pointer:coarse){#amp-one .a-chat-age-filter input[type=range]{height:44px;min-height:44px}}
@media(forced-colors:active){#amp-one .a-chat-age-stops>span{background:CanvasText;forced-color-adjust:none}}
#amp-one .a-chat-group-heading{grid-column:1/-1;margin:16px 0 6px;font-size:var(--a-text-sm,13px);font-weight:600;color:var(--a-muted)}
`,g=`#amp-one .a-nav-slot .a-nav-chat:not(.a-nav-chat-rename),#amp-one .a-nav-slot .a-workspace-row{height:60px;min-height:60px;max-height:60px;gap:0}
#amp-one .a-nav-slot .a-nav-chat-select,#amp-one .a-nav-slot .a-workspace-row>.a-workspace-select{height:100%;gap:7px;min-width:0;padding:6px}
#amp-one .a-nav-slot button.a-nav-chat-select>span.a-nav-chat-label,#amp-one .a-nav-slot .a-workspace-label{min-width:0;flex:1;overflow:hidden;white-space:nowrap}
#amp-one .a-nav-slot .a-nav-chat-label>span,#amp-one .a-nav-slot .a-workspace-label>span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;line-height:21px}
#amp-one .a-nav-slot .a-nav-chat-label>.a-nav-chat-workspace,#amp-one .a-nav-slot .a-workspace-label>small{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;overflow-wrap:normal;margin-top:1px;line-height:18px;font-size:var(--a-text-xs,12px);color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-more{width:26px;min-width:26px;height:32px;opacity:0;flex-shrink:0;margin-right:2px}
#amp-one .a-nav-slot .a-navigation-more svg{width:15px;height:15px}
#amp-one .a-nav-slot .a-workspace-new-chat{width:28px;min-width:28px;height:32px;flex:0 0 28px;padding:4px;opacity:0}
#amp-one .a-nav-slot .a-workspace-new-chat svg{width:16px;height:16px}
#amp-one .a-nav-slot .a-workspace-row:hover>.a-workspace-new-chat,#amp-one .a-nav-slot .a-workspace-row:focus-within>.a-workspace-new-chat,#amp-one .a-nav-slot .a-workspace-row.is-peeking>.a-workspace-new-chat{opacity:1}
#amp-one .a-nav-slot .a-nav-chat:hover .a-navigation-more,#amp-one .a-nav-slot .a-workspace-row:hover .a-navigation-more,#amp-one .a-nav-slot .is-peeking>.a-navigation-more,#amp-one .a-nav-slot .a-nav-chat:focus-within .a-navigation-more,#amp-one .a-nav-slot .a-workspace-row:focus-within .a-navigation-more{opacity:1}
#amp-one .a-nav-slot .a-nav-chat.is-peeking,#amp-one .a-nav-slot .a-workspace-row.is-peeking{background:var(--a-soft)}
#amp-one .a-nav-slot .a-nav-chat.is-selected,#amp-one .a-nav-slot .a-workspace-row.is-selected{background:var(--a-tint)}
#amp-one .a-navigation-status{display:inline-flex;align-items:center;justify-content:center;flex:0 0 17px;width:17px;height:19px;color:var(--a-muted)}
#amp-one .a-navigation-status svg{width:15px;height:15px}
#amp-one .a-navigation-status[data-kind="working"],#amp-one .a-navigation-status[data-kind="unread"]{color:var(--a-accent)}
#amp-one .a-navigation-status[data-kind="attention"]{color:var(--a-danger)}
#amp-one .a-navigation-unread{width:6px;height:6px;border-radius:50%;background:currentColor}
#amp-one .a-nav-slot .a-navigation-age{flex:0 0 26px;width:26px;text-align:right;font-size:var(--a-text-xs,12px);color:var(--a-muted);font-variant-numeric:tabular-nums;white-space:nowrap}
#amp-one .a-navigation-flyout{position:fixed;z-index:60;width:min(370px,calc(100vw - 20px));max-height:calc(100dvh - 20px);overflow:auto;background:var(--a-surface);color:var(--a-ink);border:1px solid var(--a-line);box-shadow:0 12px 40px #0002;border-radius:13px;padding:18px;text-align:left;white-space:normal;font-size:var(--a-text-base,14px)}
#amp-one .a-navigation-detail-heading{display:flex;align-items:center;gap:6px;font-size:var(--a-text-xs,12px);color:var(--a-muted);text-transform:uppercase;letter-spacing:.06em}
#amp-one .a-navigation-detail-heading svg{width:14px;height:14px}
#amp-one .a-navigation-flyout h3{font-size:var(--a-text-lg,16px);font-weight:600;line-height:1.5;margin:9px 0 14px;overflow-wrap:anywhere}
#amp-one .a-navigation-flyout dl{display:grid;grid-template-columns:86px minmax(0,1fr);gap:9px;margin:14px 0;font-size:var(--a-text-sm,13px)}
#amp-one .a-navigation-flyout dt{color:var(--a-muted)}
#amp-one .a-navigation-flyout dd{margin:0;min-width:0;overflow-wrap:anywhere}
#amp-one .a-navigation-detail-status{display:flex;gap:8px;align-items:center;padding:10px;background:var(--a-soft);border-radius:8px;font-size:var(--a-text-base,14px)}
#amp-one .a-navigation-copy{border-top:1px solid var(--a-line);padding-top:9px;margin-top:12px}
#amp-one .a-navigation-copy>div{display:flex;align-items:center;justify-content:space-between;color:var(--a-muted);font-size:var(--a-text-xs,12px);text-transform:uppercase;letter-spacing:.04em}
#amp-one .a-navigation-copy .a-icon{width:26px;height:26px}
#amp-one .a-navigation-copy svg{width:13px;height:13px}
#amp-one .a-navigation-copy code{display:block;font:var(--a-text-sm,13px)/1.7 var(--a-font-code);overflow-wrap:anywhere;white-space:normal;user-select:text;background:transparent;padding:0}
#amp-one .a-navigation-copy small{display:block;font-size:var(--a-text-xs,12px);color:var(--a-muted);margin-top:4px}
#amp-one .a-navigation-actions{display:flex;flex-wrap:wrap;gap:4px;padding-top:12px;margin-top:14px;border-top:1px solid var(--a-line)}
#amp-one .a-navigation-actions>button{padding:7px 6px;font-size:var(--a-text-sm,13px);gap:5px;border-radius:6px}
#amp-one .a-navigation-actions>button:hover{background:var(--a-soft)}
#amp-one .a-navigation-actions svg{width:13px;height:13px}
#amp-one .a-navigation-flyout form{margin-top:12px}
#amp-one .a-navigation-flyout input{min-width:0;width:100%;border:1px solid var(--a-line);border-radius:7px;padding:8px;background:var(--a-surface);color:var(--a-ink)}
#amp-one .a-navigation-flyout .a-nav-chat-rename{display:flex;flex-wrap:wrap;gap:6px;padding:3px;max-height:none;height:auto}
#amp-one .a-navigation-flyout .a-nav-chat-rename input{flex-basis:100%}
#amp-one .a-nav-slot .a-navigation-back{color:var(--a-muted);gap:5px;font-size:var(--a-text-sm,13px);padding:6px 3px}
#amp-one .a-nav-slot .a-navigation-back svg{width:13px;height:13px}
#amp-one .a-nav-slot .a-navigation-workspace{display:flex;gap:7px;align-items:center;padding:7px 4px}
#amp-one .a-nav-slot .a-navigation-workspace>svg{width:18px;height:18px;color:var(--a-accent);flex-shrink:0}
#amp-one .a-nav-slot .a-navigation-workspace>div{min-width:0;flex:1}
#amp-one .a-nav-slot .a-navigation-workspace>.a-navigation-more{opacity:1}
#amp-one .a-nav-slot .a-navigation-workspace strong,#amp-one .a-nav-slot .a-navigation-workspace small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#amp-one .a-nav-slot .a-navigation-workspace strong{font-size:var(--a-text-base,14px)}
#amp-one .a-nav-slot .a-navigation-workspace small{font-size:var(--a-text-xs,12px);color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-filters,#amp-one .a-nav-slot .a-workspace-modes{display:flex;align-items:center;gap:5px;margin:5px 0}
#amp-one .a-nav-slot .a-navigation-filters button,#amp-one .a-nav-slot .a-workspace-modes button{padding:5px 6px;gap:4px;font-size:var(--a-text-xs,12px);border-radius:6px;color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-filters button[aria-pressed="true"],#amp-one .a-nav-slot .a-workspace-modes button[aria-pressed="true"]{background:var(--a-tint);color:var(--a-accent)}
#amp-one .a-nav-slot .a-navigation-filters svg{width:12px;height:12px}
#amp-one .a-nav-slot .a-workspace-modes .a-icon{margin-left:auto}
#amp-one .a-nav-slot .a-workspace-explorer{border-bottom:0;padding-bottom:0;margin-bottom:0}
#amp-one .a-nav-slot .a-workspace-breadcrumbs{flex-wrap:nowrap;overflow:visible}
#amp-one .a-nav-slot .a-workspace-breadcrumbs>button{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;overflow-wrap:normal}
#amp-one .a-nav-slot .a-workspace-ancestors[data-open="true"]{flex:0 0 auto}
#amp-one .a-nav-slot .a-workspace-ancestors>div{position:absolute;top:100%;left:0;z-index:4;width:220px;max-width:calc(100vw - 70px);max-height:260px;overflow:auto;box-shadow:0 8px 24px #0002}
#amp-one[data-density="compact"] .a-nav-slot .a-nav-chat:not(.a-nav-chat-rename),#amp-one[data-density="compact"] .a-nav-slot .a-workspace-row{height:52px;min-height:52px;max-height:52px}
@media(hover:none),(pointer:coarse){#amp-one .a-nav-slot .a-navigation-more{opacity:1;width:40px;min-width:40px;height:44px}#amp-one .a-navigation-flyout button{min-height:44px;min-width:36px}#amp-one .a-nav-slot .a-navigation-filters button,#amp-one .a-nav-slot .a-workspace-modes button,#amp-one .a-nav-slot .a-navigation-back{min-height:44px}#amp-one .a-navigation-flyout input{font-size:16px}}
@media(hover:none),(pointer:coarse){#amp-one .a-nav-slot .a-workspace-new-chat{opacity:1;width:44px;min-width:44px;height:44px;flex-basis:44px}}

#amp-one .a-navigation-status[data-attention="blocked"],#amp-one .a-navigation-status[data-attention="decision"]{color:var(--a-warning,#b78020)}
#amp-one .a-workspace-identity{position:relative;display:inline-flex;align-items:center;justify-content:center;flex:0 0 21px;width:21px;height:23px;color:var(--a-muted)}
#amp-one .a-workspace-identity>svg{width:17px;height:17px}
#amp-one .a-workspace-identity>.a-navigation-status{position:absolute;right:-3px;bottom:-2px;width:12px;height:12px;border-radius:50%;background:var(--a-surface)}
#amp-one .a-workspace-identity>.a-navigation-status svg{width:11px;height:11px}
`,x=`/* Preserve source soft line breaks in incoming user/peer Markdown without
   rewriting text, interpreting literal backslash-n, or changing copy bytes. */
#amp-one .a-markdown.a-markdown-user-content p,
#amp-one .a-markdown.a-markdown-user-content li{white-space:pre-wrap}
`,v=i.renderToStaticMarkup(a.createElement(a.Fragment,null,a.createElement("header",{"data-part":"header"},a.createElement("strong",{className:"a-brand"},"Amplifier"),a.createElement("span",{className:"a-caption"},"A place for ideas")),a.createElement("div",{className:"a-thumbnail-workspace"},a.createElement("aside",{"data-part":"navigation"},a.createElement("div",{className:"a-nav-rail"},a.createElement("div",{role:"group","aria-label":"Sidebar view"},a.createElement("button",{"aria-pressed":"true"},"Browse"),a.createElement("button",{"aria-pressed":"false"},"Status")),a.createElement("p",{className:"a-nav-eyebrow"},"Pinned"),a.createElement("div",{className:"a-nav-chat is-selected"},a.createElement("button",{className:"a-nav-chat-select"},a.createElement("span",{className:"a-nav-chat-title"},"A new idea"))),a.createElement("div",{className:"a-nav-chat"},a.createElement("button",{className:"a-nav-chat-select"},a.createElement("span",{className:"a-nav-chat-title"},"Weekend plans"))))),a.createElement("main",{"data-part":"conversation"},a.createElement("div",{className:"a-messages","data-part":"messages"},a.createElement("article",{className:"a-message a-user"},a.createElement("div",{className:"a-message-body"},a.createElement(n,{text:"Make room for a new idea.",userContent:!0}))),a.createElement("article",{className:"a-message a-assistant"},a.createElement("div",{className:"a-message-body"},a.createElement(n,{text:'## Start with something small\n\nA clear space for **your next idea**, with a useful [reference](#sample).\n\n```js\nconst next = "Make it real";\n```'})))),a.createElement("form",{"data-part":"composer"},a.createElement("div",{"data-part":"composer-surface"},a.createElement("div",{className:"a-markdown-composer",role:"textbox"},"Ask Amplifier…"),a.createElement("div",{className:"a-thumbnail-composer-actions"},a.createElement("span",null,"＋"),a.createElement("button",{className:"a-send","aria-label":"Send message"},"↑")))))))),w=r(c,m)+`
`+[p,s,l,d,h,g,x].join(`
`),b=(e,t)=>o({css:e,scheme:t,hostCss:w,markup:v});export{b as renderAppearanceThumbnail};
