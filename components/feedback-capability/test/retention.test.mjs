import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';
const {createFeedbackCapability}=await import(process.env.RETENTION_NODE_MODULE??'../src/index.js');
const python=process.env.RETENTION_PYTHON;
test('installed owner holds selected retention references and invalidates old lease without replay',{skip:!python},async()=>{
 const root=await mkdtemp(join(tmpdir(),'retention-feedback-')),path=join(root,'config.json');await writeFile(path,JSON.stringify({dataDir:join(root,'owner')}));
 const uploads=(await import(process.env.RETENTION_RESOURCES_MODULE)).createResourcesCapability({directory:join(root,'uploads'),inspectSession:async session=>({session,workingDirectory:root})});
 const launcher={command:python,args:['-I','-m','amplifier_unified_feedback.server','--config',path]},owner=createFeedbackCapability({owner:launcher,uploadOwner:uploads}),p=owner.quiescenceParticipant('configured:feedback');
 const context={fenceId:'retention',commandId:'reviewed-hide',purpose:'retention-hide',instanceId:'owned-instance',dataScope:'owned-state'};
 try{
  assert.equal(p.retentionHide.version,1);const lease=await p.acquire(context);assert.ok(lease);assert.equal(lease.ownerId,p.id);
  const sessions=Array.from({length:101},(_,i)=>'ahp-session:/selected-'+i);
  assert.deepEqual(await lease.inspectRetentionReferences({sessions,limit:101}),{coverage:'complete',protected:[],omissions:[]});
  await assert.rejects(lease.inspectRetentionReferences({sessions:[...sessions,'ahp-session:/overflow']}));
  await lease.release('unknown');await assert.rejects(lease.inspectRetentionReferences({sessions}));
  await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}));
  const proof={verified:true,...context,outcome:'unchanged',receiptId:'host-exact-child-receipt'};
  await p.reconcileRelease({...context,outcome:'unchanged',proof});
  await p.reconcileRelease({...context,outcome:'unchanged',proof});
  await assert.rejects(lease.inspectRetentionReferences({sessions}));
 await uploads.action({version:1,topic:'attachments',channel:'ahp-session:/feedback-uploads',operation:'attachments.create',commandId:'upload',args:{requestId:'upload',name:'private.txt',contentType:'text/plain',size:3,sha256:'a'.repeat(64)}});
 const second={...context,fenceId:'second',commandId:'second'},held=await p.acquire(second);const pending=await held.inspectRetentionReferences({sessions});assert.equal(pending.coverage,'partial');assert.deepEqual(pending.omissions,[{reason:'feedback-upload-unattributed',scope:'owner'}]);await held.release('unchanged',{verified:true,...second,outcome:'unchanged',receiptId:'second-proof'});
 }finally{await owner.close();await rm(root,{recursive:true,force:true})}
});
