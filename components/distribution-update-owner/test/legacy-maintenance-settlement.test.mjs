import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DistributionUpdateOwner,createManualIngressGate,legacyRecoveryDigest as digest,
  createLegacyMaintenanceReleaseVerifier} from '../dist/index.js';
const release=(id,char)=>({id,version:`1.0.${id.at(-1)}`,revision:char.repeat(40),digest:char.repeat(64)});
const old=release('release1','a'),failedTarget=release('release2','b'),replacement=release('release3','c');
const prepared=identity=>({identity,handle:identity.id});
const ids=['portability','capability:attachments','workspaces','native-admin','native-message-metadata',
  'application-updates','capability:voice','capability:connectors','notifications','terminal','diagnostics',
  'capability:observations','capability:coordination','capability:worktrees','capability:publishing',
  'capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];
const raw=(directory,id)=>{const db=new DatabaseSync(join(directory,'updates.sqlite3'),{readOnly:true});
  try{return JSON.parse(db.prepare('SELECT value FROM operations WHERE id=?').get(id).value);}finally{db.close();}};

for(const variant of ['partial-release','lost-final-ack','newer-hold'])test(`retained supervisor settlement with durable22 gate journals: ${variant}`,async t=>{
  const root=await realpath(await mkdtemp(join(tmpdir(),'legacy-settlement-'))),directory=join(root,'supervisor'),history=join(root,'transcript.jsonl');
  await writeFile(history,'saved user intent\n');
  let current={identity:old,instanceId:'original',dataScope:'retained',ready:true},owner,gates=[],releaseResult=null,fail=true,restarts=0,admissions=0;
  const options={directory,dataScope:'retained',initial:prepared(old),preferences:{autoCheck:false,autoInstall:false,intervalMs:60000},
    releases:{check:async()=>({releases:[old,failedTarget],recommendedId:failedTarget.id}),prepare:async r=>prepared(r),verify:async()=>true},
    lifecycle:{inspect:async()=>current,admitRestart:async()=>{admissions++;throw Error('legacy retirement unknown');},restart:async()=>{restarts++;assert.fail('must not restart');}}};
  owner=new DistributionUpdateOwner(options);
  t.after(async()=>{await owner?.close();for(const gate of gates)gate.close();await rm(root,{recursive:true,force:true});});
  owner.check('check');await owner.waitFor('check');owner.prepare('prepare',failedTarget.id);await owner.waitFor('prepare');
  owner.activate('original-update',{preparedCommandId:'prepare',targetDigest:failedTarget.digest,expectedCurrentId:old.id});
  assert.equal((await owner.waitFor('original-update')).phase,'admission_requested');
  const original=raw(directory,'original-update');await owner.close();owner=null;
  const binding={expected:{installationId:'retained-install',ownerId:'retained-supervisor',instanceId:'original',dataScope:'retained',releaseDigest:old.digest},
    original:{commandId:'original-update',fenceId:'legacy-fence',receiptDigest:digest(original)},prepared:replacement,
    configurationDigest:digest('existing configuration'),owners:ids.map(id=>({id,bindingDigest:digest(id)})),
    historyDigest:digest('saved user intent\n'),unknownOutcomesDigest:digest('unknown external effect')};
  const fence={commandId:'original-update',fenceId:'legacy-fence',purpose:'distribution-update',instanceId:'original',dataScope:'retained'};
  // Real Node gate journals exercise exact persisted holds and idempotence. They
  // are stand-ins with the real census names, not22 instantiated product owners.
  for(const [i,id] of ids.entries()){
    let gate=await createManualIngressGate({directory:join(root,`gate-${i}`),id});
    const lease=await gate.participant.acquire(variant==='newer-hold'&&i===0?{...fence,fenceId:'newer-fence'}:fence);
    await lease.release('unknown');gate.close();gate=await createManualIngressGate({directory:join(root,`gate-${i}`),id});gates.push(gate);
  }
  current={identity:replacement,instanceId:'replacement',dataScope:'retained',ready:true};
  const receipt={schema:'legacy-process-maintenance-v1',recoveryId:'maintenance',binding,originalDisposition:'interrupted',originalOutcome:'unknown',phase:'ready',
    witnessDigest:digest('synthetic custody'),nextInstanceId:'replacement',observed:{...binding.expected,instanceId:'replacement',releaseDigest:replacement.digest,identity:replacement,ready:true}};
  const verifier=createLegacyMaintenanceReleaseVerifier({binding,receipt:async()=>receipt,inspectRunning:async()=>current,verifyPrepared:async()=>true});
  const port={binding,receipt:async()=>receipt,prepared:async()=>prepared(replacement),inspectRelease:async()=>releaseResult,
    release:async request=>{
      const proof=await verifier({...fence,owners:ids,outcome:request.outcome,evidence:null});
      for(const [i,gate] of [...gates].reverse().entries()){
        await gate.participant.reconcileRelease({...fence,owners:ids,outcome:'ready',proof});
        if(variant==='partial-release'&&i===4&&fail){fail=false;throw Error('partial owner reply lost');}
      }
      releaseResult={released:true,intakeClosed:false,...request,instanceId:'replacement',receiptId:'maintenance'};
      if(variant==='lost-final-ack'&&fail){fail=false;throw Error('final host reply lost');}
      return releaseResult;
    }};
  owner=new DistributionUpdateOwner({...options,maintenanceRecovery:port});
  await assert.rejects(owner.reconcile('original-update'));
  assert.equal(owner.receipt('original-update').maintenanceRecovery.state,'releasing');
  assert.equal(owner.inspect().current.id,old.id);assert.equal(owner.inspect().restartUnresolved,true);
  if(variant==='newer-hold'){
    await assert.rejects(owner.reconcile('original-update'));assert.equal(gates[0].inspect().held.fenceId,'newer-fence');
  }else{
    const settled=await owner.reconcile('original-update');assert.equal(settled.status,'unknown');assert.equal(settled.phase,'admission_requested');
    assert.equal(settled.target.id,failedTarget.id);assert.equal(settled.maintenanceRecovery.admissionDisposition,'interrupted');
    assert.equal(settled.maintenanceRecovery.state,'settled');assert.equal(settled.maintenanceRecovery.replacement.id,replacement.id);
    assert.equal(owner.inspect().current.id,replacement.id);assert.equal(owner.inspect().previous.id,old.id);assert.equal(owner.inspect().restartUnresolved,false);
    assert.equal(owner.inspect().actionReadiness.state,'available');assert.ok(gates.every(g=>g.inspect().held===null));
    await owner.close();owner=new DistributionUpdateOwner({...options,maintenanceRecovery:port});
    assert.deepEqual(await owner.reconcile('original-update'),settled);assert.equal(owner.inspect().restartUnresolved,false);
  }
  const {maintenanceRecovery,...retained}=raw(directory,'original-update');assert.deepEqual(retained,original);
  assert.equal(await readFile(history,'utf8'),'saved user intent\n');assert.equal(admissions,1);assert.equal(restarts,0);
});
