import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createOperationsCapabilities} from '@amplifier/unified-operations-capabilities';
import {bindHeldOwnerSnapshots} from '../src/owner-snapshots.js';

const python=process.env.OWNER_SNAPSHOT_PYTHON;
const context={fenceId:'fixture-fence',commandId:'fixture-recovery',purpose:'recovery',instanceId:'fixture-instance',dataScope:'owned-é'};
const hash=raw=>createHash('sha256').update(raw).digest('hex');
async function fixture(t,{loseReply=false,badPage=false}={}){
 const root=await realpath(await mkdtemp(join(tmpdir(),'owner-snapshot-stage-'))),config=join(root,'owner.json'),directory=join(root,'staged');
 await mkdir(directory);await writeFile(config,JSON.stringify({dataDir:join(root,'operations')}));
 const forbidden=async()=>{throw Error('No fixture external work permitted');};
 const owner=createOperationsCapabilities({owner:{command:python,args:['-I','-B','-m','amplifier_unified_operations.server','--config',config],env:{PYTHONDONTWRITEBYTECODE:'1'}},inspectSession:forbidden,readSessionContext:forbidden,nativeControl:forbidden,nativeControlExisting:forbidden,createSession:forbidden,submitScheduled:forbidden,waitForTurn:forbidden});
 const participant=owner.quiescenceParticipant('capability:operations'),acquire=participant.acquire;let calls=0;
 participant.acquire=async ctx=>{const lease=await acquire(ctx),capture=lease?.captureSnapshot;if(capture)lease.captureSnapshot=async args=>{calls++;const receipt=await capture(args);if(loseReply)throw Error('Fixture lost original capture reply');return receipt;};return lease;};
 const provider={inspectOwnerSnapshot:owner.inspectOwnerSnapshot,readOwnerSnapshot:async args=>{const row=await owner.readOwnerSnapshot(args);return badPage?{...row,sha256:'0'.repeat(64)}:row;}};
 const adapter=bindHeldOwnerSnapshots({provider,participant,withMaintenance:async(input,body)=>{assert.equal(input.fenceId,context.fenceId);assert.equal(input.commandId,context.commandId);return body();}});
 t.after(async()=>{adapter.close();await owner.close();console.error('Retained owner snapshot fixture:',root);});
 return {root,owner,adapter,directory,calls:()=>calls,input:{fenceId:context.fenceId,commandId:context.commandId,snapshotCommandId:'snapshot-original',directory,privateContentReviewed:true}};
}

test('actual installed Operations fresh held lease stages exact bounded seven-store images',{skip:!python},async t=>{
 const f=await fixture(t);
 await assert.rejects(f.adapter.stage(f.input),/fresh_held_lease_required/);
 const lease=await f.adapter.participant.acquire(context);assert.ok(lease);
 const row=await f.adapter.stage(f.input);assert.equal(row.status,'sealed');assert.equal(row.completeProductBackup,false);assert.equal(row.workReplayed,false);assert.equal(f.calls(),1);
 const image=join(f.directory,hash(f.input.snapshotCommandId),'images');
 for(const entry of row.ownerReceipt.snapshot.stores){if(entry.status==='captured')assert.equal(hash(await readFile(join(image,entry.file))),entry.sha256);}
 assert.equal(hash(await readFile(join(image,'manifest.json'))),row.manifestSha256);
 assert.deepEqual(await f.adapter.stage(f.input),{...row,replayed:false});assert.equal(f.calls(),1);
 await lease.release('unknown');
 await assert.rejects(f.adapter.stage({...f.input,snapshotCommandId:'after-release'}),/fresh_held_lease_required/);
 assert.equal((await f.adapter.inspect('snapshot-original')).receipt.status,'captured');
});

test('lost capture acknowledgment keeps root intent unknown and never redispatches the original capture',{skip:!python},async t=>{
 const f=await fixture(t,{loseReply:true});await f.adapter.participant.acquire(context);
 await assert.rejects(f.adapter.stage(f.input),/lost original capture reply/);
 const row=await f.adapter.stage(f.input);assert.equal(row.status,'unknown');assert.equal(f.calls(),1);
 assert.equal((await f.adapter.inspect('snapshot-original')).receipt.status,'captured');assert.equal(f.calls(),1);
});

test('changed export evidence refuses sealing and retains original owner receipt',{skip:!python},async t=>{
 const f=await fixture(t,{badPage:true});await f.adapter.participant.acquire(context);
 await assert.rejects(f.adapter.stage(f.input),/chunk_invalid/);
 assert.equal((await f.adapter.stage(f.input)).status,'unknown');assert.equal(f.calls(),1);
 assert.equal((await f.adapter.inspect('snapshot-original')).receipt.status,'captured');
});
