import {stageNativeInstallationArtifact} from './installation-archive.js';

/** Trusted private transport factory only. The caller owns the configured
 * maintenance process and its external-writer retirement review. This adapter
 * never creates a session, starts Core, performs inference or accepts a path
 * selector from an agent/browser request. */
export function createNativeCoherentCaptureAdapter({engineId,cwd,stagingDirectory,connect}){
 if(typeof connect!=='function'||typeof engineId!=='string')throw Error('native_capture_adapter_invalid');
 return {engineId,async acquire(context){
  const connection=await connect();let admin,held,maintenance,artifact,inspection,closed=false;
  const life=(operation,args)=>connection.request('_amplifier/admin/lifecycle',{cwd,operation,args});
  const call=(operation,args={})=>connection.request('_amplifier/admin',{cwd,operation,args,
   ...(admin?{_meta:{'amplifier.dev/admin':admin}}:{})});
  const lifeContext={fenceId:context.captureId,commandId:context.captureId,purpose:'recovery',
   instanceId:context.expected.instanceId,dataScope:context.expected.dataScope};
  const assertHeld=async()=>{
   if(closed||!held)throw Error('native_capture_lease_lost');
   const live=await call('maintenance.capture.inspect',{captureId:held.captureId,nonce:context.captureId});
   if(live?.active!==true||live.captureId!==held.captureId||live.adminInstanceId!==held.adminInstanceId||
     live.nonce!==context.captureId||live.instanceId!==context.expected.instanceId||live.dataScope!==context.expected.dataScope||
     live.stopReceiptSha256!==context.stopReceiptDigest||!Array.isArray(live.artifacts))throw Error('native_capture_lease_lost');
   return {active:true,captureId:context.captureId,reviewedScopeDigest:context.reviewedScopeDigest,
    stopReceiptDigest:context.stopReceiptDigest,leaseId:held.captureId,artifacts:live.artifacts,authority:live};
  };
  try{
   await connection.request('initialize',{protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}});
   const acquired=await life('acquire',lifeContext);
   if(!acquired?.acquired||!acquired.lease)throw Error('native_admin_capture_refused');
   admin=Object.fromEntries(['fenceId','commandId','leaseId'].map(k=>[k,acquired.lease[k]]));
   held=await call('maintenance.capture.acquire',{commandId:context.captureId+'-hold',nonce:context.captureId,
    instanceId:context.expected.instanceId,dataScope:context.expected.dataScope,stopReceiptSha256:context.stopReceiptDigest});
   await assertHeld();
  }catch(error){await connection.close();throw error;}
  return {
   assertHeld,
   async capture({includeCredentials,credentialsReviewed}){
    if(artifact)throw Error('native_capture_already_used');
    await assertHeld();
    const plan=await call('maintenance.archive.create',{commandId:context.captureId+'-create',parts:['full-native-authority'],
     privateContentReviewed:true,includeCredentials,credentialsReviewed});
    const preview=await call('maintenance.archive.prepare',{commandId:context.captureId+'-prepare',planId:plan.planId,expectedRevision:0});
    maintenance=await call('maintenance.acquire',{commandId:context.captureId+'-snapshot-lease',previewHash:preview.previewHash});
    artifact=await call('maintenance.snapshot',{commandId:context.captureId+'-snapshot',leaseId:maintenance.leaseId,previewHash:preview.previewHash});
    const staged=await stageNativeInstallationArtifact({artifactId:artifact.artifactId,sha256:artifact.sha256,directory:stagingDirectory},
     {requestNative:async(operation,args)=>{await assertHeld();return call(operation,args);}});
    inspection=staged.inspection;
    await assertHeld();
    const a=staged.inspection.authority;
    return {...staged,descriptor:{id:'native-'+engineId,engineId,artifactId:artifact.artifactId,sha256:artifact.sha256,
     manifestDigest:staged.inspection.manifest.sha256,declaredRootIds:a.declaredRootIds,completeNativeAuthority:a.completeNativeBackup,
     externalWritersExcluded:true,externalWriterEvidence:a.externalWriters}};
   },
   async release({productSha256}={}){
    if(closed)return;
    try{
     if(maintenance)await call('maintenance.release',{leaseId:maintenance.leaseId});
     const released=await call('maintenance.capture.release',{commandId:context.captureId+'-release',captureId:held.captureId,nonce:context.captureId,
      ...(productSha256?{outcome:'sealed',artifactId:artifact.artifactId,artifactSha256:artifact.sha256,manifestDigest:inspection.manifest.sha256,productSha256}:{outcome:'aborted'})});
     if(released?.released!==true)throw Error('native_capture_release_unconfirmed');
     const adminReleased=await life('release',{...admin,outcome:'unchanged',proof:{verified:true,...lifeContext,outcome:'unchanged',receiptId:context.captureId+'-closed'}});
     if(adminReleased?.released!==true)throw Error('native_admin_release_unconfirmed');
     return {released:true,captureId:held.captureId,nonce:context.captureId,...(productSha256?{productSha256}:{})};
    }finally{closed=true;await connection.close();}
   },
  };
 }};
}
