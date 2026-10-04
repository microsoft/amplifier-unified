import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,mkdir,rm,access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const {createNotificationsCapability}=await import(process.env.ADMISSION_ABORT_amplifier_unified_notifications?pathToFileURL(resolve(process.env.ADMISSION_ABORT_amplifier_unified_notifications)).href:new URL('../src/index.js',import.meta.url).href);
const python=process.env.ADMISSION_ABORT_PYTHON;
const identity='notifications';
const fence={fenceId:'original-node-fence',commandId:'original-command',purpose:'distribution-update',instanceId:'original-launch',dataScope:'owned-scope'};
const proof={...fence,kind:'distribution-admission-abort',verified:true,receiptId:'authenticated-abort-proof'};
const forbidden=async()=>{throw Error('Fixture must not invoke external authority')};
async function fixture(){
 const dir=await mkdtemp(join(tmpdir(),'admission-abort-')),config=join(dir,'config.json'),projects=join(dir,'projects');await mkdir(projects);
 await writeFile(config,JSON.stringify({dataDir:join(dir,'owner'),stateDirectory:join(dir,'owner'),allowedRoots:[projects],defaultRoot:projects}));
 const options={owner:{command:python,args:['-I','-m','amplifier_unified_notifications.server','--config',config]},catalog:{workspaceProjectionStatus:async()=>({revision:0}),projectWorkspaces:async value=>({revision:value.throughRevision}),listWorkspaces:async()=>({items:[]}),list:async()=>({items:[]}),getWorkspace:async()=>null},inspectSession:forbidden,withSessionWorkspace:forbidden,listCoordinationSessions:forbidden,readCoordinationSession:forbidden,readCoordinationWorkers:forbidden,controlCoordinationWorker:forbidden,controlCoordinationSession:forbidden,observeSession:forbidden};
 return {dir,config,options,make:()=>createNotificationsCapability(options)};
}
function participant(cap){return typeof cap.quiescenceParticipant==='function'?cap.quiescenceParticipant(identity):cap.quiescenceParticipant;}
test('actual installed Python bridge retains abort receipt across restart and preserves newer fence',{skip:!python},async()=>{
 const f=await fixture();let cap=f.make();
 try{
  const p=participant(cap);assert.equal((await p.acquire(fence)).fenceId,fence.fenceId);
  await assert.rejects(p.abortAdmission({...fence,proof:{...proof,kind:'service-lifecycle'}}));
  const first=await p.abortAdmission({...fence,proof});assert.equal(first.status,'released');assert.equal(first.ownerId,identity);
  await cap.close();cap=f.make();const restarted=participant(cap);
  assert.deepEqual(await restarted.abortAdmission({...fence,proof}),first);
  const newer={...fence,fenceId:'newer-fence',commandId:'newer-command'};assert.equal((await restarted.acquire(newer)).fenceId,newer.fenceId);
  assert.deepEqual(await restarted.abortAdmission({...fence,proof}),first);
  assert.equal((await restarted.acquire(newer)).fenceId,newer.fenceId);
 }finally{await cap.close();await rm(f.dir,{recursive:true,force:true})}
});

test('Node-local busy refusal is durable before acknowledgement and never later acquires',{skip:!python},async()=>{
 const f=await fixture(),script=join(f.dir,'held-read.py'),entered=join(f.dir,'entered'),release=join(f.dir,'release');
 await writeFile(script,`import asyncio,json,sys
from pathlib import Path
from amplifier_unified_notifications.server import Peer
async def main():
 peer=Peer(json.loads(Path(sys.argv[1]).read_text()))
 original=peer.owner.request
 async def request(method,params):
  if method=='snapshot' and not Path(sys.argv[3]).exists():
   Path(sys.argv[2]).touch()
   while not Path(sys.argv[3]).exists():await asyncio.sleep(.01)
  return await original(method,params)
 peer.owner.request=request
 await peer.run()
asyncio.run(main())
`);
 f.options.owner.args=['-I',script,f.config,entered,release];const cap=f.make();
 try{
  const topic='notifications',pending=cap.read({topic,scope:'host',uri:cap.manifest.topics[topic].uri,clientId:'owned'});
  let seen=false;for(let i=0;i<500;i++){try{await access(entered);seen=true;break}catch{}await new Promise(r=>setTimeout(r,10))}assert.equal(seen,true);
  const p=participant(cap);assert.equal(await p.acquire(fence),null);
  await assert.rejects(p.abortAdmission({...fence,proof}),/pending|flight/i);
  await writeFile(release,'continue');await pending;
  assert.equal(await p.acquire(fence),null);
  assert.equal((await p.abortAdmission({...fence,proof})).status,'not-acquired');
  assert.equal((await p.acquire({...fence,fenceId:'fresh-fence',commandId:'fresh-command'})).fenceId,'fresh-fence');
 }finally{await writeFile(release,'continue');await cap.close();await rm(f.dir,{recursive:true,force:true})}
});
