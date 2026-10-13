# Full-resolution GIF Canvas delivery

Issue #238 uses the retained-media path: save an exact GIF with
`outputs.attachMedia` at an existing assistant message, then use its **Open in
Canvas** control or `canvas.openGif` with the returned immutable media identity.
The original GIF stays downloadable after the source file is removed. Canvas
stores a small media reference, not an inline base64 image snapshot.

GIF admission permits 64 MiB encoded, 4096 pixels per side, 4 million pixels per
frame, 500 frames, and a 512 MiB total composited-pixel estimate. MP4 keeps its
32 MiB limit. Conversation transfer retains its independent 32 MiB total limit;
oversized exports fail explicitly, without omitting media or changing the source.
Download the original GIF directly when that transfer budget is exceeded. Ordinary image snapshots still have their 5 MB limit; their GIF
error now directs agents to the retained path. A source path or receipt alone
is not evidence that playback succeeded.

The existing player starts paused, supports play/pause and reduced motion,
pauses when hidden or offscreen, cancels reads on unmount and closes frames and
decoders. It retains at most current + next frames per player and admits two
decode operations per browser realm. Host media workers remain limited to two,
and the byte cache remains capped at 64 MiB. Source reads, detached bodies and
individual downloads are bounded; they are not unbounded streaming ingestion.
The pixel estimate and application frame counts do **not** bound native codec
caches or total browser process memory. Many simultaneous players still cost
resources; this is not a claim of constant memory across an arbitrary gallery.

## Independent evidence, October 12, 2026

Inside the project's owned Linux ARM64 DTU, the reproducible
`tests/fixtures/full_resolution_gif.py` generated 60,290,769 bytes at 1920×802,
68 frames and 4,540 ms. SHA-256:
`93c11027eb7fe64a7a8d3fd21de09096d8cd302cb3a050f026d337fd9d2943f0`.
Every frame has full-size pixel data, with no size padding. Pillow decoded all
68 frames independently. This is a synthetic equivalent, not the reporter's
unavailable original file.

The real Chromium `gif-canvas` browser phase passed with
`INLINE_MEDIA_FULL_RESOLUTION=1` and that file supplied as
`INLINE_MEDIA_LARGE_GIF`. It verified actual 1920×802 moving pixels, pause
freeze, initial paused state, reduced motion, original-download hash, source
removal, reload, close/reopen, one durable version and unchanged peer drafts
and Canvas. No model calls occurred. This was an explicit focused phase, not
a claim of the full media scenario or physical-device qualification.

All 241 output-media backend tests passed, including existing smaller GIF,
MP4, retention, attribution, authorization, immutable identity and delivery
checks, plus the larger resolution/frame admission and separate MP4 cap.
