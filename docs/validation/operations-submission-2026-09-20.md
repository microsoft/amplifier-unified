# Shared managed-process admission and terminal completion

The original P01 audit found missing shared submission/stdin, dependent-question
admission and optional terminal execution. These now use the existing Bash tool,
normal pre/post/permission hooks and the existing operation journal.

- Shared UI/agent `operations.submit`, `operations.write`, and
  `operations.request` use durable request identities. Duplicate requests return
  the saved receipt, changed arguments/actors are rejected, and restart or lost
  transport leaves an explicit unknown outcome without replay.
- Submission can start a runtime explicitly. Input/cancel cannot reactivate a
  missing process. Input binds the original runtime, opaque process ID and mounted
  owner; policy hooks cannot redirect those arguments. Owner checks run after
  approval. Required question IDs are checked before admission and again inside
  Bash immediately before spawning, with exact positive host acknowledgment.
- Optional POSIX terminal execution uses an actual controlling terminal, merged
  stdout/stderr, existing stdin policy, observed process-group cancellation and
  bounded output. Terminal EOF is canonical EOF; raw-mode EOF, terminal resizing,
  native Windows and reattaching after host death remain explicitly unavailable.
- Receipts contain bounded admission metadata. Command/input bodies are hashed,
  not copied; output remains in its existing journal.

Validation on this source:

- 60 affected host/operation/Stop/handoff/control/terminal/question checks passed,
  two optional checks skipped. The final focused operation suite passed21 tests.
- 215 frontend tests passed; production assets built successfully.
- The actual production browser plus real Bash subprocess fixture passed shared
  command submission, stdin, observed exit, exactly one file effect, saved output,
  reload, selected task/draft preservation and mobile bounds. The worker transport
  and hook implementation in this fixture are synthetic; no live-provider call or
  production deployment is claimed. Screenshots were inspected.
- Upstream Bash source9c88fca passed200 tests with12 platform skips; terminal tests
  verify real isatty/stdin/EOF, cancellation, host-policy rejection and dependency
  gating. A follow-up tightens exact positive question acknowledgment and is
  validated separately in its source commit.

The Bash upstream PR and Work composition must reach their declared main sources
before enabling this in a released composition. All work here is isolated from
production histories and saved user settings.
