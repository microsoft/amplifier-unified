/** Proposed cross-owner contract; NOT an implemented platform service adapter.
 * Host owns held quiescence and validates durable lifecycle proof. A platform
 * owner supplies a stable owned process/job handle; PID/discovery is not proof.
 * Distribution owns signed installed identity and exact source qualification.
 */
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
  operation: "stop" | "resume" | "adopt";
  status: "refused" | "running" | "stopped" | "ready" | "unknown";
  phase:
    | "admission_requested"
    | "held"
    | "stop_requested"
    | "stopped"
    | "resume_requested"
    | "ready"
    | "ownership_unproven";
  expected: ServiceIdentity;
  observed?: ServiceIdentity;
  fenceId?: string;
  /** Authenticated durable platform exit receipt, never endpoint absence. */
  exitProof?: {
    ownerReceiptId: string;
    ownerId: string;
    instanceId: string;
    observedAt: number;
  };
  updatedAt: number;
}
export type ServiceObservation =
  | { state: "running"; identity: ServiceIdentity; ownership: "verified" }
  | { state: "stopped"; identity: ServiceIdentity; stoppedReceiptId: string }
  | {
      state: "unknown";
      reason: "ownership_unproven" | "observation_unavailable";
    };
export interface QualifiedServiceOwner {
  /** Passive authenticated observation. No PID lookup authorizes mutation. */
  inspect(): Promise<ServiceObservation>;
  /** CAS instance and durable command, close intake and hold EVERY configured
   * participant, record uncertainty BEFORE signaling the exact owned process.
   * Only authoritative process/job exit yields stopped; preserve fence receipt. */
  stop(command: ServiceCommand): Promise<ServiceReceipt>;
  /** Explicit caller action, not automatic retry. Require confirmed stopped
   * receipt + retained bytes and new instance; preserve state and release the
   * old host fence only after independent exact readiness + durable proof. */
  resume(
    command: ServiceCommand & { stoppedCommandId: string },
  ): Promise<ServiceReceipt>;
  /** No automatic crash adoption. Platform authority must attest the same owned
   * installation/process/job, then independently authenticate exact host/state.
   * Missing/ambiguous authority returns ownership_unproven, never fresh install. */
  adopt(command: ServiceCommand): Promise<ServiceReceipt>;
  /** Read-only; cannot turn lost stop/resume replies into repeated effects. */
  receipt(commandId: string): Promise<ServiceReceipt | null>;
  reconcile(commandId: string): Promise<ServiceReceipt>;
}
