# Public offline recovery

When a root or login navigation cannot reach the server, the service worker
serves its public offline document. That document now checks for Amplifier
automatically instead of requiring the user to reload it.

Recovery uses only GET requests: the public health endpoint must identify
Amplifier, and the destination must also return HTML before navigation resumes.
This prevents a reachable health endpoint with an unavailable app route from
causing a rapid navigation loop. A five-second timeout covers both requests.
Retries start at two seconds and back off to thirty seconds. Only one probe can
run at a time. Hidden or departed pages suspend probing; foreground, pageshow,
online and manual retry restart it. Existing authenticated application pages do
not run this recovery loop or automatically reload.

No private application data is added to the service-worker cache. Recovery
neither submits messages nor replays actions. It navigates through the normal
authentication boundary. Draft preservation across navigation applies to drafts
already saved before the navigation; this is not a claim about unsaved offline
edits or a separate active-chat event-stream recovery path.

Validation in the owned Linux DTU:

- Five Node tests cover capped retries, a hung probe, concurrent wake events,
  visibility/lifecycle changes, false health responses, an unavailable entry
  route, and exclusion of active chat pages.
- The production PWA browser check covers service-worker registration and public
  cache contents, installability, mobile layout, automatic recovery without a
  click, a preserved saved draft, no message/edit/create replay, and signed-out
  recovery with private state still returning 401.
- The public readiness workflow runs both the unit and PWA browser checks.

Physical iOS Home Screen and macOS sleep/wake acceptance remain required. These
checks do not identify the original phone's VPN, DNS or TLS interruption, nor
prove that every device network interruption behaves like Chromium offline
emulation.
