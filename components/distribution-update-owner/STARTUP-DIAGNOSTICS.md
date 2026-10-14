# Owned process startup diagnostics

Version 0.13 adds optional `startupFailure` evidence to distribution-update and
service-resume receipts. The same bounded projection appears in owner change
notifications and durable diagnostics. Existing status, phase and error codes
retain their meaning. A failed restart or resume remains `unknown` until exact
passive reconciliation proves its outcome; a diagnostic never authorizes another
launch, adoption, signaling, intake release or replay.

## Public contract

```ts
interface StartupFailure {
  schema: "distribution-startup-failure-v1";
  phase: "spawn" | "handshake" | "initialization" | "readiness";
  reason: StartupReason;
  source: "child-bootstrap" | "supervisor";
  guidance: string;
  observedAt: number;
  exitCode?: number;
  signal?: string;
}
```

`StartupReason` and static guidance are exported as `startupReasons`. Reasons
cover unavailable dependencies, invalid configuration, transfer staging outside
configured workspaces, denied permissions, missing resources, unavailable
addresses, parse failures, application exceptions, process exit, connection loss,
and unconfirmed launch/handshake/readiness. Exit status is limited to 0–255;
signals use a fixed POSIX allowlist. No exception message, stack, cause, log body,
path, argv, environment, credential, PID or private channel token is included.
Unknown exceptions receive `application_exception`, without copying their text.

The child reports entry-module import/initialization rejection over its inherited
IPC connection after the ownership handshake. The parent accepts the first
allowlisted report bound to that connection, its private token and the exact
instance, only while startup is pending. This supplements the existing ownership
checks; a public diagnostic object cannot satisfy them. After authenticated
readiness, later failures remain runtime ownership failures. Uncaught failures
outside the awaited entry initialization may have only process-exit evidence.
Standard output and error remain uncaptured.

Initial launch has no update/resume operation receipt. It throws
`OwnedStartupError` (`message: "owned_startup_failed"`). Embedding launchers can
call `startupFailureFrom(error)` and include its optional result in their bounded
error response. The supervisor CLI does this automatically:

```ts
import { startupFailureFrom } from
  "@amplifier/unified-distribution-update-owner";

try {
  await lifecycle.startInitial(target, dataScope);
} catch (error) {
  const diagnostic = startupFailureFrom(error);
  // Publish the optional safe diagnostic with the caller's existing error code.
  // Do not publish the error itself or reinterpret this as retry permission.
}
```

`parseStartupFailure(value)` validates/project fields and regenerates guidance
from the reason. Use it when reading an arbitrary adapter's diagnostic; custom
text and extra properties are discarded. Receipt boundaries already apply it.
An optional report is evidence of the observed failed attempt, not a claim that
the current process is stopped. The existing authoritative child-exit proof
and exact authenticated readiness remain required. A later successful
reconciliation clears the active receipt diagnostic; historical events retain
the attempt's evidence.

## Verification and scope

Real POSIX child tests cover missing dependencies, configuration rejection,
large private error bodies, failed spawn, unconfirmed readiness, forged IPC
reports and failures after readiness. Durable tests cover update receipts,
service resume, reopen, no repeated launch, retained admission and successful
passive reconciliation. The package tests also run from an independently
installed npm artifact through its public export. These tests do not qualify a
production service manager or authorize changes to an existing installation.
