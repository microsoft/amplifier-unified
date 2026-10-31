# Native Windows installation

Status: implementation design; **not a native Windows support claim**.
Source assessment: Unified `7b9358bf6443cc003edc1f3c380da8d01a8c2f5e`,
October 31, 2026. Recheck source and resolved upstream revisions before implementation.
The [current setup guide](../../README.md#set-up-unified-on-your-computer)
continues to cover Linux, macOS and Windows through WSL.

## Intent and boundary

Requested direction:

> Let's design and document the more turnkey approach for vanilla Windows
> installation, fixing whatever is missing to make it work with Windows along
> with the currently supported environments.

Interpretation: a normal Windows user starts in PowerShell, installs Unified
without WSL, and reaches a useful authenticated conversation without selecting
Python, installing a compiler, or learning service management. Preserve existing
Linux, WSL and macOS behavior. This is a native **backend** deployment of the
existing web app, not the planned Windows desktop client.

The triggering report showed `uv tool install` selecting CPython 3.15 and
compiling PyYAML 6.0.3, then failing for lack of Microsoft C++ Build Tools.
That is user-supplied evidence, not a Windows reproduction from this assessment.
Selecting 3.13 avoids that interpreter-selection problem; it does not fix the
runtime blockers below. Installing Visual Studio is not the proposed solution.

## Recommended experience

These are proposed defaults, not ratified changes to authentication or lifetime:

1. Run one PowerShell bootstrap as the signed-in user. It explains what it will
   install and where; no administrator terminal or machine execution-policy change.
2. Reuse valid helpers or obtain user-local uv and Git, then a managed ordinary
   CPython 3.13. Do not invoke the `python` Store alias or change system Python.
3. Verify a stable release and its platform support record. Install the host and
   prepare the default worker graph without native compilation.
4. Register and start a least-privilege, per-user logon task. Verify the expected
   authenticated host identity before opening a browser.
5. Authorize that browser from the owner's launcher, then connect an AI account
   in the app. Package installation, runtime readiness and first response remain
   separately reported states.

Initial qualification target: Windows 11 x64, built-in Windows PowerShell 5.1
and PowerShell 7, Edge and Chrome. Windows ARM64, Windows 10 and other Python
versions require their own results; none are silently excluded from future work
or inferred supported from the universal Unified wheel.

### Bootstrap contract

Add `scripts/install.ps1` only with its executable Windows checks. It is a thin
bootstrap; retain lifecycle, diagnostics and recovery in Python owners.
Use `Invoke-WebRequest` with explicit failure handling, TLS verification and
bounded downloads. Neither separate curl nor Bash should be required.
Publish a review-first download/run path beside any one-line convenience form.
Do not publish a command pointing to a script that does not yet exist.

Detect actual OS and interpreter architecture, not only the current shell's
architecture. Resolve latest once to an exact stable release; support explicit
version, no-open, verbose and anonymous-public-download options equivalent to
the existing installer. Reject unsupported tuples before replacing a working
installation. Private logs identify the failed stage and one next action.

Use the existing uv installation when valid. Otherwise obtain an official
Windows uv artifact with pinned identity/integrity checks. While Git sources
remain dependencies, reuse valid Git or provision verified user-local Git from
its official distribution, including license notices and an exact receipt.
Do not silently install a machine-wide Git package or require winget.
Record provenance and constrain archive extraction to the owned directory.
Refresh only the child/current process PATH; preserve personal Git configuration.
An anonymous retry applies to that download process, not global account settings.

Request ordinary non-free-threaded Python 3.13 explicitly. Let uv manage it
alongside any system Python. Use absolute helper and launcher paths so no terminal
restart is needed. Do not rely on whichever `python` happens to resolve first.

Verify the wheel against the release checksum before installation. A checksum
served beside an artifact is integrity evidence, not an independent signature.
Preserve the current Core binary-only rule. Qualify all default host/worker
native dependencies and fail with a supported-package explanation rather than
trying MSVC/Rust builds. Pure-Python Git packages may still need packaging.

Current manifests follow component branches. Preserve that update policy:
record the exact resolved host and worker graphs, qualify candidates before
activation, and retain the prior qualified generation for rollback. Do not
convert a one-time tested revision into an undocumented permanent pin.
For initial installation, distribution-owned constraints or a verified wheel
set may reproduce a qualified graph; define and check that artifact before
advertising reproducibility. A top-level wheel alone does not freeze Git dependencies.

Re-running setup must reuse the same data identity, refuse an unmanaged startup
entry, and retain chats, settings and credentials. Failed installation must not
reset user data. Removal unregisters only owned startup/program files; data
removal remains a separate explicit action.

## Platform boundaries

### Authentication that works with Windows Hello accounts

Recommendation: the authenticated owner's Windows session can authorize a local
browser through the launcher. Identify the owner by process-token **SID**, not
display name, environment variables or a guessed email address.
Do not require a Hello PIN in a password form: it is not an account password.
A `LogonUser` password-only path would not cover all passwordless Microsoft
and Entra accounts.

The owner launcher authenticates using the private existing local control
credential and requests a distinct short-lived, single-use browser grant.
Bind it to the host/data identity and exact origin. Store a verifier, consume
atomically, and exchange for the existing HttpOnly session cookie.
Do not place the long-lived control credential in a browser URL.
Design the grant handoff to avoid request/access logs, referrer leakage and
persisted browser history; strip transient browser material immediately.
Qualify CSRF, login-CSRF, expiry, replay and cross-origin rejection before use.

Loopback alone is never authentication. Preserve Host/Origin checks, API
authorization, TLS policy and browser-cookie protections.
This delegates existing OS-session authority; it does not prove a fresh Hello
challenge. A new native Hello challenge is a different product/security promise.
Keep PAM authentication unchanged for existing POSIX deployments.

Remote browsers need an explicit owner-approved browser-pairing design over
trusted configured HTTPS. Do not silently reuse terminal/companion grants with
broader authority. Until that path is qualified, report its limitation rather
than offering the unusable Windows password form.

### Background lifetime

Recommendation: an interactive-token Scheduled Task under the owner SID,
least privilege, with a user-logon trigger and no stored password.
Do not use LocalSystem or S4U to avoid account-password prompts.
Use absolute executable/data/workspace/shared-state paths and explicit quoting.
Configure indefinite execution, restart-on-failure and battery behavior
deliberately. Automatic logon startup must not open a browser.

Work survives browser/terminal closure while the owner remains signed in.
This model does **not** promise execution after sign-out or before sign-in.
If that is required, design a separate headless service deployment, including
network credentials and ownership. Do not pretend a logon task provides it.

Integrate install/start/stop/restart/status/logs with existing lifecycle commands.
Task registration is not health: verify version, data identity, authenticated
response and actual task process. Preserve the single host/reset exclusion.
An occupied port or unrelated task must never be adopted or terminated.
If policy blocks registration, explain the failure and offer explicit foreground
`serve` once that path itself works; do not silently elevate or report turnkey success.

### Storage and execution safety

Keep each mechanism with its existing owner; avoid a general portability
framework or duplicated conversation authority.

| Boundary | Required Windows behavior |
| --- | --- |
| Private storage | Owner/SYSTEM DACLs on app-owned secret storage and temporaries before secret writes; validate existing/custom roots. POSIX mode checks do not prove Windows confidentiality. |
| Contained file operations | Handle-based identity, reparse/junction refusal and replacement detection for workspace creation, attachments, outputs, themes and task storage. Retain bounded reads and configured access boundaries. |
| Locks and durable writes | Preserve shared host/exclusive repair locking, memory serialization, and Foundation single-writer/fence semantics. Flush and atomically replace on the same volume; never delete the destination first to bypass a sharing failure. |
| Worker transport | Bounded Windows-compatible pipe reads, explicit UTF-8 protocol bytes, separate logs, EOF and cancellation that cannot strand shutdown. Retain message-size limits. |
| Process lifecycle | Passive process-handle liveness, never Windows `os.kill(pid, 0)`. Job Objects contain owned workers/preparation descendants before tool execution; graceful cancellation precedes bounded termination. Unknown effects remain unknown. |

Python 3.13 provides Windows `os.fchmod` and special handling for new
`mkdir(mode=0o700)` directories. Do not misclassify `fchmod` as an unavailable API
at this Python floor. These APIs do not validate existing DACLs, containment or
directory durability. Inspect actual Windows guarantees at each boundary.

Resolve host venv interpreters as `Scripts/python.exe` on Windows and `bin/python`
on POSIX, using an existing owner/helper where possible. Do not rewrite intentional
Linux **guest** paths in remote test-runner protocols.
Job containment must not kill the detached update helper that must outlive the old
host. Validate nested-job restrictions and escape prevention separately.

Optional Linux controllers must not crash ordinary startup. Gate unavailable
actions explicitly; preserve retained resource records without launching, deleting
or silently repairing them. Local Linux Docker/Incus testing is not a native
Windows prerequisite. Remote Linux runners retain their own supported contract.
Publishing and task transfer require their own faithful Windows durability/locking
checks or explicit limitations; do not weaken their promises to make startup pass.

## Source-backed implementation dependencies

The following are observed source assumptions, not recorded Windows test failures.
References identify the assessed source; line numbers may move during implementation.

| Work and owner | Evidence at the assessed revision | Required increment and falsifier |
| --- | --- | --- |
| Unified startup/repair | `cli.py:214`, `reset.py:391`; `service.py:778`; `amplifier_publishing/publisher.py:253`; `amplifier_portability/protocol.py:37` | Portable shared/exclusive repair lock; isolate optional constructors. Fails if `serve` imports `fcntl` or skips ownership/durability. |
| Foundation session state | Locked Foundation `amplifier_foundation/session/shared_state.py`, `_ensure_supported` and writer acquisition; consumed at `runtime_worker.py:838` | Windows ownership/private storage/atomic publication backend through the existing API, plus cooperative release transport. Fails if two contenders write or crash recovery overwrites newer history. |
| Memory store | Locked memory `src/amplifier_memory/store.py:69`, `:612`; reached through `memory_consolidation.py:7` | Portable exclusive store lock and non-inheritable descriptors. Fails if host import requires `fcntl`, timeout mutates data, or a dead holder strands the lock. |
| Unified login/lifecycle | `auth.py:141`, `deployment_service.py:264`, `installation_health.py:37`, `app_updates.py:29` | Owner authorization and Windows task backend shared with health/update/reset. Fails if login requires PAM or task registration is reported as readiness. |
| Unified I/O/process/files | `runtime_worker.py:26`, `:1696`; `generation_leases.py:19`; `workspace_placement.py:151`; `outputs.py:45`; `task_resources.py:95`; `theme_library.py:173`; `managed_deletion.py:497`; `smart_tools.py:605` | Platform-specific implementations preserving containment, encoding and cleanup. Fails on Unicode corruption, junction substitution, unsafe liveness inspection or surviving owned descendants. |

Paths without a package prefix are under `amplifier_web/`. Dependency assessments
must use the exact release-resolution receipts, not a convenient sibling checkout.
The assessed lock records Foundation `8d26ece...`, memory `b011380...`, and
Context Intelligence `a80ce69...`; capture full IDs from `uv.lock` for each check.
Core 2.0.2 already lists `cp311-abi3-win_amd64` in that lock; an abi3 wheel can
support ordinary CPython 3.13. Native-wheel availability is not runtime qualification.

Audit the default tools/providers as well. A tool named Bash may require Git Bash
or need an explicit Windows implementation. Do not silently reinterpret shell
programs as PowerShell, install WSL, or remove default capabilities. Qualify a real
file-backed task through the exact installed bundle/module graph.

These changes advance optionality and faithful ownership/recovery under
[CB1–CB6](../../contracts/component-boundaries.v1.md) and
[AP2–AP6](../../contracts/agent-protocol.v1.md). No locked contract is amended by
this document. Changes to promised lifetime/authentication or deliberate feature
omissions must be resolved explicitly rather than hidden as compatibility work.

## Delivery order and independent acceptance

Each stage returns exact-source evidence. None is a substitute for the next.

1. **Safety and upstream mechanisms:** land the passive liveness fix; qualify
   Foundation writer/release and memory locks on Windows. Preserve POSIX behavior.
   Integrate upstream only after its merge/CI; do not publish downstream first.
2. **Host runtime:** portable repair lock, storage, optional-controller isolation,
   pipe transport, process trees and default tool behavior. Prove host start,
   first worker, file tool, Stop and recovery against combined candidates.
3. **Owner entry and lifecycle:** resolve the policy choices below; implement
   local browser authorization and user-logon task, including health, update,
   rollback and reset. An unchanged data root must remain the same identity.
4. **Bootstrap:** implement PowerShell setup with verified prerequisites and
   release artifacts. Exercise missing/preinstalled helpers, an incompatible
   system Python, partial install, bad checksum, proxy/policy failure and rerun.
5. **Platform delivery:** run the [native acceptance checks](../validation/release-acceptance.md#native-windows-candidate-checks)
   on a clean Windows account and the affected Linux/macOS/WSL regressions.
   Only then add a supported native-Windows command to the README.

Run focused Windows mechanism tests automatically on a real Windows runner;
mocked platform branches on Linux are regression coverage only. Follow the
[local-first release policy](../validation/release-qualification.md); do not add
every unrelated suite as a foreground gate. First support qualification is still
required for the new platform. CI runners with Python/Git preinstalled do not
prove a vanilla-machine bootstrap.

For multi-repository changes, use one isolated combined-source integration
environment as well as native Windows. Retain source revisions, dependency graphs,
artifact hashes, OS/architecture, account class, commands, results and limitations.
Keep private usernames, SIDs, paths, tokens and screenshots out of public evidence.

## Policy choices awaiting the product owner

| Choice | Recommendation | Consequence |
| --- | --- | --- |
| Is the signed-in owner OS session sufficient to authorize a browser? | Yes, through a narrow single-use launcher grant. | Supports passwordless accounts without asking for the OS password. A fresh Hello challenge would add a different security requirement. |
| Must work continue after Windows sign-out? | Not for the ordinary user-logon deployment. | Browser and terminal closure are supported; sign-out is a documented lifecycle boundary. Always-on execution needs a separately designed service mode. |

These are recommendations, not approvals inferred from the implementation request.
Independent storage, process and dependency work does not need to wait for them.
Remote browser access and architecture coverage remain explicit qualification work,
not an implicit promise or a silent scope reduction.

## Current delivery status

This document records the design and source audit. The accompanying README change
selects 3.13 in manual commands and identifies the current native-Windows limitation.
Neither change supplies a working Windows installer or closes the upstream and
runtime dependencies. Check the candidate's actual diff and acceptance records
before describing any implementation item as complete.

The accompanying safety increment replaces Windows lease liveness signaling with
`OpenProcess(SYNCHRONIZE)` and a zero-timeout wait. Uncertain results retain storage.
Its focused workflow runs the real sentinel on Windows, Linux and macOS; no remote
workflow run was observed during this assessment.

Local verification on Linux with Python 3.13: **14 tests passed**, including a real
subprocess survival check and stubbed Win32 outcomes, using:

```sh
python -I -S -B -c "import sys, unittest; sys.path.insert(0, '.'); suite = unittest.TestSuite(unittest.defaultTestLoader.discover('tests', pattern=pattern) for pattern in ('test_generation_leases.py', 'test_installation_guidance.py')); result = unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
```

Local link targets, workflow YAML parsing and `git diff --check` also passed.
Independent source review found no actionable defect in this increment.
The existing pytest installer suite could not start because pytest was absent
from the executing interpreter. An isolated check did not start because repository
snapshot admission failed; it is not an isolated-test pass. Native Windows,
macOS and WSL runtime execution remain unverified here.