import { createInterface } from 'node:readline';
import { connect } from 'node:net';
import { join, resolve } from 'node:path';
import { mkdir, readFile, open, unlink } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { LinuxUnitLifecycle } from '../../src/linux-unit-lifecycle.ts';
import { ServiceLifecycleOwner } from '../../src/service-owner.ts';

const [directory, unit] = process.argv.slice(2), root = resolve(directory);
const runtime = fileURLToPath(new URL('./runtime.py', import.meta.url));
const binding = { installationId: 'spike-installation', ownerId: 'spike-supervisor', dataScope: 'spike-data' };
const runtimeDigest = createHash('sha256').update(await readFile(runtime)).digest('hex');
const targets = Object.fromEntries(['a','b'].map((name,i) => [name, {handle:name,identity:{
  id:'fixture-'+name,version:'1.0.'+i,revision:name.repeat(40),digest:name.repeat(64),
}}]));
const delay = ms => new Promise(r=>setTimeout(r,ms));
async function request(value) {
  return new Promise((resolve,reject)=>{
    const s=connect(join(root,'runtime.sock'));let text='';
    s.on('error',reject);s.on('connect',()=>s.write(JSON.stringify(value)+'\n'));
    s.on('data',chunk=>{text+=chunk;if(text.includes('\n')){s.end();resolve(JSON.parse(text));}});
    s.setTimeout(2000,()=>s.destroy(Error('fixture_transport_unconfirmed')));
  });
}
async function inspect() {
  let v;try { v=await request({op:'inspect'}); } catch { return null; }
  const target=Object.values(targets).find(t=>t.identity.digest===v.releaseDigest);
  if(!target || v.domainLockHeld!==true)return null;
  return {...v,identity:target.identity};
}
await mkdir(root,{recursive:true,mode:0o700});
const lifecycle = new LinuxUnitLifecycle({
  ...binding,unit,unitDirectory:join(root,'units'),inspect,observationMs:10000,
  resolve: async target=>{
    if(targets[target.handle]?.identity.digest!==target.identity.digest ||
      createHash('sha256').update(await readFile(runtime)).digest('hex')!==runtimeDigest)
      throw Error('fixture_artifact_changed');
    // Different role interpreter from the Node controller, deliberately.
    return {command:'/usr/bin/python3',args:[runtime,root],cwd:root,env:{PATH:'/usr/bin:/bin'}};
  },
  initialProvisioning:{claim:async r=>{
    const file=await open(join(root,'initial-authority'),'wx',0o600);await file.close();
    // Synthetic Host gate only; the separate signed-Host fixture proves the real constructor.
    externalFence={commandId:r.commandId,fenceId:r.commandId,purpose:'initial-start',phase:'closed',
      instanceId:r.instanceId,dataScope:r.dataScope,serviceIdentity:{...binding,instanceId:r.instanceId,releaseDigest:r.target.identity.digest}};
    return {kind:'pristine-installation',installationId:binding.installationId,
      commandId:r.commandId,instanceId:r.instanceId,dataScope:r.dataScope,targetDigest:r.target.identity.digest};
  }},
});
let service, externalFence=null;
async function admission(command, interrupted) {
  const fence={commandId:command.commandId,fenceId:command.commandId,purpose:'service-stop',
    instanceId:command.expected.instanceId,dataScope:command.expected.dataScope,
    serviceIdentity:command.expected,phase:interrupted?'closed':'held'};
  // Synthetic shared admission lives in the external controller. Its existing
  // service command ledger excludes configured launchers even with a dead app.
  if(interrupted)externalFence=fence;
  try { await request({op:'close',fence}); }
  catch(error) { if(!interrupted)throw error; }
  let active;
  do {
    try {active=await request({op:'inspect'});}
    catch(error) {
      if(!interrupted)throw error;
      active={activeWork:'unknown',instanceId:command.expected.instanceId,dataScope:command.expected.dataScope};
    }
    if(!interrupted && active.activeWork) await delay(25);
  }
  while(!interrupted && active.activeWork);
  // One shared gate, no per-capability owner census or release loop.
  return {admitted:true,commandId:command.commandId,fenceId:fence.fenceId,purpose:'service-stop',
    expected:command.expected,evidence:{activeWork:active.activeWork,intakeClosed:true,
      instanceId:active.instanceId,dataScope:active.dataScope}};
}
const host={
  admitServiceStop: c=>admission(c,false), closeServiceIntake:c=>admission(c,true),
  inspectServiceLifecycle:async()=>{
    // This fixture's admission gate lives outside the intentionally killed
    // runtime. Inspecting its retained fence must not require that app to reply.
    if(externalFence)return {intakeClosed:true,fence:externalFence};
    const actual=await request({op:'inspect'});
    return {intakeClosed:actual.intakeClosed,fence:actual.fence};
  },
  releaseServiceStart:async r=>{
    const start=service.proof(r.commandId),actual=await lifecycle.inspectOwned();
    if(start?.status!=='ready'||start.expected.instanceId!==actual.instanceId)throw Error('fixture_initial_unconfirmed');
    await request({op:'open',instanceId:actual.instanceId});externalFence=null;
    return {...r,released:true,intakeClosed:false,purpose:'initial-start',expected:start.expected,observed:start.expected};
  },
  serviceStopReceipt:async id=>service.receipt(id),
  releaseServiceStop:async r=>{
    const resumed=service.proof(r.resumeCommandId), stopped=service.proof(r.commandId);
    const actual=await lifecycle.inspectOwned();
    if(resumed?.status!=='ready'||resumed.observed.instanceId!==actual.instanceId||
      resumed.observed.releaseDigest!==actual.identity.digest||stopped?.status!=='stopped')
      throw Error('fixture_release_unconfirmed');
    await request({op:'open',instanceId:actual.instanceId});
    externalFence=null;
    let dropAck=false;
    try { await unlink(join(root,'drop-release-ack'));dropAck=true; }
    catch(error) { if(error.code!=='ENOENT')throw error; }
    if(dropAck)throw Error('fixture_release_ack_lost');
    return {...r,released:true,intakeClosed:false,purpose:'service-stop',
      expected:stopped.expected,observed:resumed.observed};
  },
};
service=new ServiceLifecycleOwner({directory:join(root,'controller'),...binding,host,lifecycle,
  releases:{verify:async t=>targets[t.handle]?.identity.digest===t.identity.digest,
    // Synthetic only: real signed source qualification is exercised separately.
    qualifyActivation:async t=>{if(targets[t.handle]?.identity.digest!==t.identity.digest)throw Error('fixture_artifact_changed');}},
  currentRelease:()=>{
    // Source fixture inventory only; real signed artifact verification is a
    // separate production port and is not claimed by these synthetic releases.
    return current;
  },
  authorizeInterruption:(_c,id)=>id==='fixture-explicit-interruption',
  onChange:r=>process.stdout.write(JSON.stringify({event:'receipt',receipt:r})+'\n'),
});
let current=targets.a;
const lines=createInterface({input:process.stdin});
process.stdout.write(JSON.stringify({event:'controller-ready'})+'\n');
for await(const line of lines){
  const {id,action,args={}}=JSON.parse(line);
  // Do not serialize passive/callback commands behind an outstanding drain.
  void (async()=>{
    let result;
    if(action==='initial'){
      service.startInitial({target:targets.a,commandId:'initial'});
      const started=await service.waitFor('initial');
      if(started.status!=='ready'||started.admissionSettlement?.state!=='settled')throw Error('fixture_initial_unconfirmed');
      result=await lifecycle.inspectOwned();
    }else if(action==='runtime'){result=await request(args);}
    else if(action==='stop'){
      const prior=args.fromResumeCommandId?service.proof(args.fromResumeCommandId)?.observed:null;
      const actual=prior?null:await lifecycle.inspectOwned();
      const expected=prior??{...binding,instanceId:actual.instanceId,releaseDigest:actual.identity.digest};
      current=Object.values(targets).find(t=>t.identity.digest===expected.releaseDigest);
      if(args.wrongGeneration)expected.instanceId='wrong-generation';
      result=service.stop({commandId:args.commandId,expected},
        args.interrupt?{authorizationId:args.authorizationId}:undefined);
    }else if(action==='resume'){
      const stopped=service.receipt(args.stoppedCommandId);
      result=service.resume({commandId:args.commandId,stoppedCommandId:args.stoppedCommandId,
        expected:stopped.expected,target:targets[args.target??'b']});
    }else if(action==='inspect'){result=await service.inspect();}
    else if(action==='receipt'){result=service.receipt(args.commandId);}
    else if(action==='reconcile'){result=await service.reconcile(args.commandId);}
    else if(action==='try-occupied-launch'){
      await lifecycle.resumeOwned({target:targets.b,instanceId:'forbidden-second',previousInstanceId:'initial-a',
        dataScope:binding.dataScope,commandId:'negative-test',signal:new AbortController().signal});result='unexpected';
    }else if(action==='try-wrong-invocation'){
      const expected=service.proof(args.fromResumeCommandId).observed;
      await lifecycle.verifyCustody(expected,{kind:'linux-unit',unit,invocationId:'0'.repeat(32),
        instanceId:expected.instanceId,dataScope:expected.dataScope,releaseDigest:expected.releaseDigest});
      result='unexpected';
    }else if(action==='close'){await service.close();result=true;}
    else throw Error('unknown_fixture_action');
    process.stdout.write(JSON.stringify({id,result})+'\n');
  })().catch(error=>process.stdout.write(JSON.stringify({id,error:String(error.message)})+'\n'));
}
