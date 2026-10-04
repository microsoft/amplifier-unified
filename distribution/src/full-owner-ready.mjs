/**
 * Startup reports declaration eligibility only. It cannot certify a backup,
 * stopped writers, captured bytes, or a restorable installed configuration.
 * Keep the v1 receipt field while reading the inventory's actual schema field.
 */
export function createFullOwnerReadyReceipt({source, identity, owners, inventory, releaseBinding}) {
 return {
  schema: 'full-owner-ready-v1',
  mode: source ? 'instrumented-source' : 'supervised',
  identity,
  owners,
  storageComplete: inventory.completeEligible === true,
  releaseBinding,
 };
}
