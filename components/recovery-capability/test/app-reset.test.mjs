import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
const {createRecoveryCapabilities}=await import(process.env.RECOVERY_PACKAGE_MODULE??'../dist/index.js');
const {AppResets}=await import(new URL('./app-reset.js',process.env.RECOVERY_PACKAGE_MODULE?new URL('file://'+process.env.RECOVERY_PACKAGE_MODULE):new URL('../dist/index.js',import.meta.url)).href);
const ready=process.env.RECOVERY_HOST_MODULE&&process.env.APP_RESET_BRIDGE_MODULE&&process.env.APP_RESET_NOTIFICATIONS_MODULE&&process.env.APP_RESET_PYTHON;
const {createHost}=ready?await import(process.env.RECOVERY_HOST_MODULE):{};
const {AdminConnection}=ready?await import(process.env.APP_RESET_BRIDGE_MODULE):{};
const {createNotificationsCapability}=ready?await import(process.env.APP_RESET_NOTIFICATIONS_MODULE):{};
const wait=async fn=>{for(let i=0;i<1000;i++){const r=await fn();if(r)return r;await new Promise(r=>setTimeout(r,5));}throw Error('Timed out waiting for queued recovery job');};
test('non-success owner result bodies never enter durable product jobs',async()=>{
 for(const state of ['running','unknown','refused']){
  const owner={id:'notifications',parts:['notifications.settings'],perform:async(operation,args)=>({receipt:{ownerId:'notifications',commandId:args.commandId,operation,state,...(state==='refused'?{executed:false}:{}),result:{private:{token:'PRIVATE-OWNER-CONTENT'}},createdAt:1,replayed:false}})};
  const reset=new AppResets([owner]),job={accountId:'account',commandId:'outer',operation:'recovery.appReset.prepare',args:{parts:owner.parts},context:{clientId:'owner'}};const persisted=[];
  await reset.perform(job,()=>persisted.push(JSON.stringify(job)));
  assert.equal(Object.hasOwn(job.appResetReceipts.notifications,'result'),false);
  assert.ok(persisted.every(v=>!v.includes('PRIVATE-OWNER-CONTENT')));
 }
});
test('malformed successful proof and nested receipt scalars are refused before product persistence',()=>{
 const owner={id:'notifications',parts:['notifications.settings'],perform:async()=>({})},reset=new AppResets([owner]);
 const result={ownerId:owner.id,preparedId:'review',reviewHash:'a'.repeat(64),parts:owner.parts,postResetRevision:'opaque',restored:false,preserved:['commands'],replayed:false};
 const receipt={ownerId:owner.id,commandId:'exact',operation:'apply',state:'succeeded',result,replayed:false,createdAt:1,settledAt:2};
 assert.deepEqual(reset.receipt({receipt},owner,'exact','apply').result,result);
 for(const key of ['ownerId','executed','replayed','createdAt','settledAt'])assert.throws(()=>reset.receipt({receipt:{...receipt,[key]:{token:'PRIVATE'}}},owner,'exact','apply'),/Invalid public/);
 for(const [key,value] of Object.entries({ownerId:{token:'PRIVATE'},preparedId:{token:'PRIVATE'},reviewHash:'bad',parts:['native.app-bundle-default'],postResetRevision:{token:'PRIVATE'},preserved:[{token:'PRIVATE'}],restored:'false',replayed:{token:'PRIVATE'},canonicalFilesChanged:1,sharedSettingsChanged:true}))assert.throws(()=>reset.receipt({receipt:{...receipt,result:{...result,[key]:value}}},owner,'exact','apply'),/Invalid public/);
 const review={ownerId:owner.id,preparedId:'review',reviewHash:'a'.repeat(64),parts:owner.parts,revision:'opaque',expiresAt:100,containsPrivateContent:true,credentialsIncluded:false,coverage:'explicit-app-local-parts',preserved:['commands'],omissions:['unselected'],restoresCommandId:null,items:[{part:owner.parts[0],operation:'reset-to-disabled-defaults'}]};
 for(const change of [{preserved:[{token:'PRIVATE'}]},{restoresCommandId:{token:'PRIVATE'}},{items:[{part:owner.parts[0],operation:{token:'PRIVATE'}}]},{credentialsIncluded:{token:'PRIVATE'}}])assert.throws(()=>reset.receipt({receipt:{...receipt,operation:'prepare',result:{...review,...change}}},owner,'exact','prepare'),/Invalid/);
});
async function fixture(){
 const directory=await mkdtemp(join(tmpdir(),'app-reset-vertical-'));for(const name of ['home','app','workspace','notifications'])await mkdir(join(directory,name));
 const config=join(directory,'native.json'),notificationConfig=join(directory,'notification.json');
 await writeFile(config,JSON.stringify({home:join(directory,'home'),appHome:join(directory,'app'),adminWorkspaceRoots:[join(directory,'workspace')],adminMaintenance:true,workerCommand:['/impossible/no-provider']}));
 await writeFile(notificationConfig,JSON.stringify({stateDirectory:join(directory,'notifications')}));
 await writeFile(join(directory,'app','admin-settings.yaml'),'bundle:\n  active: old-default\n  unrelated: retain\nother: unchanged\n');await writeFile(join(directory,'home','keys.env'),'fixture-shared-secret');
 const admin=new AdminConnection({command:process.env.APP_RESET_PYTHON,args:['-m','amplifier_acp','--config',config],cwd:directory,resolveWorkspace:()=>join(directory,'workspace')});
 const notifications=createNotificationsCapability({owner:{command:process.env.APP_RESET_PYTHON,args:['-m','amplifier_unified_notifications.server','--config',notificationConfig],cwd:directory},inspectSession:async s=>({session:s}),waitForTurn:async()=>({completed:false})});
 let host,owner,lost=false,partial=false;
 const actualNative=(op,args,ctx)=>admin.perform(op,args,ctx),native=async(op,args,ctx)=>{const result=await actualNative(op,args,ctx);if(lost&&op==='maintenance.appReset.apply'){lost=false;throw Error('Lost successful native acknowledgement');}return result;};
 const options={directory:join(directory,'recovery'),nativeAuthority:'native',nativeMaintenance:await admin.maintenanceCapabilities(),nativeAdmin:native,appResetOwners:[{...notifications.appReset,perform:async(op,args,...rest)=>{if(partial&&op==='apply')throw Error('Uncertain notification transport');return notifications.appReset.perform(op,args,...rest)}}],authorize:async(ctx,operation,args)=>{if(args.parts?.includes('notifications.credentials')&&ctx.clientId!=='owner')throw Error('Explicit account authorization denied');return {accountId:'account'};},resolveSession:async()=>{throw Error('No session lookup for app reset')},quiescence:{admitQuiescence:a=>host.admitQuiescence(a),inspectQuiescence:()=>host.inspectQuiescence(),quiescenceReceipt:c=>host.quiescenceReceipt(c),releaseQuiescence:a=>host.releaseQuiescence(a),withQuiescenceMaintenance:(input,fn)=>host.withQuiescenceMaintenance(input,()=>admin.withMaintenanceFence(input,fn))}};
 owner=createRecoveryCapabilities(options);
 host=await createHost({stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[directory],engines:[{id:'none',command:'/impossible/worker'}],capabilities:{manifest:owner.manifest,quiescenceAccess:owner.quiescenceAccess,action:(...args)=>owner.action(...args),read:(...args)=>owner.read(...args)},quiescence:{instanceId:'reset-test',dataScope:'reset-private',requiredOwners:['native-admin','notifications','recovery'],coverage:{nativeHost:[],capabilities:{recovery:'recovery'}},participants:[admin.quiescenceParticipant,notifications.quiescenceParticipant,{id:'recovery',acquire:async c=>{const lease=await owner.quiescenceParticipant.acquire(c);return lease?{...lease,release:(outcome,proof)=>owner.quiescenceParticipant.reconcileRelease({...c,outcome,proof})}:null},reconcileRelease:c=>owner.quiescenceParticipant.reconcileRelease(c)}],verifyRelease:async request=>{const value=owner.readReleaseEvidence(request);assert.ok(value);return {verified:true,fenceId:value.fenceId,commandId:value.commandId,outcome:'unchanged',instanceId:value.instanceId,dataScope:value.dataScope,receiptId:value.receiptId};}}});
 let n=0;const call=(op,args={},commandId='cmd-'+(++n),clientId='owner')=>host.invokeCapability({version:1,topic:'recovery',channel:'ahp-root://',operation:op,args,commandId},{actorId:clientId,clientId,origin:'ui'});
 const settled=id=>wait(async()=>{const row=(await call('recovery.job',{jobId:id})).result;return ['prepared','succeeded','refused','unknown'].includes(row.state)?row:false;});
 const prepare=async(parts=['native.app-bundle-default','notifications.settings','notifications.credentials'],extra={})=>settled((await call('recovery.appReset.prepare',{parts,privateContentReviewed:true,credentialsReviewed:true,...extra})).result.id);
 await notifications.action({version:1,topic:'notifications',channel:'ahp-root://',operation:'notifications.save',commandId:'seed',args:{expectedRevision:0,patch:{topic:'private-topic',token:'private-token',enabled:true,preview:true}}},{clientId:'owner'});
 return {directory,admin,notifications,get owner(){return owner;},host,call,settled,prepare,lose:()=>lost=true,partial:()=>partial=true,restart:async()=>{await owner.close();owner=createRecoveryCapabilities(options);},close:async()=>{await owner.close();await host.close();await notifications.close();await admin.close();await rm(directory,{recursive:true,force:true});}};
}
test('actual installed host/native/notification reset retains originals, redacts credentials and reverses exactly',{skip:!ready},async()=>{
 const f=await fixture();try{
  const parts=['native.app-bundle-default','notifications.settings','notifications.credentials'];const preview=await f.prepare(parts);assert.equal(preview.state,'prepared');const manifest=(await f.call('recovery.preview',{jobId:preview.id})).result;assert.equal(manifest.owners.length,2);assert.ok(!JSON.stringify(manifest).includes('private-token'));assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  const original=await readFile(join(f.directory,'app','admin-settings.yaml'));const reset=await f.settled((await f.call('recovery.appReset.apply',{preparedJobId:preview.id,previewHash:preview.previewHash})).result.id);assert.equal(reset.state,'succeeded');assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.equal((await readFile(join(f.directory,'home','keys.env'))).toString(),'fixture-shared-secret');assert.ok(!(await readFile(join(f.directory,'app','admin-settings.yaml'))).toString().includes('active'));
  const settings=(await f.notifications.read({topic:'notifications',scope:'host',uri:f.notifications.manifest.topics.notifications.uri})).data.notificationSettings;assert.equal(settings.enabled,false);assert.equal(settings.tokenConfigured,false);
  const undo=await f.prepare(parts,{restoreResetJobId:reset.id});assert.equal(undo.state,'prepared');const restored=await f.settled((await f.call('recovery.appReset.restore',{preparedJobId:undo.id,previewHash:undo.previewHash,resetJobId:reset.id,expectedPostResetRevision:reset.result.postResetRevision})).result.id);assert.equal(restored.state,'succeeded');assert.deepEqual(await readFile(join(f.directory,'app','admin-settings.yaml')),original);assert.equal((await f.notifications.read({topic:'notifications',scope:'host',uri:f.notifications.manifest.topics.notifications.uri})).data.notificationSettings.tokenConfigured,true);
  assert.equal((await f.admin.inspectLifecycle()).workerStarted,false);assert.equal((await f.admin.inspectLifecycle()).work.providerSignIns,0);
 }finally{await f.close();}
});
test('causal stale second owner review refuses before first owner effect; credential authorization is independent',{skip:!ready},async()=>{
 const f=await fixture();try{
  await assert.rejects(f.call('recovery.appReset.prepare',{parts:['notifications.credentials'],privateContentReviewed:true,credentialsReviewed:true},'denied','stranger'),/authorization/);
  const preview=await f.prepare();
  await assert.rejects(f.call('recovery.appReset.apply',{preparedJobId:preview.id,previewHash:preview.previewHash},'denied-apply','stranger'),/authorization/);
  await assert.rejects(f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.appReset.prepare',commandId:'agent',args:{parts:['native.app-bundle-default'],privateContentReviewed:true}},{clientId:'owner',origin:'agent',session:'ahp-session:/owned'}),/account-level user/);
  const original=await readFile(join(f.directory,'app','admin-settings.yaml'));
  await f.notifications.action({version:1,topic:'notifications',channel:'ahp-root://',operation:'notifications.save',commandId:'later',args:{expectedRevision:1,patch:{preview:false}}},{clientId:'owner'});
  const result=await f.settled((await f.call('recovery.appReset.apply',{preparedJobId:preview.id,previewHash:preview.previewHash})).result.id);assert.equal(result.state,'refused');assert.deepEqual(await readFile(join(f.directory,'app','admin-settings.yaml')),original);assert.equal(f.host.inspectQuiescence().intakeClosed,false);
 }finally{await f.close();}
});
test('lost successful owner response survives recovery restart and passive inspection never repeats effect',{skip:!ready},async()=>{
 const f=await fixture();try{
  const preview=await f.prepare(['native.app-bundle-default']);f.lose();const unknown=await f.settled((await f.call('recovery.appReset.apply',{preparedJobId:preview.id,previewHash:preview.previewHash},'apply-once')).result.id);assert.equal(unknown.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,true);const post=await readFile(join(f.directory,'app','admin-settings.yaml'));
  await f.restart();const observed=await f.call('recovery.reconcile',{jobId:unknown.id});assert.equal(observed.result.state,'succeeded',JSON.stringify({job:observed.result,gate:f.host.inspectQuiescence()}));assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.deepEqual(await readFile(join(f.directory,'app','admin-settings.yaml')),post);
 }finally{await f.close();}
});
test('partial cross-owner effects remain held and inspected without replay or compensation',{skip:!ready},async()=>{
 const f=await fixture();try{const preview=await f.prepare();f.partial();const unknown=await f.settled((await f.call('recovery.appReset.apply',{preparedJobId:preview.id,previewHash:preview.previewHash})).result.id);assert.equal(unknown.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,true);assert.equal((await f.call('recovery.reconcile',{jobId:unknown.id})).result.state,'unknown');assert.equal((await f.notifications.read({topic:'notifications',scope:'host',uri:f.notifications.manifest.topics.notifications.uri})).data.notificationSettings.tokenConfigured,true);}finally{await f.close();}
});
