const allocation={allocationId:'12345678-1234-1234-1234-123456789abc',executionDirectory:'/owned/managed/files',allocationHash:'a'.repeat(64),treeHash:'b'.repeat(64),entryCount:1,bytes:5};
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';
const {createCoordinationCapabilities}=await import(process.env.RETENTION_NODE_MODULE??'../dist/index.js');
const python=process.env.RETENTION_PYTHON;
test('installed owner holds selected managed-file references and invalidates old lease without replay',{skip:!python},async()=>{
 const root=await mkdtemp(join(tmpdir(),'retention-coordination-')),path=join(root,'config.json');await writeFile(path,JSON.stringify({dataDir:join(root,'owner')}));
 const launcher={command:python,args:['-I','-m','amplifier_unified_coordination.server','--config',path]},owner=createCoordinationCapabilities({owner:launcher}),p=owner.quiescenceParticipant('configured:coordination');
 const context={fenceId:'retention',commandId:'reviewed-hide',purpose:'managed-files-disposal',instanceId:'owned-instance',dataScope:'owned-state'};
 try{
  assert.equal(p.managedFiles.version,1);const lease=await p.acquire(context);assert.ok(lease);assert.equal(lease.ownerId,p.id);
  const sessions=Array.from({length:101},(_,i)=>'ahp-session:/selected-'+i);
  assert.deepEqual(await lease.inspectManagedFilesReferences({allocation,sessions,limit:101}),{coverage:'complete',protected:[],omissions:[]});
  await assert.rejects(lease.inspectManagedFilesReferences({allocation,sessions:[...sessions,'ahp-session:/overflow']}));
  await assert.rejects(lease.inspectManagedFilesReferences({allocation:{...allocation,treeHash:'c'.repeat(64)},sessions:['ahp-session:/one']}));await lease.release('unknown');await assert.rejects(lease.inspectManagedFilesReferences({allocation,sessions}));
  await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}));
  const proof={verified:true,...context,outcome:'unchanged',receiptId:'host-exact-child-receipt'};
  await p.reconcileRelease({...context,outcome:'unchanged',proof});
  await p.reconcileRelease({...context,outcome:'unchanged',proof});
  await assert.rejects(lease.inspectManagedFilesReferences({allocation,sessions}));
 }finally{await owner.close();await rm(root,{recursive:true,force:true})}
});
