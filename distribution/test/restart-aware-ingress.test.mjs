import test from 'node:test';import assert from 'node:assert/strict';
import {restartAwareIngress} from '../src/restart-aware-ingress.mjs';
const context={purpose:'distribution-update',fenceId:'f',commandId:'c',instanceId:'i',dataScope:'s'},fence=()=>({...context,phase:'checking',admissionStage:'participants',owners:['business','ingress']});
function fixture(){let status={intakeClosed:true,fence:fence()};const calls=[];
 const gate={participant:{id:'ingress',acquire:async()=>{calls.push('acquire');return {ownerId:'ingress',fenceId:'f',release:async(outcome)=>calls.push(outcome)}},reconcileRelease:async()=>{},abortAdmission:async()=>({status:'released'})}},access={drainForRestart:async()=>{calls.push('drain');return {release:()=>calls.push('reopen')}}};
 return {calls,access,setStatus:value=>{status=value},participant:restartAwareIngress(gate,{getAccess:()=>access,inspectQuiescence:()=>status,requiredOwners:()=>['business','ingress']})};}
test('only exact held business-owner restart admission can retire sockets',async()=>{
 const f=fixture();for(const patch of [{intakeClosed:false},{fence:{...fence(),owners:['ingress']}},{fence:{...fence(),commandId:'foreign'}},{fence:{...fence(),phase:'unknown'}},{fence:{...fence(),owners:['ingress','business']}}]){f.setStatus({intakeClosed:true,fence:fence(),...patch});await assert.rejects(f.participant.acquire(context),/admission_unconfirmed/);assert.deepEqual(f.calls,[]);}
 f.setStatus({intakeClosed:true,fence:fence()});const lease=await f.participant.acquire(context);assert.deepEqual(f.calls,['drain','acquire']);await lease.release('unknown',{});assert.deepEqual(f.calls,['drain','acquire','unknown']);await f.participant.reconcileRelease({...context,outcome:'unchanged'});assert.equal(f.calls.at(-1),'reopen');
});
test('busy HTTP drain refuses without acquiring, and successful refusal release reopens access',async()=>{
 const f=fixture();f.access.drainForRestart=async()=>null;assert.equal(await f.participant.acquire(context),null);assert.deepEqual(f.calls,[]);
 const ready=fixture(),lease=await ready.participant.acquire(context);await lease.release('unchanged',{});assert.deepEqual(ready.calls,['drain','acquire','unchanged','reopen']);
});
test('in-place recovery never retires the initiating connection',async()=>{const f=fixture();await f.participant.acquire({...context,purpose:'recovery'});assert.deepEqual(f.calls,['acquire']);});
