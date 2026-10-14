import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,mkdir,chmod,rm,readFile,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Store} from '../dist/store.js';
import {ServiceStore} from '../dist/service-store.js';
import {withOfflineSupervisorSnapshot} from '../dist/index.js';

async function fixture(t){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'offline-supervisor-')));await chmod(directory,0o700);t.after(()=>rm(directory,{recursive:true,force:true}));
 const expected={installationId:'fixture',ownerId:'owner',dataScope:'test',instanceId:'instance',releaseDigest:'a'.repeat(64)};
 const current={identity:{id:'current',version:'1.0.0',revision:'b'.repeat(40),digest:expected.releaseDigest},handle:'fixture'};
 const owner=new Store(join(directory,'owner'),{schema:1,dataScope:'test',current,previous:null,catalog:null,lastCheck:0,lastCheckSucceeded:false,preferences:{autoCheck:false,autoInstall:false,intervalMs:60000}});
 owner.accept({id:'uncertain-check',command:'check',status:'unknown',phase:'accepted',createdAt:1,updatedAt:1,args:{}});
 const service=new ServiceStore(join(directory,'service'),{installationId:'fixture',ownerId:'owner',dataScope:'test'});
 const stopped={commandId:'stop',operation:'stop',status:'stopped',phase:'stopped',expected,fenceId:'fence',target:current,qualifiedOwners:['resources','updates'],exitProof:{ownerId:'owner',instanceId:'instance',observedAt:1,code:0,signal:null},updatedAt:1};
 service.accept(stopped,['fixture']);
 const request={dataDirectory:directory,inventoryDigest:'c'.repeat(64),expected,stoppedCommandId:'stop',participantIds:['resources','updates']};
 let closed=false;const close=()=>{if(!closed){owner.close();service.close();closed=true;}};t.after(close);
 return {directory,request,stopped,service,close};
}
test('offline capture freezes both ledgers, exports standalone SQLite and preserves unknown receipts',async t=>{
 const f=await fixture(t);f.close();let temporary;
 const result=await withOfflineSupervisorSnapshot(f.request,async ({proof,ledgers})=>{
  temporary=ledgers.updates;assert.equal(proof.inventoryDigest,f.request.inventoryDigest);assert.deepEqual(proof.qualifiedOwners,['resources','updates']);
  for(const path of [join(f.directory,'owner/updates.sqlite3'),join(f.directory,'service/service.sqlite3')]){
   const other=new DatabaseSync(path);try{assert.throws(()=>other.exec('BEGIN IMMEDIATE'),/locked/);}finally{other.close();}
  }
  const copy=new DatabaseSync(ledgers.updates,{readOnly:true});try{assert.equal(JSON.parse(copy.prepare("SELECT value FROM operations WHERE id='uncertain-check'").get().value).status,'unknown');}finally{copy.close();}
  return 'captured';
 });
 assert.equal(result,'captured');await assert.rejects(readFile(temporary),/ENOENT/);
 const db=new DatabaseSync(join(f.directory,'owner/updates.sqlite3'));db.exec('BEGIN IMMEDIATE;ROLLBACK');db.close();
});
test('a live supervisor refuses without invoking capture; closed supervisor alone is insufficient',async t=>{
 const f=await fixture(t);let calls=0;const capture=async()=>{calls++;};
 await assert.rejects(withOfflineSupervisorSnapshot(f.request,capture),/offline_supervisor_must_be_closed/);
 f.stopped.resumeCommandId='already-used';f.service.write(f.stopped);f.close();
 await assert.rejects(withOfflineSupervisorSnapshot(f.request,capture),/offline_qualified_stop_required/);assert.equal(calls,0);
});
test('missing participant, legacy stop, and mismatched inventory/service binding refuse',async t=>{
 const f=await fixture(t);delete f.stopped.qualifiedOwners;f.service.write(f.stopped);f.close();
 await assert.rejects(withOfflineSupervisorSnapshot(f.request,async()=>{}),/offline_qualified_stop_required/);
 await assert.rejects(withOfflineSupervisorSnapshot({...f.request,inventoryDigest:'bad'},async()=>{}),/invalid_inventory_digest/);
});
test('capture failure releases all writer locks and does not resume or rewrite commands',async t=>{
 const f=await fixture(t);f.close();
 await assert.rejects(withOfflineSupervisorSnapshot(f.request,async()=>{throw Error('capture-failed');}),/capture-failed/);
 const db=new DatabaseSync(join(f.directory,'service/service.sqlite3'));try{db.exec('BEGIN IMMEDIATE');const stop=JSON.parse(db.prepare("SELECT value FROM commands WHERE id='stop'").get().value);assert.equal(stop.resumeCommandId,undefined);db.exec('ROLLBACK');}finally{db.close();}
});
