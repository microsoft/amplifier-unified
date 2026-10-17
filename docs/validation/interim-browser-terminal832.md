# Interim-reply scrollback test boundary

The frozen browser census failed the final long-scrollback step in
`interim-replies-browser.mjs`: the expected folded group remained absent. Earlier
preference, export, copy, reveal, reload, cross-client, active/error, focused-control,
retained-media and narrow-layout assertions had passed.

A diagnostic held actual WebSocket state frames after the fixture's completion
HTTP response. With the original assertion ordering, the test scrolled to the
bottom before the completed generation reached the browser. Recorded geometry
then changed from scrollTop 3991 / scrollHeight 4773 / clientHeight 782 to
3991 / 4941 / 819. The remaining 131 pixels exceed the app's 80-pixel reading
threshold, so leaving interim prose unfolded was correct. The original final
group-count assertion failed under this controlled ordering.

The routine fixture now waits for the unique long generation's actual message
node, then its exact terminal message node and completed response binding. Only
then does it assert that scrollback still defers folding and click the ordinary
**Jump to latest messages** control. The same held-frame diagnostic passes the
complete browser journey with this boundary. No app folding code, timeout or
assertion was relaxed, and no model input was sent. The existing extended
`interim-replies` scenario remains its owner; the diagnostic adapter is evidence,
not a production or standard-run dependency.

Logs and exact generation/geometry traces are retained under
`output/afk-approved-20261017/work-receipt832/`. This reproduces a sufficient
fixture timing cause on unchanged built assets; the original census did not retain
its browser frame ordering, so it does not prove that run's exact interleaving.
