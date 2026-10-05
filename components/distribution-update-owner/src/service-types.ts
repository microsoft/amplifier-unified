import { token, identity, type PreparedRelease, type ReleaseIdentity } from "./types.js";
import type { InitialProvisioningEvidence } from "./lifecycle.js";
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
  operation: "start" | "stop" | "resume" | "adopt" | "handoff";
  status: "running" | "refused" | "stopped" | "ready" | "unknown";
  phase:
    | "accepted"
    | "initial_claim_requested"
    | "initial_start_requested"
    | "admission_requested"
    | "handoff_requested"
    | "held"
    | "stop_requested"
    | "interrupt_requested"
    | "interrupted"
    | "stopped"
    | "resume_requested"
    | "ready"
    | "stop_refused"
    | "ownership_unproven";
  expected: ServiceIdentity;
  observed?: ServiceIdentity;
  /** Forward activation qualified by the signed release owner, never request evidence. */
  activation?: ServiceActivationBinding;
  /** Consumed pristine authority, retained on this same start operation. */
  initialClaim?: InitialProvisioningEvidence;
  fenceId?: string;
  stoppedCommandId?: string;
  resumeCommandId?: string;
  exitProof?: OwnedExitProof;
  /** Authenticated held host-fence coverage, captured before child exit. Older
   * receipts may resume, but cannot qualify an offline whole-owner snapshot. */
  qualifiedOwners?: string[];
  /** Local interruption is never a completed/drained business outcome. */
  interruption?: { authorizationId: string; outcome: "unknown" | "interrupted" };
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
  /** Trusted update owner alone selects previously retained bytes for rollback. */
  retainedRollback?: true;
  target?: PreparedRelease;
  /** Local platform custody, retained on this existing command before readiness. */
  localCustody?: ServiceProcessCustody;
}
export interface ServiceProcessCustody {
  kind: "linux-unit";
  unit: string;
  invocationId: string;
  instanceId: string;
  dataScope: string;
  releaseDigest: string;
}
export interface ServiceActivationBinding {
  schema: "distribution-service-activation-v1";
  target: ReleaseIdentity;
}
export function serviceActivation(value: ServiceActivationBinding): ServiceActivationBinding {
  if (value?.schema !== "distribution-service-activation-v1") throw Error("service_activation_invalid");
  return {schema:value.schema,target:identity(value.target)};
}
export interface ServiceHostPort {
  admitServiceStop(request: ServiceCommand): Promise<unknown>;
  /** Optional single local gate; closes intake WITHOUT claiming drained work. */
  closeServiceIntake?(request: ServiceCommand): Promise<unknown>;
  releaseServiceStart?(request: {fenceId: string; commandId: string; evidence?: unknown}): Promise<unknown>;
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
  activation?: ServiceActivationBinding;
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
  const { target, startupFailure, localCustody, ...receipt } = value;
  const safe = parseStartupFailure(startupFailure);
  return structuredClone({...receipt, ...(safe ? {startupFailure:safe} : {})});
}

/** Existing authenticated service control; initial activation has no previous
 * service, stop, exit, or resume receipt. Caller evidence carries no authority. */
export interface ServiceInitialStartRequest {
  fenceId: string;
  commandId: string;
  purpose: string;
  instanceId: string;
  dataScope: string;
  serviceIdentity?: ServiceIdentity;
  evidence: unknown;
}
export interface ServiceInitialStartProof {
  kind: "service-initial-start";
  verified: true;
  fenceId: string;
  commandId: string;
  expected: ServiceIdentity;
  observed: ServiceIdentity;
  instanceId: string;
  dataScope: string;
  claimReceiptId: string;
  readyReceiptId: string;
  receiptId: string;
  target: ReleaseIdentity;
}
