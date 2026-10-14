import {consumeFailedBootstrapRecovery,type FailedBootstrapRecoveryOptions} from './manual-bootstrap-recovery.js';
import {createServer, createConnection, type Socket} from 'node:net';
import {chmod, lstat} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {isDeepStrictEqual as equal} from 'node:util';
import {createAuthority, authorityKey, readAuthority, writeAuthority, seal, unseal} from './manual-authority.js';
import {inspectExistingStateBindings, type ExistingStateBinding} from './existing-state.js';
import {serviceIdentity, sameService, type ServiceIdentity, type ServiceHostPort} from './service-types.js';
import {identity, prepared, token, same, type PreparedRelease} from './types.js';
import type {ExistingStateHandoffClaim, ExistingStateHandoffProof, ExistingStateHandoffSource} from './existing-state-types.js';
import {witnessDigest, type SystemdSourceObserver, type SystemdSourceWitness} from './systemd-witness.js';

function census(ids:string[]){if(!Array.isArray(ids)||!ids.length||ids.length>128||new Set(ids).size!==ids.length)throw Error('manual_owner_census_invalid');return ids.map(x=>token(x)).sort();}
function claimValue(v:ExistingStateHandoffClaim):ExistingStateHandoffClaim {
  const expected=serviceIdentity(v.expected),next=serviceIdentity(v.next),target=identity(v.target);
  if(expected.instanceId===next.instanceId||!sameService({...next,instanceId:expected.instanceId},expected)||
    target.digest!==expected.releaseDigest||!/^[a-f0-9]{64}$/.test(v.dataBindingDigest)||v.commandId===v.stoppedCommandId)throw Error('manual_claim_invalid');
  return {commandId:token(v.commandId),stoppedCommandId:token(v.stoppedCommandId),expected,next,target,
    dataBindingDigest:v.dataBindingDigest,participantIds:census(v.participantIds)};
}
type SourceState={schema:'manual-systemd-source-v1';phase:'starting'|'ready'|'admission_requested'|'refused'|'retired'|'stopping'|'closed';
  expected:ServiceIdentity;target:PreparedRelease;witness:SystemdSourceWitness;dataBindingDigest:string;owners:string[];
  claim?:ExistingStateHandoffClaim;fenceId?:string;recovery?:{predecessor:string;permitDigest:string};updatedAt:number};

/** Installed by a trusted launcher BEFORE creating the app or opening listeners.
 * The authority directory is a one-shot launch guard. It must never be deleted
 * to retry an uncertain handoff or reused to start the retired source again. */
export async function createManualSystemdHandoffLauncher(options:{
  directory:string; expected:ServiceIdentity; bindings:ExistingStateBinding[];
  observer:SystemdSourceObserver;
  /** Must independently qualify the actual installed bytes, never a config label. */
  qualifyCurrent():Promise<PreparedRelease>;
  /** Explicit reviewed predecessor recovery; never an implicit restart. */
  recovery?:FailedBootstrapRecoveryOptions;
}) {
  const expected=serviceIdentity(options.expected),bindings=structuredClone(options.bindings);
  const [target,witness,dataBindingDigest]=await Promise.all([
    options.qualifyCurrent().then(prepared),options.observer.capture(),inspectExistingStateBindings(bindings)]);
  if(witness.pid!==process.pid||(process.env.INVOCATION_ID&&witness.invocationId!==process.env.INVOCATION_ID)||target.identity.digest!==expected.releaseDigest)
    throw Error('manual_source_identity_unconfirmed');
  let state:SourceState={schema:'manual-systemd-source-v1',phase:'starting',expected,target,witness,dataBindingDigest,owners:[],updatedAt:Date.now()};
  if(options.recovery)state.recovery=await consumeFailedBootstrapRecovery(options.recovery,{directory:options.directory,expected,witness,bindingDigest:dataBindingDigest});
  const key=await createAuthority(options.directory,state);
  const save=async()=>{state.updatedAt=Date.now();await writeAuthority(options.directory,key,state);};
  let attached=false;
  return {
    // Completed source authority is never permitted to release its held fence.
    // The destination's normal service verifier settles the imported receipt.
    serviceLifecycle:{identity:expected,verifyRelease:async()=>{throw Error('manual_source_release_not_supported');}},
    async attach(app:{host:ServiceHostPort;requiredOwners:string[];expectedOwners:string[];close():Promise<void>;exit():void}) {
      if(attached)throw Error('manual_source_already_attached');attached=true;
      const owners=census(app.requiredOwners);if(!equal(owners,census(app.expectedOwners)))throw Error('manual_owner_census_mismatch');
      state.owners=owners;state.phase='ready';await save();
      const sockets=new Set<Socket>();let chain:Promise<unknown>=Promise.resolve();
      async function exact(claim:ExistingStateHandoffClaim){
        if(!sameService(claim.expected,expected)||!same(claim.target,target.identity)||!equal(claim.participantIds,owners)||claim.dataBindingDigest!==dataBindingDigest||
          await inspectExistingStateBindings(bindings)!==dataBindingDigest||!same((await options.qualifyCurrent()).identity,target.identity))throw Error('manual_binding_mismatch');
      }
      async function held(claim:ExistingStateHandoffClaim,fenceId:string){
        const status:any=await app.host.inspectServiceLifecycle(),f=status?.fence;
        if(!f||f.phase!=='held'||f.purpose!=='service-stop'||f.commandId!==claim.stoppedCommandId||f.fenceId!==fenceId||
          f.instanceId!==expected.instanceId||f.dataScope!==expected.dataScope||!sameService(f.serviceIdentity,expected)||!equal(census(f.owners),owners))
          throw Error('manual_held_census_unconfirmed');
      }
      async function handle(req:any){
        if(req.operation==='inspect')return state;
        const claim=claimValue(req.claim);await exact(claim);
        if(req.witnessDigest!==witnessDigest(witness)||witnessDigest(await options.observer.capture())!==witnessDigest(witness))throw Error('manual_process_changed');
        if(req.operation==='retire'){
          if(state.phase==='retired'&&equal(state.claim,claim))return state;
          if(!['ready','refused'].includes(state.phase))throw Error('manual_source_consumed_or_unknown');
          state.claim=claim;state.phase='admission_requested';await save();
          const admitted:any=await app.host.admitServiceStop({commandId:claim.stoppedCommandId,expected});
          if(admitted?.admitted===false&&admitted.executed===false&&admitted.intakeClosed===false&&admitted.purpose==='service-stop'&&sameService(admitted.expected,expected)){
            state.phase='refused';await save();throw Error('manual_source_busy');
          }
          const e=admitted?.evidence;
          if(admitted?.admitted!==true||admitted.commandId!==claim.stoppedCommandId||admitted.purpose!=='service-stop'||!sameService(admitted.expected,expected)||
            e?.activeWork!==0||e?.intakeClosed!==true||e?.instanceId!==expected.instanceId||e?.dataScope!==expected.dataScope)throw Error('manual_admission_unconfirmed');
          state.fenceId=token(admitted.fenceId);await held(claim,state.fenceId);
          // Durable retirement precedes permission to close ANY app/access owner.
          state.phase='retired';await save();return state;
        }
        if(req.operation==='stop'){
          if(state.phase!=='retired'||!equal(state.claim,claim)||!state.fenceId)throw Error('manual_source_not_retired');
          await held(claim,state.fenceId);state.phase='stopping';await save();return state;
        }
        throw Error('manual_operation_invalid');
      }
      async function closeOwned(){
        // No shutdown retry on uncertainty. The saved stopping state is not an
        // exit proof. Only successful owner closure plus a kernel exit qualifies.
        try{await app.close();await new Promise<void>(resolve=>{server.close(()=>resolve());for(const s of sockets)s.destroy();});
          state.phase='closed';await save();app.exit();
        }catch{/* Durable stopping remains unknown; never forge closure. */}
      }
      const server=createServer(socket=>{
        sockets.add(socket);socket.on('close',()=>sockets.delete(socket));socket.on('error',()=>{});
        let text='',used=false;socket.on('data',chunk=>{
          if(used)return; text+=chunk.toString('utf8');if(Buffer.byteLength(text)>65536){used=true;socket.destroy();return;}
          if(!text.includes('\n'))return;used=true;
          const task=chain.then(async()=>{
            let req:any;
            try{req=unseal(JSON.parse(text.trim()),key);if(!/^[a-f0-9-]{36}$/.test(req.challenge))throw Error('manual_challenge_invalid');}
            catch{socket.destroy();return;}
            let response:any;
            try{response={challenge:req.challenge,value:await handle(req)};}catch(error){response={challenge:req.challenge,error: error instanceof Error&&/^manual_[a-z_]+$/.test(error.message)?error.message:'manual_source_unconfirmed'};}
            const stop=response.value?.phase==='stopping'&&req.operation==='stop';
            socket.end(JSON.stringify(seal(response,key))+'\n',()=>{if(stop)void closeOwned();});
          });chain=task.catch(()=>{socket.destroy();});
        });
      });
      const path=join(options.directory,'control.sock');
      if(Buffer.byteLength(path)>100)throw Error('manual_control_path_too_long');
      await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(path,()=>resolve());});await chmod(path,0o600);
      return {inspect:()=>structuredClone(state),close:async()=>{await new Promise<void>(resolve=>{server.close(()=>resolve());for(const s of sockets)s.destroy();});}};
    },
  };
}

async function request(path:string,key:Buffer,value:object){
  const socketPath=join(path,'control.sock'),s=await lstat(socketPath);
  if(!s.isSocket()||s.isSymbolicLink()||s.uid!==process.getuid?.()||(s.mode&0o077))throw Error('manual_control_invalid');
  const challenge=randomUUID();
  return new Promise<any>((resolve,reject)=>{
    const socket=createConnection(socketPath);let text='',settled=false;
    const fail=()=>{if(!settled){settled=true;reject(Error('manual_source_unconfirmed'));}socket.destroy();};
    socket.on('error',fail);socket.on('close',()=>{if(!settled)fail();});
    socket.on('connect',()=>socket.write(JSON.stringify(seal({...value,challenge},key))+'\n'));
    socket.on('data',chunk=>{text+=chunk.toString('utf8');if(Buffer.byteLength(text)>65536)return fail();if(!text.includes('\n'))return;
      try{const response=unseal(JSON.parse(text.trim()),key);if(response.challenge!==challenge)throw Error('challenge');settled=true;socket.end();
        if(response.error)reject(Error(/^manual_[a-z_]+$/.test(response.error)?response.error:'manual_source_unconfirmed'));else resolve(response.value);
      }catch{fail();}
    });
  });
}

/** Trusted local controller. Captures a pidfd before retirement; never adopts,
 * kills, starts or disables the systemd service. Every mutation is one-use. */
export function createManualSystemdHandoffSource(options:{
  sourceDirectory:string;claimDirectory:string;bindings:ExistingStateBinding[];observer:SystemdSourceObserver;
}):ExistingStateHandoffSource {
  const bindings=structuredClone(options.bindings);
  return {claim:async input=>{
    const claim=claimValue(input),sourceKey=await authorityKey(options.sourceDirectory);
    // Claim directory creation is a durable CAS across competing controllers.
    // Any interruption leaves it consumed. Reading completed proof is passive.
    let key:Buffer;
    try{key=await createAuthority(options.claimDirectory,{claim,phase:'accepted'});}
    catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;
      const old=await readAuthority(options.claimDirectory,await authorityKey(options.claimDirectory));
      if(old.phase==='completed'&&equal(old.claim,claim))return old.proof as ExistingStateHandoffProof;
      throw Error('manual_handoff_consumed_or_unknown');
    }
    const original:SourceState=await request(options.sourceDirectory,sourceKey,{operation:'inspect'});
    if(original.schema!=='manual-systemd-source-v1'||!['ready','refused'].includes(original.phase)||!sameService(original.expected,claim.expected)||!same(original.target.identity,claim.target)||
      original.dataBindingDigest!==claim.dataBindingDigest||!equal(original.owners,claim.participantIds)||await inspectExistingStateBindings(bindings)!==claim.dataBindingDigest)
      throw Error('manual_binding_mismatch');
    const witness=await options.observer.capture();if(!equal(witness,original.witness))throw Error('manual_process_changed');
    const observation=await options.observer.bind(witness);
    try{
      const args={claim,witnessDigest:witnessDigest(witness)};
      const retired:SourceState=await request(options.sourceDirectory,sourceKey,{operation:'retire',...args});
      if(retired.phase!=='retired'||!equal(retired.claim,claim)||!retired.fenceId||!equal(retired.owners,claim.participantIds))throw Error('manual_retirement_unconfirmed');
      await writeAuthority(options.claimDirectory,key,{claim,phase:'retired',witness,fenceId:retired.fenceId});
      // Capture uncertainty before the stop request. Lost replies never retry.
      await writeAuthority(options.claimDirectory,key,{claim,phase:'stop_requested',witness,fenceId:retired.fenceId});
      const stopping:SourceState=await request(options.sourceDirectory,sourceKey,{operation:'stop',...args});
      if(stopping.phase!=='stopping'||!equal(stopping.claim,claim))throw Error('manual_stop_unconfirmed');
      await observation.exited;
      const closed:SourceState=await readAuthority(options.sourceDirectory,sourceKey);
      if(closed.phase!=='closed'||!equal(closed.claim,claim)||closed.fenceId!==retired.fenceId||!equal(closed.witness,witness)||!equal(closed.owners,claim.participantIds))
        throw Error('manual_closure_unconfirmed');
      await options.observer.confirmExited(witness);
      if(await inspectExistingStateBindings(bindings)!==claim.dataBindingDigest)throw Error('manual_binding_mismatch');
      const proof:ExistingStateHandoffProof={schema:'distribution-existing-state-handoff-v1',claim,stopped:{
        commandId:claim.stoppedCommandId,operation:'stop',status:'stopped',phase:'stopped',expected:claim.expected,target:original.target,
        fenceId:retired.fenceId,qualifiedOwners:closed.owners,handoffRetired:true,handoffClaim:claim,resumeCommandId:claim.commandId,updatedAt:Date.now(),
        exitProof:{ownerReceiptId:randomUUID(),ownerId:claim.expected.ownerId,instanceId:claim.expected.instanceId,observedAt:Date.now(),code:null,signal:null},
      }};
      await writeAuthority(options.claimDirectory,key,{claim,phase:'completed',proof});return proof;
    }finally{observation.close();}
  }};
}
