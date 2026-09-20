# Work capability access

Work uses the existing patch-editing module from the filesystem bundle. Unified
tracks Work and its dependencies on `main`; an explicit user source registration remains
authoritative. Existing conversations pick up changed composition when remounted,
without replaying prior tools. A saved snapshot keeps its original module roster.

The file-access controls stored in `modules.tools`, `config.tools`, or
`overrides.tool-filesystem.config` apply to
both filesystem writes and patch editing, including nested agents and portable
snapshots. An explicit patch allowlist is intersected with the shared allowlist;
deny lists are combined. Empty allowlists deny writes. This is a file-tool policy,
not an operating-system sandbox for arbitrary shell processes. A denied write is
not an instruction to retry through another tool.

## Existing capabilities to discover

| Need | Existing access |
| --- | --- |
| Find/read other conversations | `app_control` operation `history`, actions `list`, `search`, `read`; bounded pages, workspace scope by default |
| Manage conversations/workspaces | `app_control` operation `list_actions` with `session.` or `workspace.` prefix, followed by `dispatch` |
| Diagnose/recover a failed conversation | `session.inspect`, `session.recover`; recovery creates an idle copy and never replays tools |
| Show and revisit artifacts | `canvas.show`, `canvas.select`, canvas application/view actions |
| Discover connected tools/resources | `smartTools.*` actions and the mounted tool catalog; availability does not imply a connected account |
| Discover/use skills | Mounted `load_skill` from the configured skills behavior |
| Delegate and steer workers | Mounted `delegate` and `live_worker`; async opt-in and pending receipts remain distinct from completion |
| Read original admitted history | Work's `read_transcript`, separate from cross-conversation discovery |
| Ask durable choices while independent work continues | Shared `question.*` actions; [question and delivery contract](DURABLE-QUESTIONS.md) |
| Voice lifecycle and scoped file access | `call.*` and `permissions.*` shared actions |

Host services are supplied by Unified, not by the portable Work bundle. The
agent's app-control description and runtime guidance explain these discovery
paths; separate aliases need not duplicate the implementations.

This does not establish parity with another product. Durable scheduling, a
general managed process/stdin service, worktree handoff, and bounded cross-task
wait subscriptions still require separate integration work. The existing web
module is not added here: its search-error path can return mock results, which
must be corrected before making it a default research capability.

Validation includes Foundation composition, shared-policy regression tests, and
both real patch engines operating in a disposable directory with denial and
symlink-escape checks. No model request or live-session write is part of that
evidence.

## Ecosystem updates

Work, its tools, live loop, context modules, routing, and worker dependencies follow
`main`. The update check compares both cached source checkouts and the worker's
installed Git revisions with their branches. Installation refreshes an isolated
cache and worker lock, validates the configured bundles without a model turn,
and activates only when work and calls are idle. Resuming mounts the new code.
Automatic checking and automatic installation are separate settings.

Lockfiles and update receipts record what was installed and support rollback;
they do not prevent the next ecosystem update from advancing the branch. The
server's own Python libraries follow their dependency declarations when the app
is installed or updated. They are distinct from the independently updateable
conversation worker libraries. User-supplied fixed sources and local edits remain
explicit choices; the app does not rewrite arbitrary user configuration.
