/** Implemented owner contract. Platform adapters must supply actual ownership
 * evidence; importing these types does not qualify an OS service manager. */
export type {
  ServiceIdentity,
  ServiceCommand,
  ServiceReceipt,
  ServiceHostPort,
  ServiceReleaseRequest,
  ServiceReleaseProof,
} from "@amplifier/unified-distribution-update-owner";
