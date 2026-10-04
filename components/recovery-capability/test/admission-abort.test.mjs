import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const module=process.env.RECOVERY_PACKAGE_MODULE?pathToFileURL(process.env.RECOVERY_PACKAGE_MODULE).href:new URL('../dist/index.js',import.meta.url).href;
const {createRecoveryCapabilities}=await import(module);
const context={fenceId:'original-fence',commandId:'original-command',purpose:'distribution-update',instanceId:'original-instance',dataScope:'owned-data'};
const proof={...context,kind:'distribution-admission-abort',verified:true,receiptId:'authenticated-abort'};
const next={...context,fenceId:'later-fence',commandId:'later-command'};
const forbidden=()=>assert.fail('Admission abort must never invoke native effects or replay');
function fixture(t,extra={}){
 const directory=mkdtempSync(join(tmpdir(),'recovery-admission-'));let owner;
 const open=()=>owner=createRecoveryCapabilities({directory,nativeAuthority:'fixture',authorize:async()=>({accountId:'fixture'}),nativeAdmin:forbidden,resolveSession:forbidden,quiescence:{},...extra});
 const db=fn=>{const conn=new DatabaseSync(join(directory,'recovery.sqlite'));try{return fn(conn);}finally{conn.close();}};
 const record=()=>db(conn=>JSON.parse(conn.prepare('SELECT payload FROM participant_releases WHERE fence_id=?').get(context.fenceId)?.payload??'null'));
 const fence=()=>db(conn=>JSON.parse(conn.prepare('SELECT payload FROM fence WHERE id=1').get()?.payload??'null'));
 open();t.after(async()=>{await owner?.close();rmSync(directory,{recursive:true,force:true});});
 return {directory,get owner(){return owner;},db,record,fence,reopen:async()=>{await owner.close();return open();},abort:input=>owner.quiescenceParticipant.abortAdmission(input??{...context,proof})};
}
test('original acquisition and exact abort survive a lost reply and owner restart',async t=>{
 const f=fixture(t);assert.ok(await f.owner.quiescenceParticipant.acquire(context));assert.equal(f.record().acquisition,true);
 await f.reopen();const receipt=await f.abort();assert.deepEqual(Object.keys(receipt).sort(),['commandId','dataScope','fenceId','instanceId','ownerId','receiptId','status']);assert.equal(receipt.ownerId,'recovery');assert.equal(receipt.status,'released');assert.equal(f.fence(),null);
 await f.reopen();assert.deepEqual(await f.abort(),receipt);await assert.rejects(f.owner.quiescenceParticipant.acquire(context),/cannot be acquired again/);
 await assert.rejects(f.owner.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}),/distinct retained proof/);
});
test('completed exact retry preserves a later fence and its live rollback authority',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);const receipt=await f.abort();const later=await f.owner.quiescenceParticipant.acquire(next);
 assert.deepEqual(await f.abort(),receipt);assert.deepEqual(f.fence(),next);await later.release('unchanged',{kind:'admission-refused'});assert.equal(f.fence(),null);
});
test('durable busy refusal stays refused after work settles and can prove not-acquired',async t=>{
 let unblock;const pending=new Promise(resolve=>{unblock=resolve;});const f=fixture(t,{authorize:async()=>{await pending;return {accountId:'fixture'};}});
 const call=f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.list',args:{}},{clientId:'fixture'});
 assert.equal(await f.owner.quiescenceParticipant.acquire(context),null);assert.equal(f.record().acquisition,false);await assert.rejects(f.abort(),/pending or uncertain/);
 unblock();await call;assert.equal(await f.owner.quiescenceParticipant.acquire(context),null);assert.equal((await f.abort()).status,'not-acquired');
});
test('unknown native jobs prevent abort without clearing the hold or writing a receipt',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);
 f.db(db=>db.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)').run('unknown','fixture','unknown','unknown',1,0,JSON.stringify({id:'unknown',state:'unknown'})));
 await assert.rejects(f.abort(),/pending or uncertain/);assert.deepEqual(f.fence(),context);assert.equal(f.record().abort,undefined);
});
for(const change of [{kind:'service-lifecycle'},{verified:false},{purpose:'recovery'},{fenceId:'other'},{commandId:'other'},{instanceId:'other'},{dataScope:'other'},{receiptId:''},{outcome:'unchanged'}])test('different or generic abort proof refuses: '+JSON.stringify(change),async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);await assert.rejects(f.abort({...context,proof:{...proof,...change}}),/abort/);assert.deepEqual(f.fence(),context);assert.equal(f.record().abort,undefined);
});
test('changed authenticated proof refuses after completion',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);await f.abort();const before=f.record();await assert.rejects(f.abort({...context,proof:{...proof,receiptId:'different-authenticated-proof'}}),/differs/);assert.deepEqual(f.record(),before);
});
test('changed binding refuses even with a self-consistent proof',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);const changed={...context,commandId:'different-command'};await assert.rejects(f.abort({...changed,proof:{...proof,...changed}}),/original admission/);assert.deepEqual(f.fence(),context);
});
for(const held of [false,true])test('legacy absence never proves acquisition or refusal; held='+held,async t=>{
 const f=fixture(t);if(held)f.db(db=>db.prepare('INSERT INTO fence VALUES(1,?)').run(JSON.stringify(context)));
 await assert.rejects(f.abort(),/No exact original/);assert.deepEqual(f.fence(),held?context:null);assert.equal(f.record(),null);
});
test('old release receipts are preserved without manufacturing abort authority',async t=>{
 const f=fixture(t),legacy={context,evidence:null,releasedAt:1};f.db(db=>db.prepare('INSERT INTO participant_releases VALUES(?,?,?,?)').run(context.fenceId,context.commandId,'legacy',JSON.stringify(legacy)));
 await assert.rejects(f.abort(),/No exact original/);assert.deepEqual(f.record(),legacy);
});
test('missing held fence cannot be inferred settled from current absence',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);f.db(db=>db.exec('DELETE FROM fence'));await assert.rejects(f.abort(),/no pre-effect settlement/);assert.equal(f.record().abort,undefined);
});
test('known pre-effect rollback can be acknowledged by distinct abort after restart',async t=>{
 const f=fixture(t),lease=await f.owner.quiescenceParticipant.acquire(context);await lease.release('unchanged',{kind:'admission-refused'});await f.reopen();assert.equal((await f.abort()).status,'released');
});
test('normal ready release cannot be relabeled as a pre-retirement abort',async t=>{
 const f=fixture(t),lease=await f.owner.quiescenceParticipant.acquire(context);await lease.release('ready',{...context,instanceId:'replacement-instance',verified:true,outcome:'ready',receiptId:'ready-proof'});await assert.rejects(f.abort(),/no pre-effect settlement/);
});
test('unknown lease cannot use the live rollback route',async t=>{
 const f=fixture(t),lease=await f.owner.quiescenceParticipant.acquire(context);await lease.release('unknown');await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}),/newly acquired live lease/);assert.equal((await f.abort()).status,'released');
});
test('receipt commit and hold removal roll back atomically when hold deletion fails',async t=>{
 const f=fixture(t);await f.owner.quiescenceParticipant.acquire(context);const before=f.record();f.db(db=>db.exec("CREATE TRIGGER fail_abort BEFORE DELETE ON fence BEGIN SELECT RAISE(ABORT,'owned injection'); END"));
 await assert.rejects(f.abort(),/owned injection/);assert.deepEqual(f.record(),before);assert.deepEqual(f.fence(),context);f.db(db=>db.exec('DROP TRIGGER fail_abort'));assert.equal((await f.abort()).status,'released');
});
test('crash-left acquisition WAL retains abort authority without replay',async t=>{
 const f=fixture(t);await f.owner.close();const code=`import {createRecoveryCapabilities} from ${JSON.stringify(module)};const o=createRecoveryCapabilities({directory:${JSON.stringify(f.directory)},nativeAuthority:'fixture',authorize:async()=>({accountId:'fixture'}),nativeAdmin:()=>{throw Error('No effects')},resolveSession:()=>{},quiescence:{}});await o.quiescenceParticipant.acquire(${JSON.stringify(context)});process.kill(process.pid,'SIGKILL');`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',code],{encoding:'utf8'});assert.equal(result.signal,'SIGKILL',result.stderr);await f.reopen();assert.equal((await f.abort()).status,'released');
});
