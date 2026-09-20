# Interaction feedback

A click must change the interface immediately. Shared actions use the dispatch feedback bridge: keep `data-action` on the initiating control aligned with the action it submits. Text buttons show a moving underline, icon buttons show a spinner, and controls expose `aria-busy`. Duplicate clicks on a pending request are ignored. Immediate view edits and navigation stay interactive while their state is saved.

HTTP acceptance is not completion for background operations. Components must keep a lifecycle indicator driven by the exact operation and target. Use `aria-busy` on the initiating button and `ActivityRegion` (or `useRegionActivity` when an existing DOM node must be kept) on the affected content. Give concurrent lists, credential checks, catalogs, inspections and results separate regions. Do not mark an entire page busy because one control is loading.

Retain current content and drafts during refresh. `useRefreshValue(sourceKey, value, refreshing)` retains same-source results while loading; a successful empty result clears them. Change the key when the provider, source, tool or conversation changes. Existing result/error notices remain available; refresh decoration adds no extra reading. Reduced-motion users get a static indicator. The feedback bridge never changes command order or execution semantics.

The shared dialog backdrop dismisses only a click that starts and ends outside the dialog. Nested model/bundle/location surfaces use `useOutsideDismiss`: outside clicks dismiss them and Escape dismisses the topmost surface first. Dismissal changes visibility, never submits or cancels an accepted operation. Keep ordinary drafts in the existing view state; secrets stay local and must not be sent as view patches.

For asynchronous work performed before dispatch (file reading, session preparation or detail fetching), set local pending state before the first await. Do not rely on the gesture bridge after an await. Clear it in `finally`, keep failure recovery visible, and do not equate a request receipt with a completed operation.

Validation: `npm test`, `npm run test:activity-feedback-browser`, and the affected feature's browser suite. Exercise delayed receipts, delayed lifecycle completion, failure/retry, concurrent regions, preserved selections/drafts, keyboard/outside dismissal and reduced motion. Browser tests use isolated fixtures; they never manipulate a user's active app.
