// Exercise the packaged recovery workflow, then one explicit qualification input.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';

export async function activateRestoredFixture({root,directory,nativeFile,expected,archiveFile,archiveReceipt,api,installed}) {
 const original=await readFile(join(directory,'application.json'),'utf8');
 const request={schema:'unified-installation-recovery-v1',directory,
  commandId:'restore-installation-once',archiveFile,archiveSha256:archiveReceipt.archiveSha256,
  manifestDigest:archiveReceipt.manifestDigest,expected,stoppedCommandId:'archive-stop',
  native:{engineId:'amplifier',configurationFile:nativeFile,cwd:join(root,'workspace'),destination:{rootId:'fixture',name:'restored-native'}},
  privateContentReviewed:true,credentialsReviewed:true,
  writerRetirementReviewDigest:createHash('sha256').update('All owned fixture processes and external writers are retired').digest('hex')};
 let prepared,reconciliation;
 if(process.env.FULL_OWNER_RECOVERY_FAULTS==='1'){
  await assert.rejects(api.prepareInstallationRecovery(request,{publicationCheckpoint:async phase=>{
   if(phase==='original-retained')throw Error('Installed publication interruption');
  }}),/Installed publication interruption/);
  const input={directory,commandId:request.commandId},review=await api.inspectInstallationRecovery(input);
  assert.equal(review.canRollback,true);assert.equal(review.serviceStarted,false);
  await assert.rejects(api.reconcileInstallationRecovery({...input,decision:'complete',reviewDigest:'0'.repeat(64)}),/proof_changed/);
  const rolledBack=await api.reconcileInstallationRecovery({...input,decision:'rollback',reviewDigest:review.reviewDigest});
  assert.equal(rolledBack.rolledBack,true);assert.equal(rolledBack.serviceStarted,false);
  assert.equal(await readFile(join(directory,'application.json'),'utf8'),original);
  const next=await api.inspectInstallationRecovery(input);
  prepared=await api.reconcileInstallationRecovery({...input,decision:'complete',reviewDigest:next.reviewDigest});
  reconciliation={interruptedAt:'original-retained',staleReviewRefused:true,rollbackPreservedOriginal:true,explicitCompletion:true,workReplayed:false};
 }else prepared=await api.prepareInstallationRecovery(request);
 assert.equal(prepared.serviceStarted,false);assert.equal(prepared.workReplayed,false);
 assert.deepEqual(await readFile(join(directory,'recovery-restore-installation-once/original-application.json'),'utf8'),original);
 const retry=await api.prepareInstallationRecovery({schema:'unified-installation-recovery-v1',directory,
  commandId:'restore-installation-once',archiveFile,archiveSha256:archiveReceipt.archiveSha256,
  manifestDigest:archiveReceipt.manifestDigest,expected,stoppedCommandId:'archive-stop',
  native:{engineId:'amplifier',configurationFile:nativeFile,cwd:join(root,'workspace'),destination:{rootId:'fixture',name:'restored-native'}},
  privateContentReviewed:true,credentialsReviewed:true,
  writerRetirementReviewDigest:createHash('sha256').update('All owned fixture processes and external writers are retired').digest('hex')});
 assert.equal(retry.previouslyPrepared,true);
 await api.readInstalledServiceConfiguration(directory);
 await writeFile(join(root,'restore-activation-request.json'),JSON.stringify({commandId:'restored-explicit-input',text:'RESTORED-CHECK-42: confirm the saved artifact after restoring this installation.',sourceHome:prepared.preservedNativeHome,restoredHome:prepared.restoredNativeHome}),{flag:'wx',mode:0o600});
 installed.supervisor=await api.openInstalledService(directory);
 installed.supervisor.service.resume({commandId:'restore-resume',stoppedCommandId:'archive-stop',expected});
 const resumed=await installed.supervisor.service.waitFor('restore-resume');
 assert.equal(resumed.status,'ready',JSON.stringify(resumed));assert.equal(resumed.admissionSettlement.state,'settled',JSON.stringify(resumed));
 let result;
 for(let i=0;i<1200;i++){
  const error=await readFile(join(root,'restore-activation-error.txt'),'utf8').catch(e=>{if(e.code==='ENOENT')return null;throw e;});
  assert.equal(error,null,error??'');
  result=await readFile(join(root,'restore-activation-result.json'),'utf8').then(JSON.parse).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
  if(result)break;await new Promise(resolve=>setTimeout(resolve,50));
 }
 assert.ok(result,'Restored signed child did not finish its explicit acceptance input');
 return {prepared,resumed,catalogProjection:prepared.catalog,...result,...(reconciliation?{reconciliation}:{}),configurationReboundBy:'packaged-local-recovery-workflow',sourceConfigurationPreserved:true};
}
