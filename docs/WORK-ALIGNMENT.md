# Work capabilities in Unified

This integration adds persistent work controls to the shared conversation interface. The user and the agent use the same actions; opening a view or changing a draft does not send a message. Existing conversation, voice and output records remain the originals.

These changes are under integration review. They are not part of the deployed 0.19.4 release. The [delivery issue](https://github.com/bkrabach/amplifier-unified/issues/128) tracks review, acceptance and release separately.

| Capability | What it supports | Guide |
|---|---|---|
| Managed commands | Start a command, follow output, wait, provide permitted input, and request cancellation with a durable result. | [Operations](OPERATIONS.md) |
| Programmatic tools | Run a bounded JavaScript program over approved tools, keeping nested receipts and returning selected results. | [Work composition](work-profile.md) |
| Questions | Save an optional or required question, answer later, and retain its delivery state after reconnect. | [Durable questions](DURABLE-QUESTIONS.md) |
| Task continuity | Save an objective, corrections, constraints, dependencies and completion evidence across context changes. | [Task continuity](TASK-CONTINUITY.md) |
| Scheduled work | Preview and activate reminders, continuation or monitors with pause, cancellation and missed-run policies. | [Schedules](SCHEDULES.md) |
| Coordination | Read and wait for other tasks and workers by cursor, then send an explicit follow-up without changing the selected conversation. | [Task coordination](TASK-COORDINATION.md) |
| Recall and memory | Search saved work, inspect exact sources, and explicitly correct or delete scoped memory. | [Recall](RECALL.md) |
| Web research | Search and fetch attributable sources, with explicit failures when the network or search service is unavailable. | [Work composition](work-profile.md) |
| Worktrees | Create or attach an isolated checkout and move execution with a receipt while keeping saved history in its original home. | [Worktrees](WORKTREES.md) |
| Conversation library | Archive and restore conversations, organize collections, and deliberately publish a fixed share snapshot. | [Conversation library](CONVERSATION-LIBRARY.md) |
| Computation | Keep Python or Node variables across cells, inspect results, and reset a computation runtime. | [Computation](COMPUTATION.md), [runtime discovery](ARTIFACT-RUNTIMES.md) |
| Outputs and review | Attach exact saved outputs, inspect lineage, edit writing copies, preview PNGs, and record comments on a frozen local Git diff. | [Outputs](OUTPUTS.md) |
| Connectors | Connect local or remote MCP services, complete supported authorization, and discover relevant tool schemas on demand. | [Connectors](CONNECTORS.md) |
| Visual context in voice | Explicitly grant a browser-selected screen or window, capture it for the active voice conversation, and revoke access. | [Voice visual context](VOICE-VISUAL.md) |
| Native provider support | Opt into supported live direction and opaque provider compaction while retaining ordinary request-boundary behavior elsewhere. | [Native provider](NATIVE-PROVIDER.md) |
| Usage and budgets | Inspect attributed root/worker usage and set limits on future supported model calls. | [Capacity](CAPACITY.md) |

## Availability

Managed commands, programmatic tools and web research depend on the corresponding Work modules. Source declarations remain at `@main`; validation records the exact revision that was resolved. The enabled Work composition must follow the upstream module changes, not precede them.

Persistent computation requires a trusted execution configuration permitting interpreter input. The default strict Bash policy rejects it. Explicitly enabling unrestricted managed input is a configuration decision; creating a computation runtime does not silently relax that policy. Cell execution still follows the configured approval hooks. Runtime discovery reports installed programs and libraries; listing a capability does not install it.

Native provider support is optional and model-specific. The existing configured Terra model was used for ordinary live acceptance; native Astra steering remains protocol-fixture evidence until separately exercised with a supported account/model. Connector consent was exercised with the official SDK and local protocol servers; those checks do not establish access to a user's third-party account. Browser voice capture tests do not establish physical microphone quality or a native foreground-app bridge.

Schedules run while a capable host is available and follow their saved missed-run policy. Share snapshots are served by the publishing host. Budget enforcement covers recorded supported invocation boundaries, not all account spending or already-started work. Each guide describes the exact boundaries and recovery behavior.

## Validation and recovery

The aggregate validation combines real Git and runtime processes, production browser assets, protocol fixtures, and bounded calls to the configured model in new private test histories. Paid acceptance runners require `--allow-live`; credentials and raw provider transcripts stay in their private output directory. A passing fixture, a loaded tool, or a model's claim alone does not establish a completed external effect.

A command, handoff, schedule or native request interrupted without a terminal receipt stays unknown. Inspect and reconcile its actual result before retrying. Restart never automatically replays uncertain effects or restores computation variables by rerunning old cells.
