import {readFile,writeFile,appendFile,mkdir,rename} from 'node:fs/promises';
import {createServer} from 'node:http';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const c=JSON.parse(await readFile(process.argv[2],'utf8'));
const api=await import(c.api);
const {createManualSystemdHandoffLauncher,createLinuxSystemdSourceObserver,createManualIngressGate}=api;
const invocation=process.env.INVOCATION_ID??'a'.repeat(32);if(!c.real)process.env.INVOCATION_ID=invocation;
const witness=c.real?null:{unit:'fixture.service',pid:process.pid,bootId:'fixture-boot',startTicks:'1',invocationId:invocation,cgroup:'/fixture',unitDigest:'a'.repeat(64)};
const observer=c.real?createLinuxSystemdSourceObserver({unit:c.unit,python:'/usr/bin/python3'}):{capture:async()=>witness};
const expected={installationId:'manual-fixture',ownerId:'fixture-owner',dataScope:'fixture-scope',instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID??'old-instance',releaseDigest:c.target.identity.digest};
const qualifyCurrent=async()=>{if(c.entryDigest&&createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).digest('hex')!==c.entryDigest)throw Error('fixture_source_changed');if(c.changedBytes)throw Error('fixture_bytes_changed');return c.target;};
let wrapper,supervisor,control;
if(c.destination){
 supervisor=api.connectSupervisorFileLazy(c.supervisorFile);
 wrapper={serviceLifecycle:{identity:expected,verifyRelease:api.createHostServiceReleaseVerifier({service:supervisor.service,inspectRunningService:()=>expected})}};
}else wrapper=await createManualSystemdHandoffLauncher({directory:c.source,expected,bindings:c.bindings,observer,qualifyCurrent});
// The original saved history is read by both instances; never copied/reset.
const history=await readFile(join(c.data,'history'),'utf8');
const gate=await createManualIngressGate({directory:join(c.root,'ingress'),id:'ingress'});
const participants=[gate.participant];let held=null;
const log=e=>appendFile(join(c.root,'events.jsonl'),JSON.stringify(e)+'\n');
let host,server,leases=[];
if(c.hostModule){
 const {createHost}=await import(c.hostModule);
 host=await createHost({stateDirectory:join(c.root,'host-state'),allowedWorkspaceRoots:[c.root],engines:[{id:'unused',command:process.execPath,args:['-e','process.exit(1)']}],
 quiescence:{instanceId:expected.instanceId,dataScope:expected.dataScope,requiredOwners:['ingress'],coverage:{},participants,verifyRelease:async()=>{throw Error('fixture_has_no_update_authority');},serviceLifecycle:wrapper.serviceLifecycle}});
}else{
 host={admitServiceStop:async()=>{throw Error('manual fixture must never use weaker shared-service admission');},admitMaintenanceServiceStop:async r=>{
   if(c.busy)return {admitted:false,executed:false,intakeClosed:false,purpose:'service-stop',expected};
   const f={phase:'held',purpose:'service-stop',fenceId:'source-fence',commandId:r.commandId,instanceId:expected.instanceId,dataScope:expected.dataScope,serviceIdentity:expected,owners:c.missingOwner?[]:['ingress']};
   const lease=await gate.participant.acquire(f);if(!lease)throw Error('fixture_busy');leases.push(lease);held=f;await log({event:'held'});
   return {admitted:true,expected,...f,evidence:{activeWork:0,intakeClosed:true,instanceId:expected.instanceId,dataScope:expected.dataScope}};
 },inspectServiceLifecycle:async()=>({fence:held}),close:async()=>{}};
}
server=createServer((req,res)=>{const done=gate.enter();if(!done){res.writeHead(503).end();return;}
 res.once('close',done);res.end('synthetic fixture');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
if(c.destination){
 control=await api.serveHostControl({host,inspectRunning:async()=>({identity:c.target.identity,instanceId:expected.instanceId,dataScope:expected.dataScope,ready:true}),token:c.hostToken,discovery:{file:c.hostFile,tokenFile:c.hostTokenFile,dataScope:expected.dataScope}});
 await writeFile(join(c.root,'destination-ready.json'),JSON.stringify({expected,history,port:server.address().port}));
 process.on('SIGTERM',async()=>{await control.close();await new Promise(r=>server.close(r));await host.close();gate.close();supervisor.close();process.exit(0);});
 await new Promise(()=>{});
}
const controller=await wrapper.attach({host,requiredOwners:['ingress'],expectedOwners:c.censusMismatch?['other']:['ingress'],
 close:async()=>{
  const state=(await readFile(join(c.source,'authority.json'),'utf8'));await log({event:'closing',retired:state.includes('stopping'),gate:gate.inspect()});
  if(c.closeFailure)throw Error('fixture_close_failure');
  await new Promise(r=>server.close(r));await host.close();gate.close();await log({event:'closed'});
 },exit:()=>process.exit(0)});
// Publish complete readiness atomically; the parent polls this file.
await writeFile(join(c.root,'ready.tmp'),JSON.stringify({pid:process.pid,expected,witness:controller.inspect().witness,history,port:server.address().port}));
await rename(join(c.root,'ready.tmp'),join(c.root,'ready.json'));
process.on('SIGTERM',async()=>{await controller.close();await new Promise(r=>server.close(r));await host.close();process.exit(0);});
