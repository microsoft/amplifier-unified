# Historical turn duration ranges

The live status can show an **Estimated turn** range from recent completed human
turns in that same chat. No prediction service, prompt analysis call, transcript
scan or cross-chat data collection is added. This estimates the foreground turn,
not completion of a goal, all delegates, or external work.

At a new ordinary human input, the host records metadata only: exact selected
connection/model/effort, workspace/bundle, a logarithmic request-length bucket
and attachment types. It examines at most 80 retained host turns and uses at most
20 exact-shape matches. At least five completed samples are required. Failed,
interrupted, unfinished and invalid timings are excluded; durations outside
1 second–2 hours and widely varying distributions produce no range. The 20th–80th
sample percentiles receive a 25% margin and are rounded outward to five seconds.
This is a descriptive heuristic, not a calibrated probability interval.

Older turns without this metadata are not guessed or backfilled. A new chat or
changed connection can therefore show only elapsed time until enough examples
exist. Request length is a limited proxy: tool choice, cache warmth, provider
load and task difficulty can change duration. The tooltip exposes the basis and
sample count. Once the range is exceeded, the display says the turn is taking
longer than recent similar turns; it never stalls at a zero-second countdown.
Pending decisions, stopped/idle work and independently active delegates suppress
this hint. No additional per-second work is added to the transcript parent.

Tests cover sparse/variable history, account/model/configuration mismatches,
failed outcomes, duplicate lifecycle events, machine/answer/steer inputs,
overruns and stale UI states. Browser evidence uses synthetic host history;
there is no claim of measured real-world forecast accuracy.
