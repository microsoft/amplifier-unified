import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const source=process.env.OWNER_SERVICE_SOURCE_ROOT;
const python=process.env.SERVICE_OWNERS_PYTHON;
const installed=process.env.OWNER_SERVICE_PACKAGE_ROOT;
const packages={history:'@amplifier/unified-history-capability',worktree:'@amplifier/unified-worktree-capability',recovery:'@amplifier/unified-recovery-capability',workspace:'@amplifier/unified-workspace-capability',notifications:'@amplifier/unified-notifications-capability',diagnostics:'@amplifier/unified-diagnostics-capability',recall:'@amplifier/unified-recall-capability',coordination:'@amplifier/unified-coordination-capability',publishing:'@amplifier/unified-publishing-capability',feedback:'@amplifier/unified-feedback-capability'};
const identity={installationId:'install',ownerId:'platform',dataScope:'scope',instanceId:'original',releaseDigest:'release'};
const context={fenceId:'fence',commandId:'stop',purpose:'service-stop',instanceId:'original',dataScope:'scope',serviceIdentity:identity};
const proof={verified:true,fenceId:'fence',commandId:'stop',outcome:'ready',instanceId:'replacement',dataScope:'scope',receiptId:'proof',kind:'service-lifecycle',serviceOutcome:'resumed',expected:identity,observed:{...identity,instanceId:'replacement'},resumeCommandId:'resume',exitReceiptId:'exit',readyReceiptId:'ready'};
const moduleFor=async(name,path)=>import(pathToFileURL(installed?createRequire(join(installed,'package.json')).resolve(packages[name]):resolve(source,'components',name+'-capability',path)).href);
const forbidden=()=>{throw Error('No host business effect is allowed in service qualification')};

for(const name of ['history','worktree','recovery','notifications','diagnostics','workspace','recall','coordination','publishing','feedback']){
 test(name+' retains exact service proof through owner replacement and rejects generic releases',{skip:(!source&&!installed)||!python,timeout:60000},async()=>{
  const directory=await mkdtemp(join(tmpdir(),'node-owner-service-')),workspace=join(directory,'workspace');await mkdir(workspace);
  let owner,participant;
  const create=async()=>{
   if(name==='history'||name==='worktree'){
    const m=await moduleFor(name,'src/index.js');owner=name==='history'?m.createHistoryCapability({directory,engineId:'native',nativeAdmin:forbidden,authorizeWorkspace:forbidden,adoptImportedSession:forbidden,importAdoptionReceipt:forbidden}):m.createWorktreeCapability({directory,gitWorker:{quiescenceCoverage:1,request:forbidden,close:async()=>{}},inspectSession:forbidden});participant=typeof owner.quiescenceParticipant==='function'?owner.quiescenceParticipant(name):owner.quiescenceParticipant;return;
   }
   if(name==='recovery'){
    const {createRecoveryCapabilities}=await moduleFor(name,'dist/index.js');owner=createRecoveryCapabilities({directory,nativeAuthority:'native',nativeAdmin:forbidden,authorize:async()=>({accountId:'owner'}),resolveSession:forbidden,quiescence:{}});participant=owner.quiescenceParticipant;return;
   }
   const packages={workspace:'workspaces',notifications:'notifications',diagnostics:'diagnostics',recall:'recall',coordination:'coordination',publishing:'publishing',feedback:'feedback'};
   const config=join(directory,'owner.json');await writeFile(config,JSON.stringify({dataDir:join(directory,'data'),stateDirectory:join(directory,'data'),allowedRoots:[workspace],defaultRoot:workspace}));
   const launcher={command:python,args:['-I','-m','amplifier_unified_'+packages[name]+'.server','--config',config]};
   const options={owner:launcher,inspectSession:forbidden};
   if(name==='feedback'){const resourcePath=process.env.FEEDBACK_RESOURCES_MODULE??(installed?createRequire(join(installed,'package.json')).resolve('@amplifier/unified-resources-capability'):undefined);if(!resourcePath)throw Error('Installed resources required for aggregate feedback service coverage');const {createResourcesCapability}=await import(pathToFileURL(resourcePath).href);options.uploadOwner=createResourcesCapability({directory:join(directory,'uploads'),inspectSession:async session=>({session,workingDirectory:workspace})});}
   const m=await moduleFor(name,['workspace','coordination','publishing'].includes(name)?'dist/index.js':'src/index.js');
   const factory={workspace:'createWorkspaceCapabilities',notifications:'createNotificationsCapability',diagnostics:'createDiagnosticsCapability',recall:'createRecallCapability',coordination:'createCoordinationCapabilities',publishing:'createPublishingCapabilities',feedback:'createFeedbackCapability'}[name];
   owner=m[factory](options);participant=typeof owner.quiescenceParticipant==='function'?owner.quiescenceParticipant(name):owner.quiescenceParticipant;
  };
  try{
   await create();assert.equal(participant.serviceStop?.version,1);
   const held=await participant.acquire(structuredClone(context));assert.ok(held);
   await assert.rejects(participant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}),'Reconciliation cannot manufacture a live partial-acquisition rollback');
   await assert.rejects(held.release('ready',{...proof,kind:'generic'}));
   await owner.close();await create();
   await assert.rejects(participant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}));
   await assert.rejects(participant.reconcileRelease({...context,outcome:'ready',proof:{...proof,observed:{...proof.observed,ownerId:'foreign'}}}));
   await participant.reconcileRelease({...context,outcome:'ready',proof});
   await owner.close();await create();
   await assert.rejects(participant.reconcileRelease({...context,outcome:'ready',proof:{...proof,readyReceiptId:'changed-first-after-reopen'}}));
   const interim={...context,fenceId:'interim',commandId:'interim'};const independent=await participant.acquire(interim);assert.ok(independent,'First changed receipt after reopen must not manufacture held intake');await independent.release('unchanged',{kind:'admission-refused'});
   await participant.reconcileRelease({...context,outcome:'ready',proof});
   await assert.rejects(participant.reconcileRelease({...context,outcome:'ready',proof:{...proof,readyReceiptId:'changed'}}));
   const second={...context,fenceId:'second',commandId:'second'};const lease=await participant.acquire(second);assert.ok(lease,'Changed duplicate proof must not manufacture a new held gate');
   await lease.release('unknown');
   await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}),'Unknown outcomes revoke live rollback');
   await participant.reconcileRelease({...second,outcome:'ready',proof:{...proof,fenceId:'second',commandId:'second'}});
  }finally{await owner?.close();await rm(directory,{recursive:true,force:true});}
 });
}

for(const name of ['workspace','notifications']){
 test(name+' keeps intake held after a post-release RPC failure until exact reconciliation',{skip:(!source&&!installed)||!python,timeout:30000},async()=>{
  const directory=await mkdtemp(join(tmpdir(),'owner-release-uncertain-')),workspace=join(directory,'workspace');await mkdir(workspace);
  const config=join(directory,'config.json');await writeFile(config,JSON.stringify({dataDir:join(directory,'data'),stateDirectory:join(directory,'data'),allowedRoots:[workspace],defaultRoot:workspace}));
  const module='amplifier_unified_'+(name==='workspace'?'workspaces':name);
  const injection=`import importlib; module=importlib.import_module('${module}.server'); original=module.Peer.__init__\ndef install(self,*args,**kwargs):\n original(self,*args,**kwargs)\n release=self.owner.intake.release\n failed=False\n def uncertain(args):\n  nonlocal failed\n  result=release(args)\n  if not failed and result.get('released'):\n   failed=True\n   raise RuntimeError('injected failure after durable release')\n  return result\n self.owner.intake.release=uncertain\nmodule.Peer.__init__=install\nmodule.main()`;
  const launcher={command:python,args:['-I','-c',injection,'--config',config]},m=await moduleFor(name,name==='workspace'?'dist/index.js':'src/index.js');
  const owner=name==='workspace'?m.createWorkspaceCapabilities({owner:launcher,catalog:{}}):m.createNotificationsCapability({owner:launcher});
  const participant=owner.quiescenceParticipant;
  try{
   const lease=await participant.acquire(context);assert.ok(lease);
   await assert.rejects(lease.release('ready',proof),/injected failure after durable release/);
   await assert.rejects(participant.acquire({...context,fenceId:'must-remain-closed',commandId:'must-remain-closed'}),/fence|held/i);
   await participant.reconcileRelease({...context,outcome:'ready',proof});
   const next={...context,fenceId:'next',commandId:'next'},nextLease=await participant.acquire(next);assert.ok(nextLease);await nextLease.release('unchanged',{kind:'admission-refused'});
  }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
 });
}

test('feedback aggregate reconciles a lost nested upload release reply without changing proof',{skip:(!source&&!installed)||!python,timeout:30000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'feedback-service-partial-')),config=join(directory,'config.json');await writeFile(config,JSON.stringify({dataDir:join(directory,'data')}));
 const resourcePath=process.env.FEEDBACK_RESOURCES_MODULE??createRequire(join(installed,'package.json')).resolve('@amplifier/unified-resources-capability');
 const {createResourcesCapability}=await import(pathToFileURL(resourcePath).href),{createFeedbackCapability}=await moduleFor('feedback','src/index.js');let owner,participant;
 const create=async(lose)=>{const uploads=createResourcesCapability({directory:join(directory,'uploads'),inspectSession:forbidden}),factory=uploads.quiescenceParticipant.bind(uploads);uploads.quiescenceParticipant=id=>{const actual=factory(id);return {...actual,acquire:async c=>{const lease=await actual.acquire(c);return lease&&{...lease,release:async(outcome,p)=>{await lease.release(outcome,p);if(lose&&outcome==='ready'){lose=false;throw Error('nested upload release reply lost');}}};}};};owner=createFeedbackCapability({owner:{command:python,args:['-I','-m','amplifier_unified_feedback.server','--config',config]},uploadOwner:uploads,inspectSession:forbidden});participant=owner.quiescenceParticipant('feedback');};
 try{
  await create(true);const lease=await participant.acquire(context);assert.ok(lease);await assert.rejects(lease.release('ready',proof),/nested upload release reply lost/);
  await owner.close();await create(false);
  await assert.rejects(participant.reconcileRelease({...context,outcome:'ready',proof:{...proof,readyReceiptId:'changed-after-loss'}}));
  await participant.reconcileRelease({...context,outcome:'ready',proof});await participant.reconcileRelease({...context,outcome:'ready',proof});
  const next={...context,fenceId:'next',commandId:'next'},nextLease=await participant.acquire(next);assert.ok(nextLease);await nextLease.release('unchanged',{kind:'admission-refused'});
 }finally{await owner?.close();await rm(directory,{recursive:true,force:true})}
});
