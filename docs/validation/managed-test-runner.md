# Managed runner qualification

This change extends workspace test environments with supported setup, prepared
dependencies and durable queued execution. It does not deploy a production host
or establish native desktop-platform support.

## Observed acceptance

- Linux ARM64, Docker: setup through the shared action installed the qualified
  deterministic tool, verified the exact base image with an offline Python
  probe, removed the probe and saved the private configuration.
- Two distinct root chats submitted jobs through the shared queue. Closing and
  reopening the host while one command ran retained its single execution;
  cancelling the other stopped only that job. Both exported evidence and removed
  their environments. This was repeated on the final release-integrated source.
- A locked Python project prepared and ran pytest offline. Root project package
  metadata was available. Cancelling a live dependency builder stopped it and
  removed its owned resources while retaining evidence.
- The browser profile prepared locked Python/Node dependencies, launched
  Chromium headlessly and ran the unchanged 100-trial Linux X11 coexistence
  workload. It completed in 584.8 seconds: 95 detections, zero false positives,
  release on all detected halts, all existing assertions passed. The queue
  retained source and complete artifact output, then removed the guest and its
  prepared image. No assertion was relaxed and the benchmark was not retried.
- Linux ARM64, Incus: setup used a private project, bridge and default-profile
  root disk on an existing pool. Its offline probe passed and was removed.
  Bare queued checks and a prepared Python/pytest queued check passed.
- Browser controls: workspace Details entry, explicit setup, profile selection,
  keyboard submission, queued cancellation, reload, bounded output paging,
  narrow layout and unknown-result retention passed. No JavaScript errors were
  observed. Interactive computer use also exercised profile selection, queuing
  and reading a completed result. No chat-header control was added.

## Source qualification and limits

The release-integrated source passed 154 focused lifecycle tests, 25 setup
tests and 18 frontend component tests. Additional transport/capacity and Incus
launcher suites passed (72 and 45 tests). The prepared-profile suite passed
29 tests. Eight supervisor process tests were separately run in a root-owned
Linux test environment; ordinary unprivileged pytest correctly skips them.
The frontend was rebuilt with the server changes.

The long benchmark ran on the frozen integration candidate before the final
observation-cost and Incus setup fixes. Its guest dependency recipe, profile
definitions and transport match the final source. The final observer changes
were qualified by focused tests and the repeated actual Docker restart/cancel
run; the complete long benchmark was not repeated on that later tree.

UI fixtures use a controlled backend. Actual Docker and Incus acceptance are
separate evidence. Completion notification guards have source-test coverage;
no real provider completion/resumption was invoked. Headless Chromium inside a
guest does not prove an interactive preview route, physical input behavior or
native macOS/Windows/WSL setup. Memory admission observes free headroom and caps
builders; it does not reserve memory against unrelated host activity.

Read-only discovery needs no setup approval. Installing/configuring the runner
is a distinct host effect: a direct user action or the narrow agent
`test_host_setup` grant authorizes it. Cleanup must retain ownership whenever
the guest, builder, prepared image or evidence export has an uncertain outcome.
