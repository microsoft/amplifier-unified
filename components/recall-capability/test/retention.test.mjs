import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';
const {createRecallCapability}=await import(process.env.RETENTION_NODE_MODULE??'../src/index.js');
const python=process.env.RETENTION_PYTHON;
test('installed owner holds selected retention references and invalidates old lease without replay',{skip:!python},async()=>{
 const root=await mkdtemp(join(tmpdir(),'retention-recall-')),path=join(root,'config.json');await writeFile(path,JSON.stringify({dataDir:join(root,'owner')}));
 const launcher={command:python,args:['-I','-m','amplifier_unified_recall.server','--config',path]},owner=createRecallCapability({owner:launcher}),p=owner.quiescenceParticipant('configured:recall');
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
 }finally{await owner.close();await rm(root,{recursive:true,force:true})}
});
