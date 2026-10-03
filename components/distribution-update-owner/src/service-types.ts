import { token, type PreparedRelease } from "./types.js";
import type { OwnedExitProof } from "./posix-process.js";
import { parseStartupFailure, type StartupFailure } from "./startup-diagnostics.js";
import type { ExistingStateHandoffClaim } from "./existing-state-types.js";
export interface ServiceIdentity {
  installationId: string;
  dataScope: string;
  ownerId: string;
  instanceId: string;
  releaseDigest: string;
}
export interface ServiceCommand {
  commandId: string;
  expected: ServiceIdentity;
}
export interface ServiceReceipt {
  commandId: string;
  operation: "stop" | "resume" | "adopt" | "handoff";
  status: "running" | "refused" | "stopped" | "ready" | "unknown";
  phase:
    | "accepted"
    | "admission_requested"
    | "handoff_requested"
    | "held"
    | "stop_requested"
    | "stopped"
    | "resume_requested"
    | "ready"
    | "stop_refused"
    | "ownership_unproven";
  expected: ServiceIdentity;
  observed?: ServiceIdentity;
  fenceId?: string;
  stoppedCommandId?: string;
  resumeCommandId?: string;
  exitProof?: OwnedExitProof;
  /** Authenticated held host-fence coverage, captured before child exit. Older
   * receipts may resume, but cannot qualify an offline whole-owner snapshot. */
  qualifiedOwners?: string[];
  noEffect?: true;
  errorCode?: string;
  startupFailure?: StartupFailure;
  /** One-way source retirement or destination claim; no paths or credentials. */
  handoffClaim?: ExistingStateHandoffClaim;
  /** Source ledger only: service and update launch authority is permanently retired. */
  handoffRetired?: true;
  admissionSettlement?: {
    state: "pending" | "settled" | "unknown";
    updatedAt: number;
  };
  updatedAt: number;
}
export interface ServiceRecord extends ServiceReceipt {
  target?: PreparedRelease;
}
export interface ServiceHostPort {
  admitServiceStop(request: ServiceCommand): Promise<unknown>;
  inspectServiceLifecycle(): Promise<unknown>;
  serviceStopReceipt(commandId: string): Promise<unknown>;
  releaseServiceStop(request: {
    fenceId: string;
    commandId: string;
    outcome: "resumed" | "stop-refused" | "unknown";
    resumeCommandId?: string;
    evidence?: unknown;
  }): Promise<unknown>;
}
export interface ServiceReleaseRequest {
  fenceId: string;
  commandId: string;
  purpose: string;
  instanceId: string;
  dataScope: string;
  serviceIdentity?: ServiceIdentity;
  outcome: "resumed" | "stop-refused";
  resumeCommandId?: string;
  evidence: unknown;
}
export interface ServiceReleaseProof {
  verified: true;
  kind: "service-lifecycle";
  fenceId: string;
  commandId: string;
  dataScope: string;
  instanceId: string;
  receiptId: string;
  outcome: "ready" | "unchanged";
  serviceOutcome: "resumed" | "stop-refused";
  expected: ServiceIdentity;
  observed: ServiceIdentity;
  resumeCommandId?: string;
  exitReceiptId?: string;
  readyReceiptId?: string;
  refusalReceiptId?: string;
}
export function serviceIdentity(value: ServiceIdentity): ServiceIdentity {
  if (!value || !/^[a-f0-9]{64}$/.test(value.releaseDigest))
    throw Error("invalid_service_identity");
  return {
    installationId: token(value.installationId),
    dataScope: token(value.dataScope),
    ownerId: token(value.ownerId),
    instanceId: token(value.instanceId),
    releaseDigest: value.releaseDigest,
  };
}
export function sameService(a: ServiceIdentity, b: ServiceIdentity) {
  return (
    JSON.stringify(serviceIdentity(a)) === JSON.stringify(serviceIdentity(b))
  );
}
export function serviceReceipt(value: ServiceRecord): ServiceReceipt {
  const { target, startupFailure, ...receipt } = value;
  const safe = parseStartupFailure(startupFailure);
  return structuredClone({...receipt, ...(safe ? {startupFailure:safe} : {})});
}
