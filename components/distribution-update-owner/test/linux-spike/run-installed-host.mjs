// Separate pristine-start qualification using actual consumed provisioning,
// closed Host, signed runtime and durable unit custody, then ordinary A->B.
// The payload contains only locally compiled components and existing JS deps.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile,cp,readdir,lstat,unlink,realpath} from 'node:fs/promises';
import {join,resolve,dirname,relative} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {connect} from 'node:net';
import {execFile,spawn} from 'node:child_process';
import {promisify} from 'node:util';
import * as tar from 'tar';
import {DatabaseSync} from 'node:sqlite';
import {createInterface} from 'node:readline';
import {publisher,source,dependencySource} from '../release-fixtures.mjs';
import {SignedReleaseAdapter,releaseDigest,ServiceLifecycleOwner,DistributionUpdateOwner,
  connectHostControlFile,serveSupervisor,createPristineInstallation,createProductionSupervisorPorts} from '../../dist/index.js';
import {LinuxUnitLifecycle} from '../../dist/linux-unit-lifecycle.js';

const [rootArg,payloadArg]=process.argv.slice(2),root=resolve(rootArg),payload=resolve(payloadArg);
assert.equal(process.platform,'linux');
await mkdir(root,{mode:0o700});
const unit='amplifier-installed-lifecycle-'+randomUUID().replaceAll('-','').slice(0,12)+'.service';
const execute=promisify(execFile),ctl=async(...args)=>(await execute('systemctl',['--user',...args])).stdout.trim();
assert.equal(await ctl('show',unit,'--property=LoadState','--value'),'not-found');
await writeFile(join(root,'owner.json'),JSON.stringify({schema:'owned-linux-host-fixture-v1',unit,production:false}));
const evidence=[],clients=[],binding={installationId:randomUUID(),ownerId:'fixture-supervisor',dataScope:'fixture-data'};
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const {AhpClient}=await import('@microsoft/agent-host-protocol/client');
const {WebSocketTransport}=await import('@microsoft/agent-host-protocol/ws');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function observed(promise){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{
  timer=setTimeout(()=>reject(Error('fixture_observation_expired')),20000);
})]);}finally{clearTimeout(timer);}}
async function until(read){const deadline=Date.now()+20000;do{const value=await read();if(value)return value;await pause(30);}while(Date.now()<deadline);throw Error('fixture_observation_expired');}
async function rpc(request){return new Promise((resolve,reject)=>{const s=connect(join(root,'fixture.sock'));let data='';
  s.on('error',reject);s.on('connect',()=>s.write(JSON.stringify(request)+'\n'));
  s.on('data',chunk=>{data+=chunk;if(data.includes('\n')){s.end();const v=JSON.parse(data);v.error?reject(Error(v.error)):resolve(v.result);}});
  s.setTimeout(3000,()=>s.destroy(Error('fixture_transport_expired')));
});}
async function artifact(version,origin){
  const stage=join(root,'artifact-'+version),directory=join(stage,'package');await mkdir(stage);
  await cp(payload,directory,{recursive:true});
  const pkg=JSON.parse(await readFile(join(directory,'package.json'),'utf8'));pkg.version=version+'.0.0';
  await writeFile(join(directory,'package.json'),JSON.stringify(pkg));
  const files=[],components=[];
  async function walk(dir){for(const name of await readdir(dir)){
    const file=join(dir,name),info=await lstat(file),path=relative(directory,file);
    if(info.isDirectory()){await walk(file);continue;}assert.ok(info.isFile());
    const bytes=await readFile(file);files.push({path,sha256:hash(bytes),bytes:bytes.length,mode:info.mode&0o777});
    if(path==='package.json'||/^node_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(path)){
      const meta=JSON.parse(bytes);components.push({name:meta.name,version:meta.version,root:path==='package.json'?'':dirname(path),
        repository:path==='package.json'?source:dependencySource,ref:'main',revision:path==='package.json'?String(version).repeat(40):'d'.repeat(40)});
    }
  }}
  await walk(directory);files.sort((a,b)=>a.path.localeCompare(b.path));
  const archive=join(root,`fixture-v${version}.tgz`);await tar.c({cwd:stage,file:archive,gzip:true,portable:true},['package']);
  const bytes=await readFile(archive),release={identity:{id:`fixture-v${version}`,version:version+'.0.0',revision:String(version).repeat(40),digest:''},
    artifact:{url:origin+`/fixture-v${version}.tgz`,sha256:hash(bytes),bytes:bytes.length},entrypoint:'server.mjs',platform:'any',arch:'any',files,components};
  release.identity.digest=releaseDigest(release);return {release,bytes};
}
let pub,controller,paths;
async function startController(config,initial=false){
 const child=spawn(process.execPath,[join(payload,'controller.mjs'),config,...(initial?['initial']:[])],{stdio:['pipe','pipe','pipe']});
 let errors='',readyResolve,readyReject;const pending=new Map();
 const ready=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
 child.stderr.on('data',chunk=>{errors+=chunk;});
 createInterface({input:child.stdout}).on('line',line=>{
  let value;try{value=JSON.parse(line);}catch{return;}
  if(value.ready)readyResolve();
  const next=pending.get(value.id);if(!next)return;pending.delete(value.id);
  value.error?next.reject(Error(value.error)):next.resolve(value.result);
 });
 child.on('exit',(code,signal)=>{readyReject(Error('controller_exit '+code+' '+signal+' '+errors));for(const p of pending.values())p.reject(Error('controller_exit '+errors));pending.clear();});
 const c={child,async call(action,args={}){const id=randomUUID();return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,action,args})+'\n');});},
  async kill(){if(child.exitCode===null&&child.signalCode===null){const exited=new Promise(resolve=>child.once('exit',resolve));child.kill('SIGKILL');await exited;}},errors:()=>errors};
 controller=c;await observed(ready);return c;
}
async function client(url,id){const c=new AhpClient(await WebSocketTransport.connect(url));c.connect();
 await c.initialize({clientId:id,protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});clients.push(c);return c;}
try{
 pub=await publisher();
 const a=await artifact(1,pub.origin),b=await artifact(2,pub.origin);
 paths=await createPristineInstallation({directory:join(root,'installation'),dataScope:binding.dataScope,initial:a.release.identity,plannedInstallationId:binding.installationId});
 await writeFile(paths.hostTokenFile,randomBytes(32).toString('hex'),{mode:0o600});
 const release={directory:paths.releaseDirectory,channelUrl:pub.origin+'/channel.json',trustedKeys:pub.keys,accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,
  launchEnv:{FIXTURE_ROOT:root,FIXTURE_TRUSTED_KEYS:JSON.stringify(pub.keys),PATH:dirname(process.execPath)+':/usr/bin:/bin'}};
 const adapter=new SignedReleaseAdapter({...release,resolveSources:async()=>await(await fetch(pub.origin+'/sources')).json()});
 pub.publish([a],1);const preparedA=await adapter.prepare(a.release.identity,{commandId:'prepare',signal:new AbortController().signal});
 const config={schema:'distribution-supervisor-v1',dataDirectory:paths.dataDirectory,dataScope:binding.dataScope,tokenFile:paths.supervisorTokenFile,discoveryFile:paths.supervisorDiscoveryFile,
  hostDiscoveryFile:paths.hostDiscoveryFile,provisioningAuthorityFile:paths.authorityFile,release,initial:preparedA,
  serviceLifecycle:{installationId:binding.installationId,ownerId:binding.ownerId,platform:{kind:'linux-user-unit',unit,unitDirectory:join(root,'units')}}};
 const configFile=join(root,'supervisor-config.json');await writeFile(configFile,JSON.stringify(config),{mode:0o600});
 await startController(configFile,true);
 const initialCommand='initial:'+hash(binding.installationId).slice(0,32);
 const db=new DatabaseSync(join(paths.dataDirectory,'service','service.sqlite3'),{readOnly:true});
 const initial=JSON.parse(db.prepare('SELECT value FROM commands WHERE id=?').get(initialCommand).value);db.close();
 assert.equal(initial.status,'ready');assert.equal(initial.admissionSettlement.state,'settled');
 const first=await rpc({op:'inspect'});assert.equal(first.identity.digest,preparedA.identity.digest);assert.equal(first.intakeClosed,false);
 assert.equal(initial.localCustody.invocationId,first.invocationId);
 const claim=await readFile(join(paths.directory,'initial-provisioning.claim'));
 assert.equal(JSON.parse(claim).instanceId,first.instanceId);
 await controller.kill();await startController(configFile);
 assert.equal((await rpc({op:'inspect'})).instanceId,first.instanceId);
 assert.deepEqual(await readFile(join(paths.directory,'initial-provisioning.claim')),claim);
 evidence.push({case:'normal-factory-pristine-and-controller-reopen',passed:true,initial,first,claimUnchanged:true});
 const c=await client(first.hostUrl,'fixture-client'),session='ahp-session:/'+randomUUID(),chat=session.replace('ahp-session:','ahp-chat:');
 await c.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(root).href]});
 await c.subscribe(session);const subscription=await c.subscribe(chat,{view:{turns:2}});
 c.dispatch(session,{type:'session/activeClientSet',activeClient:{clientId:'fixture-client',tools:[{name:'inspect-ui',description:'Fixture callback'}]}});
 await until(async()=>(await rpc({op:'clients',session})).length===1);
 const unknown=await rpc({op:'seed-unknown'}),turnId=randomUUID();
 const ready=(async()=>{for await(const event of subscription.subscription){if(event.type==='action'&&event.params.action.type==='chat/toolCallReady')return event.params.action;}throw Error('tool-never-ready');})();
 c.dispatch(chat,{type:'chat/turnStarted',turnId,startedAt:new Date().toISOString(),message:{text:'client-tool',origin:{kind:'user'}}});
 const tool=await observed(ready);
 pub.publish([a,b],2);
 const requestedAt=Date.now();await controller.call('check',{commandId:'check-b'});
 const checked=await until(async()=>{const v=await controller.call('receipt',{commandId:'check-b'});return v.status==='running'||v.status==='queued'?null:v;});
 assert.equal(checked.status,'succeeded',JSON.stringify(checked));
 await controller.call('install',{commandId:'install-b'});
 const serviceHost=connectHostControlFile(paths.hostDiscoveryFile,binding.dataScope);
 const closed=await until(async()=>{const v=await serviceHost.service.inspectServiceLifecycle();return v.intakeClosed?v:null;});
 assert.equal(closed.fence.phase,'closed');
 assert.equal((await controller.call('receipt',{commandId:'install-b'})).status,'running');
 await assert.rejects(c.request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'fixture',workingDirectories:[pathToFileURL(root).href]}));
 await c.request('listSessions',{channel:'ahp-root://',limit:50});
 c.dispatch(chat,{type:'chat/toolCallComplete',turnId:'wrong-turn',toolCallId:tool.toolCallId,result:{success:true}});
 await pause(50);assert.equal((await controller.call('receipt',{commandId:'install-b'})).status,'running');
 c.dispatch(chat,{type:'chat/toolCallComplete',turnId,toolCallId:tool.toolCallId,result:{success:true,pastTenseMessage:'Inspected',structuredContent:{fixture:true}}});
 const updated=await until(async()=>{const v=await controller.call('receipt',{commandId:'install-b'});return ['queued','running'].includes(v.status)?null:v;});
 assert.equal(updated.status,'succeeded',JSON.stringify(updated));
 const second=await rpc({op:'inspect'});assert.equal(second.identity.digest,b.release.identity.digest);assert.notEqual(second.instanceId,first.instanceId);assert.equal(second.intakeClosed,false);
 assert.deepEqual(await rpc({op:'receipt',commandId:'saved-unknown'}),unknown);
 serviceHost.close();
 evidence.push({case:'manual-check-install-through-normal-owner',passed:true,updated,elapsedMs:Date.now()-requestedAt,acceptedCompletionDrained:true,staleCallbackRefused:true,newWorkRefusedDuringDrain:true,unknownUnchanged:true});
 await controller.kill();await startController(configFile);
 assert.equal((await rpc({op:'inspect'})).instanceId,second.instanceId);
 const launches=(await readFile(join(root,'launches.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
 assert.equal(launches.length,2);assert.ok(launches.every(v=>v.intakeClosed));
 const next=await client(second.hostUrl,'next-client'),nextSession='ahp-session:/'+randomUUID(),nextChat=nextSession.replace('ahp-session:','ahp-chat:');
 await next.request('createSession',{channel:nextSession,provider:'fixture',workingDirectories:[pathToFileURL(root).href]});
 const nextTurn=randomUUID();next.dispatch(nextChat,{type:'chat/turnStarted',turnId:nextTurn,startedAt:new Date().toISOString(),message:{text:'after update',origin:{kind:'user'}}});
 await until(async()=>(await rpc({op:'receipt',commandId:nextTurn}))?.status==='completed');
 evidence.push({case:'replacement-controller-reopen-no-replay',passed:true,launches,newTurnCompleted:true});
 for(const c of clients)await c.shutdown().catch(()=>{});
 const stopped=await controller.call('stop',{commandId:'stop-b'});assert.equal(stopped.status,'stopped',JSON.stringify(stopped));
 await controller.call('close');await controller.kill();
}catch(error){evidence.push({case:'integration',passed:false,error:String(error.stack??error),controllerErrors:controller?.errors()});process.exitCode=1;}
finally{
 for(const c of clients)await c.shutdown().catch(()=>{});await controller?.kill();
 const state=await ctl('show',unit,'--property=ActiveState','--value');
 if(!['inactive','failed'].includes(state)){
  // Explicit cleanup of this disposable unit, never application timeout policy.
  await ctl('kill','--kill-whom=all','--signal=SIGKILL',unit).catch(()=>{});await ctl('stop',unit).catch(()=>{});
 }
 await pub?.close();await ctl('reset-failed',unit).catch(()=>{});
 const runtimeLink=join('/run/user',String(process.getuid()),'systemd/user',unit);
 try{if((await realpath(runtimeLink)).startsWith(root+'/units/'))await unlink(runtimeLink);}catch(e){if(e.code!=='ENOENT')throw e;}
 await ctl('daemon-reload');
 const cleanup=await ctl('show',unit,'--property=LoadState,ActiveState,ControlGroup');
 await writeFile(join(root,'ACCEPTANCE.json'),JSON.stringify({schema:'linux-installed-supervisor-integration-v1',unit,evidence,cleanup},null,2)+'\n');
 console.log(JSON.stringify({passed:evidence.every(e=>e.passed),cases:evidence.map(e=>({case:e.case,passed:e.passed})),cleanup}));
}
