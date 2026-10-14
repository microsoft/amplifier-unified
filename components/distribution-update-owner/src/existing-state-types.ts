import type { ServiceIdentity, ServiceRecord } from "./service-types.js";
import type { ReleaseIdentity } from "./types.js";

/** Trusted launcher-to-launcher contract, never a browser-supplied exit claim. */
export interface ExistingStateHandoffCommand {
  commandId: string;
  stoppedCommandId: string;
  expected: ServiceIdentity;
  target: ReleaseIdentity;
  dataBindingDigest: string;
  participantIds: string[];
}
export interface ExistingStateHandoffClaim extends ExistingStateHandoffCommand {
  next: ServiceIdentity;
}
export interface ExistingStateHandoffProof {
  schema: "distribution-existing-state-handoff-v1";
  claim: ExistingStateHandoffClaim;
  /** Authentic source-owned stop, including its retired resume authority. */
  stopped: ServiceRecord;
}
export interface ExistingStateHandoffSource {
  /** Atomically retire the original launcher's resume right before returning.
   * Repeated exact requests may observe a claim; changed requests must refuse.
   * The adapter must authenticate its source, actual exit and held owner census.
   * Missing endpoints, PID files, operator text and copied JSON are insufficient. */
  claim(request: ExistingStateHandoffClaim): Promise<ExistingStateHandoffProof>;
}
