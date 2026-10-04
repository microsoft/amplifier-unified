import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
const {PosixProcessOwner,PosixOwnedProcessLifecycle,classifyStartupError,parseStartupFailure,startupFailure,startupFailureFrom}=await import(process.env.DISTRIBUTION_OWNER_MODULE??'../dist/index.js');
const target={identity:{id:'test',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},handle:'test'};
const tick=()=>new Promise(r=>setTimeout(r,10));
async function until(fn){for(let i=0;i<300;i++){const value=await fn();if(value)return value;await tick();}throw Error('fixture_deadline');}
async function fixture(t,body,options={}){
 const directory=await mkdtemp(join(tmpdir(),'startup-diagnostics-')),entry=join(directory,'app.mjs'),ready=join(directory,'ready');
 await writeFile(entry,body);
 let expected;
 const lifecycle=new PosixOwnedProcessLifecycle({ownerId:'test',readinessMs:options.readinessMs??1000,
  resolve:async()=>({command:process.execPath,args:[entry,ready],...(options.cwd?{cwd:join(directory,'absent')}: {})}),
  inspect:async()=>options.inspectReady&&await readFile(ready,'utf8').catch(()=>null)?{instanceId:expected.instanceId,dataScope:'test',identity:target.identity,ready:true}:null,
  admitRestart:async()=>{throw Error('unused');},initialProvisioning:{claim:async r=>{expected={instanceId:r.instanceId,dataScope:r.dataScope,releaseDigest:r.target.identity.digest};return {kind:'pristine-installation',installationId:'test',commandId:r.commandId,instanceId:r.instanceId,dataScope:r.dataScope,targetDigest:r.target.identity.digest};}},
 });
 t.after(async()=>{await lifecycle.close().catch(()=>{});await rm(directory,{recursive:true,force:true});});
 return {lifecycle,directory,get expected(){return expected;}};
}
test('real child initialization errors survive ownership handshake without error text or paths',async t=>{
 for(const [body,reason] of [
  ["await import('./missing-private-component.mjs');",'dependency_unavailable'],
  ["throw Error('Transfer staging must be within configured workspace roots');",'transfer_workspace_mismatch'],
  ["throw Error('secret-credential-body'.repeat(100000));",'application_exception'],
 ]){
  const f=await fixture(t,body);let failure;
  await assert.rejects(f.lifecycle.startInitial(target,'test'),error=>{failure=startupFailureFrom(error);return error.message==='owned_startup_failed';});
  assert.equal(failure.reason,reason);assert.equal(failure.source,'child-bootstrap');assert.equal(failure.phase,'initialization');
  assert.ok(JSON.stringify(failure).length<700);assert.ok(!JSON.stringify(failure).includes('secret-credential'));assert.ok(!JSON.stringify(failure).includes(f.directory));
  await until(()=>f.lifecycle.processes.exitProof(f.expected));
  assert.throws(()=>f.lifecycle.processes.assertOwned(f.expected),/ownership_unproven/);
  await assert.rejects(f.lifecycle.processes.stop(f.expected),/ownership_unproven/);
 }
});
test('spawn failure and unconfirmed readiness retain uncertainty without an automatic launch or kill',async t=>{
 const missing=await fixture(t,'',{cwd:true});
 await assert.rejects(missing.lifecycle.startInitial(target,'test'),e=>startupFailureFrom(e)?.reason==='resource_missing'&&startupFailureFrom(e)?.phase==='spawn');
 assert.equal(missing.lifecycle.processes.inspect().state,'unknown');
 const waiting=await fixture(t,"setInterval(()=>{},1000);",{readinessMs:100});
 await assert.rejects(waiting.lifecycle.startInitial(target,'test'),e=>startupFailureFrom(e)?.reason==='readiness_unconfirmed');
 assert.equal(waiting.lifecycle.processes.inspect().state,'running');
 assert.throws(()=>waiting.lifecycle.processes.assertOwned({...waiting.expected,instanceId:'different'}),/ownership_unproven/);
});
test('untrusted diagnostic messages and late failures cannot replace owned startup state',async t=>{
 const f=await fixture(t,`import {writeFileSync} from 'node:fs';
 process.send({schema:'distribution-owned-child-v1',operation:'startup-failed',key:'wrong',instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,failure:${JSON.stringify(startupFailure('permission_denied','initialization','child-bootstrap'))}});
 writeFileSync(process.argv[2],'ready');await new Promise(r=>setTimeout(r,150));throw Error('late-private-body');`,{inspectReady:true});
 await f.lifecycle.startInitial(target,'test');assert.equal(f.lifecycle.processes.inspect().state,'running');
 await until(()=>f.lifecycle.processes.exitProof(f.expected));
 assert.throws(()=>f.lifecycle.processes.assertStarting(f.expected),/ownership_unproven/);
});
test('diagnostic projection allowlists reasons and regenerates guidance rather than trusting adapter text',()=>{
 const value=startupFailure('dependency_unavailable','initialization');
 const safe=parseStartupFailure({...value,guidance:'secret',stack:'secret',env:{KEY:'secret'},signal:'secret',exitCode:999999});
 assert.deepEqual(safe,value);assert.equal(parseStartupFailure({...value,reason:'secret'}),undefined);
 assert.equal(classifyStartupError(Object.assign(Error('secret'),{code:'EADDRINUSE'})),'address_unavailable');
 assert.equal(classifyStartupError(new SyntaxError('secret')),'syntax_invalid');
 assert.equal(classifyStartupError({message:'secret',code:'EACCES'}),'application_exception');
});
