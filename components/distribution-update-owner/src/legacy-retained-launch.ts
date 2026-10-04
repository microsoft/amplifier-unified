import {join} from 'node:path';
import {createAuthority} from './manual-authority.js';
import {PosixOwnedProcessLifecycle} from './posix-lifecycle.js';
import {prepared, same, token, type PreparedRelease} from './types.js';
import {inspectLegacyProcessRecovery, legacyRecoveryBinding, legacyRecoveryDigest,
  type LegacyRecoveryBinding} from './legacy-process-recovery.js';

/** Explicit, private retained-installation launch adapter. It shares the actual
 * supervisor's POSIX child owner; it never consumes pristine-installation
 * authority, adopts a PID, retries an activation or resumes conversation work.
 *
 * The external exclusion adapter must stay alive from old-service custody to
 * acquisition of the replacement's actual writer gates with intake closed.
 * This helper's exclusive claim prevents duplicate launches; it is NOT a writer
 * lock or proof that every historical launcher participates in exclusion. */
export function createLegacyRetainedLauncher(options: {
  directory: string;
  binding: LegacyRecoveryBinding;
  lifecycle: PosixOwnedProcessLifecycle;
  target(): Promise<PreparedRelease>;
  verifyPrepared(target: PreparedRelease): Promise<boolean>;
  assertExclusionHeld(): Promise<void>;
  /** Read-only exact old command/fence, owner census and retained authority. */
  assertRetainedUnchanged(): Promise<void>;
  /** Actual replacement locks and saved original holds, not just readiness. */
  assertReplacementHeld(instanceId: string): Promise<void>;
}) {
  const binding=legacyRecoveryBinding(options.binding);
  if (!(options.lifecycle instanceof PosixOwnedProcessLifecycle) ||
      options.lifecycle.processes.ownerId!==binding.expected.ownerId)
    throw Error('maintenance_lifecycle_owner_mismatch');
  const digest=legacyRecoveryDigest(binding);
  return async (request: {recoveryId:string;binding:LegacyRecoveryBinding;instanceId:string}) => {
    const recoveryId=token(request.recoveryId),instanceId=token(request.instanceId);
    if(legacyRecoveryDigest(legacyRecoveryBinding(request.binding))!==digest)
      throw Error('maintenance_launch_binding_changed');
    const receipt=await inspectLegacyProcessRecovery(options.directory);
    if(receipt.phase!=='restart_requested'||receipt.recoveryId!==recoveryId||
       receipt.nextInstanceId!==instanceId||legacyRecoveryDigest(receipt.binding)!==digest)
      throw Error('maintenance_restart_not_authorized');
    if(options.lifecycle.ownedPid!==null)throw Error('maintenance_lifecycle_already_used');
    await options.assertExclusionHeld();
    await options.assertRetainedUnchanged();
    const target=prepared(await options.target());
    if(!same(target.identity,binding.prepared)||!await options.verifyPrepared(target))
      throw Error('maintenance_target_unqualified');
    // An exclusive, fsynced permanent directory is consumed BEFORE any launch.
    // Even a lost write/spawn/readiness reply must not permit a second child.
    await createAuthority(join(options.directory,'retained-launch'),{
      schema:'legacy-retained-launch-v1',recoveryId,instanceId,bindingDigest:digest,
      receiptDigest:legacyRecoveryDigest(receipt),target,
    });
    if(legacyRecoveryDigest(await inspectLegacyProcessRecovery(options.directory))!==legacyRecoveryDigest(receipt))
      throw Error('maintenance_launch_receipt_changed');
    await options.assertRetainedUnchanged();
    await options.assertExclusionHeld();
    if(!await options.verifyPrepared(target))throw Error('maintenance_target_unqualified');
    await options.lifecycle.resumeOwned({commandId:recoveryId,instanceId,target,
      previousInstanceId:binding.expected.instanceId,dataScope:binding.expected.dataScope,
      signal:new AbortController().signal});
    // Startup/readiness does not release any old hold. The caller can only
    // record ready and reconcile that exact fence after these owners qualify.
    await options.assertReplacementHeld(instanceId);
    await options.assertExclusionHeld();
    const observed=await options.lifecycle.inspectOwned();
    if(observed.instanceId!==instanceId||observed.dataScope!==binding.expected.dataScope||
       !same(observed.identity,binding.prepared))throw Error('maintenance_readiness_unconfirmed');
  };
}
