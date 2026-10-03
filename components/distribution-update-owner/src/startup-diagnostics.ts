/** Bounded diagnostic data, never launch/stop/adoption authority. No raw error
 * message, cause, stack, log, argv, path or environment crosses this contract. */
export const startupReasons = {
  dependency_unavailable: "A required application module could not load. Verify the installed release and its dependencies.",
  configuration_invalid: "The application rejected its configuration. Review the configured values before starting it again.",
  transfer_workspace_mismatch: "Transfer staging is outside the configured workspace roots. Correct the staging location or workspace configuration.",
  permission_denied: "The application could not access a required resource. Check the service account permissions.",
  resource_missing: "A required startup resource was not found. Check the installed files and configured locations.",
  address_unavailable: "The configured listening address is unavailable. Check the address and whether its port is already in use.",
  syntax_invalid: "An application module or configuration could not be parsed. Verify the installed release and configuration format.",
  application_exception: "The application raised an exception while loading. Review its configuration and installed components.",
  process_exited: "The launched process exited before readiness was confirmed. Inspect the exit status and installed configuration.",
  connection_lost: "The owned process connection was lost before readiness was confirmed. Inspect its state before another launch.",
  handshake_unconfirmed: "The child ownership handshake was not confirmed. Inspect the launch state; do not assume a retry is safe.",
  launch_unconfirmed: "The operating system did not confirm the launch. Check the executable and working directory permissions.",
  readiness_unconfirmed: "The application did not confirm readiness. It may still be starting; inspect its state before another launch.",
} as const;
export type StartupReason = keyof typeof startupReasons;
export type StartupPhase = "spawn" | "handshake" | "initialization" | "readiness";
export interface StartupFailure {
  schema: "distribution-startup-failure-v1";
  phase: StartupPhase;
  reason: StartupReason;
  source: "child-bootstrap" | "supervisor";
  guidance: string;
  observedAt: number;
  exitCode?: number;
  signal?: string;
}
const phases = new Set(["spawn", "handshake", "initialization", "readiness"]);
const signals = new Set(["SIGABRT", "SIGBUS", "SIGFPE", "SIGILL", "SIGINT", "SIGKILL", "SIGSEGV", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGPIPE"]);
export function startupFailure(reason: StartupReason, phase: StartupPhase, source: StartupFailure["source"] = "supervisor"): StartupFailure {
  return { schema: "distribution-startup-failure-v1", phase, reason, source, guidance: startupReasons[reason], observedAt: Date.now() };
}
/** Whitelist known error codes and exact static configuration errors. Unknown
 * text stays private, even when short or apparently harmless. */
export function classifyStartupError(error: unknown): StartupReason {
  if (!(error instanceof Error)) return "application_exception";
  const code = (error as NodeJS.ErrnoException).code;
  if (["ERR_MODULE_NOT_FOUND", "MODULE_NOT_FOUND", "ERR_PACKAGE_PATH_NOT_EXPORTED", "ERR_DLOPEN_FAILED"].includes(code ?? "")) return "dependency_unavailable";
  if (["EACCES", "EPERM"].includes(code ?? "")) return "permission_denied";
  if (code === "ENOENT") return "resource_missing";
  if (["EADDRINUSE", "EADDRNOTAVAIL"].includes(code ?? "")) return "address_unavailable";
  if (["ERR_INVALID_ARG_TYPE", "ERR_INVALID_ARG_VALUE", "ERR_INVALID_URL"].includes(code ?? "")) return "configuration_invalid";
  if (error.message === "Transfer staging must be within configured workspace roots") return "transfer_workspace_mismatch";
  if (error instanceof SyntaxError) return "syntax_invalid";
  return "application_exception";
}
/** Project known fields again at each durable/public boundary. A diagnostic
 * property added by an arbitrary adapter cannot smuggle its error body. */
export function parseStartupFailure(value: unknown): StartupFailure | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const v = value as Record<string, unknown>;
  if (v.schema !== "distribution-startup-failure-v1" || typeof v.reason !== "string" ||
      !Object.hasOwn(startupReasons, v.reason) || !phases.has(v.phase as string) ||
      !["child-bootstrap", "supervisor"].includes(v.source as string) ||
      !Number.isSafeInteger(v.observedAt) || (v.observedAt as number) < 0) return;
  const result: StartupFailure = { schema: "distribution-startup-failure-v1", reason: v.reason as StartupReason,
    phase: v.phase as StartupPhase, source: v.source as StartupFailure["source"], observedAt: v.observedAt as number,
    guidance: startupReasons[v.reason as StartupReason] };
  if (Number.isInteger(v.exitCode) && (v.exitCode as number) >= 0 && (v.exitCode as number) <= 255) result.exitCode = v.exitCode as number;
  if (typeof v.signal === "string" && signals.has(v.signal)) result.signal = v.signal;
  return result;
}
export class OwnedStartupError extends Error {
  readonly startupFailure: StartupFailure;
  constructor(failure: StartupFailure) {
    super("owned_startup_failed");
    const safe = parseStartupFailure(failure);
    if (!safe) throw Error("invalid_startup_diagnostic");
    this.startupFailure = safe;
  }
}
export function startupFailureFrom(error: unknown): StartupFailure | undefined {
  return error instanceof Error ? parseStartupFailure((error as OwnedStartupError).startupFailure) : undefined;
}
