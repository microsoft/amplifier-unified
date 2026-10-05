// Separate pristine-start qualification using actual consumed provisioning,
// closed Host, signed runtime and durable unit custody, then ordinary A->B.
// The payload contains only locally compiled components and existing JS deps.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile,cp,readdir,lstat,unlink,realpath} from 'node:fs/promises';
import {join,resolve,dirname,relative} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {connect} from 'node:net';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as tar from 'tar';
import {DatabaseSync} from 'node:sqlite';
import {publisher,source,dependencySource} from '../release-fixtures.mjs';
import {SignedReleaseAdapter,releaseDigest,ServiceLifecycleOwner,DistributionUpdateOwner,
  connectHostControlFile,serveSupervisor,createPristineInstallation,createProductionSupervisorPorts} from '../../dist/index.js';
import {LinuxUnitLifecycle} from '../../dist/linux-unit-lifecycle.js';

const [rootArg,payloadArg]=process.argv.slice(2),root=resolve(rootArg),payload=resolve(payloadArg);
assert.equal(process.platform,'linux');
await mkdir(root,{mode:0o700});
const unit='amplifier-initial-lifecycle-'+randomUUID().replaceAll('-','').slice(0,12)+'.service';
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
let pub,service,supervisor,owner,lifecycle,host,ports,paths;
try{
  pub=await publisher();
  const hostKey=randomBytes(32).toString('hex'),superKey=randomBytes(32).toString('hex');
  const adapter=new SignedReleaseAdapter({directory:join(root,'installation','releases'),channelUrl:pub.origin+'/channel.json',
    trustedKeys:pub.keys,accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,
    resolveSources:async()=>await(await fetch(pub.origin+'/sources')).json(),
    launchEnv:{FIXTURE_ROOT:root,FIXTURE_PROVISIONING_AUTHORITY:join(root,'installation','initial-provisioning.json'),FIXTURE_TRUSTED_KEYS:JSON.stringify(pub.keys),PATH:dirname(process.execPath)+':/usr/bin:/bin',
      AMPLIFIER_DISTRIBUTION_INSTALLATION_ID:binding.installationId,AMPLIFIER_DISTRIBUTION_OWNER_ID:binding.ownerId}});
  const context={commandId:'prepare',signal:new AbortController().signal};
  const a=await artifact(1,pub.origin);
  paths=await createPristineInstallation({directory:join(root,'installation'),dataScope:binding.dataScope,initial:a.release.identity,plannedInstallationId:binding.installationId});
  await writeFile(paths.hostTokenFile,hostKey,{mode:0o600});await writeFile(paths.supervisorTokenFile,superKey,{mode:0o600});
  pub.publish([a],1);const preparedA=await adapter.prepare(a.release.identity,context);
  const b=await artifact(2,pub.origin);let preparedB;
  let current=preparedA;
  ports=createProductionSupervisorPorts({...paths,provisioningAuthorityFile:paths.authorityFile,resolveSources:async()=>await(await fetch(pub.origin+'/sources')).json()});
  host=connectHostControlFile(paths.hostDiscoveryFile,binding.dataScope);
  lifecycle=new LinuxUnitLifecycle({ownerId:binding.ownerId,unit,unitDirectory:join(root,'units'),observationMs:15000,
    resolve:t=>adapter.resolveLaunch(t),inspect:async()=>{try{return await rpc({op:'inspect'});}catch{return null;}},
    initialProvisioning:ports.initialProvisioning});
  owner=new DistributionUpdateOwner({directory:join(root,'updates'),dataScope:binding.dataScope,releases:adapter,initial:preparedA,
    lifecycle:{inspect:host.inspect,admitRestart:async()=>{throw Error('not-used');},restart:async()=>{throw Error('not-used');}}});
  let allowInitialRelease;
  const releaseGate=new Promise(resolve=>{allowInitialRelease=resolve;});
  const serviceHost={...host.service,releaseServiceStart:async request=>{
    await releaseGate;return host.service.releaseServiceStart(request);
  }};
  async function openSupervisor(){
    service=new ServiceLifecycleOwner({directory:join(root,'service'),...binding,host:serviceHost,lifecycle,releases:adapter,currentRelease:()=>current});
    supervisor=await serveSupervisor({owner,service,token:superKey});
    await writeFile(paths.supervisorDiscoveryFile,JSON.stringify({schema:'distribution-supervisor-connection-v1',url:supervisor.url,
      tokenFile:paths.supervisorTokenFile}),{mode:0o600});
  }
  await openSupervisor();
  service.startInitial({commandId:'initial',target:preparedA});
  await until(async()=>service.receipt('initial')?.status==='ready');
  const beforeRelease=await rpc({op:'inspect'});
  assert.equal(beforeRelease.intakeClosed,true);
  const db=new DatabaseSync(join(root,'service','service.sqlite3'),{readOnly:true});
  const savedStart=JSON.parse(db.prepare('SELECT value FROM commands WHERE id=?').get('initial').value);db.close();
  assert.equal(savedStart.localCustody.invocationId,beforeRelease.invocationId);
  assert.equal(savedStart.initialClaim.commandId,'initial');
  const consumed=JSON.parse(await readFile(join(paths.directory,'initial-provisioning.claim'),'utf8'));
  assert.equal(consumed.instanceId,beforeRelease.instanceId);
  const blocked=await client(beforeRelease.hostUrl,'initial-observer');
  await assert.rejects(blocked.request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'fixture',workingDirectories:[pathToFileURL(root).href]}));
  await blocked.request('listSessions',{channel:'ahp-root://',limit:10});
  allowInitialRelease();
  const started=await service.waitFor('initial');
  assert.equal(started.status,'ready');assert.equal(started.admissionSettlement.state,'settled');
  assert.equal(started.exitProof,undefined);assert.equal(started.stoppedCommandId,undefined);
  await supervisor.close();await service.close();await openSupervisor();
  assert.equal(service.startInitial({commandId:'initial',target:preparedA}).expected.instanceId,started.expected.instanceId);
  assert.equal((await service.reconcile('initial')).admissionSettlement.state,'settled');
  const first=await rpc({op:'inspect'}),expected={...binding,instanceId:first.instanceId,releaseDigest:first.identity.digest};
  assert.equal(first.identity.digest,preparedA.identity.digest);assert.equal(first.intakeClosed,false);
  evidence.push({case:'pristine-closed-start',passed:true,started,custodyBeforeReady:true,consumedClaim:consumed,
    preActivationNewWorkRefused:true,preActivationPassiveRead:true,sameGenerationAfterOwnerReopen:true});
  pub.publish([a,b],2);preparedB=await adapter.prepare(b.release.identity,context);
  async function client(url,id){const c=new AhpClient(await WebSocketTransport.connect(url));c.connect();
    await c.initialize({clientId:id,protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});clients.push(c);return c;}
  const c=await client(first.hostUrl,'fixture-client');
  const session='ahp-session:/'+randomUUID(),chat=session.replace('ahp-session:','ahp-chat:');
  await c.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(root).href]});
  await c.subscribe(session);const subscription=await c.subscribe(chat,{view:{turns:2}});
  c.dispatch(session,{type:'session/activeClientSet',activeClient:{clientId:'fixture-client',tools:[{name:'inspect-ui',description:'Fixture callback'}]}});
  await until(async()=>(await rpc({op:'clients',session})).length===1);
  const unknown=await rpc({op:'seed-unknown'}),turnId=randomUUID();
  const ready=(async()=>{for await(const event of subscription.subscription){if(event.type==='action'&&event.params.action.type==='chat/toolCallReady')return event.params.action;}throw Error('tool-never-ready');})();
  c.dispatch(chat,{type:'chat/turnStarted',turnId,startedAt:new Date().toISOString(),message:{text:'client-tool',origin:{kind:'user'}}});
  const tool=await observed(ready);
  service.stop({commandId:'stop-a',expected});
  const closed=await until(async()=>{const v=await host.service.inspectServiceLifecycle();return v.intakeClosed?v:null;});
  assert.equal(service.receipt('stop-a').status,'running');assert.equal(closed.fence.phase,'closed');
  await assert.rejects(c.request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'fixture',workingDirectories:[pathToFileURL(root).href]}));
  await c.request('listSessions',{channel:'ahp-root://',limit:50});
  c.dispatch(chat,{type:'chat/toolCallComplete',turnId:'wrong-turn',toolCallId:tool.toolCallId,result:{success:true}});
  await pause(50);assert.equal(service.receipt('stop-a').status,'running');
  c.dispatch(chat,{type:'chat/toolCallComplete',turnId,toolCallId:tool.toolCallId,result:{success:true,pastTenseMessage:'Inspected',structuredContent:{fixture:true}}});
  const stopped=await service.waitFor('stop-a');assert.equal(stopped.status,'stopped');
  assert.equal(await ctl('show',unit,'--property=ActiveState','--value'),'inactive');
  evidence.push({case:'real-Host-drain',passed:true,stopped,newStartRefused:true,passiveRead:true,staleCallbackRefused:true});
  await supervisor.close();await service.close();await openSupervisor();
  assert.equal((await service.inspect()).state,'stopped');
  service.resume({commandId:'resume-b',stoppedCommandId:'stop-a',expected,target:preparedB});
  const resumed=await service.waitFor('resume-b');assert.equal(resumed.status,'ready',JSON.stringify(resumed));
  assert.equal(resumed.admissionSettlement.state,'settled');
  assert.deepEqual(resumed.activation,{schema:'distribution-service-activation-v1',target:preparedB.identity});
  current=preparedB;
  const second=await rpc({op:'inspect'});assert.equal(second.identity.digest,preparedB.identity.digest);
  assert.notEqual(second.instanceId,first.instanceId);assert.equal(second.intakeClosed,false);
  assert.deepEqual(await rpc({op:'receipt',commandId:'saved-unknown'}),unknown);
  const launches=(await readFile(join(root,'launches.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(launches.length,2);assert.equal(launches[0].intakeClosed,true);assert.equal(launches[1].intakeClosed,true);
  const next=await client(second.hostUrl,'next-client'),nextSession='ahp-session:/'+randomUUID(),nextChat=nextSession.replace('ahp-session:','ahp-chat:');
  await next.request('createSession',{channel:nextSession,provider:'fixture',workingDirectories:[pathToFileURL(root).href]});
  const nextTurn=randomUUID();next.dispatch(nextChat,{type:'chat/turnStarted',turnId:nextTurn,startedAt:new Date().toISOString(),message:{text:'after update',origin:{kind:'user'}}});
  await until(async()=>(await rpc({op:'receipt',commandId:nextTurn}))?.status==='completed');
  evidence.push({case:'signed-B-after-controller-reopen',passed:true,resumed,launches,unknownUnchanged:true,newTurnCompleted:true});
  for(const c of clients)await c.shutdown().catch(()=>{});
  service.stop({commandId:'stop-b',expected:resumed.observed});
  assert.equal((await service.waitFor('stop-b')).status,'stopped');
}catch(error){evidence.push({case:'integration',passed:false,error:String(error.stack??error)});process.exitCode=1;}
finally{
  for(const c of clients)await c.shutdown().catch(()=>{});
  if(lifecycle){
    const state=await ctl('show',unit,'--property=ActiveState','--value');
    if(!['inactive','failed'].includes(state)){
      // Explicit disposable-fixture cleanup only; never update timeout policy.
      await ctl('kill','--kill-whom=all','--signal=SIGKILL',unit).catch(()=>{});
      await ctl('stop',unit).catch(()=>{});
    }
  }
  await supervisor?.close();await service?.close();await owner?.close();host?.close();ports?.close();await pub?.close();
  await ctl('reset-failed',unit).catch(()=>{});
  const runtimeLink=join('/run/user',String(process.getuid()),'systemd/user',unit);
  try{if((await realpath(runtimeLink)).startsWith(root+'/units/'))await unlink(runtimeLink);}catch(e){if(e.code!=='ENOENT')throw e;}
  await ctl('daemon-reload');
  const cleanup=await ctl('show',unit,'--property=LoadState,ActiveState,ControlGroup');
  await writeFile(join(root,'ACCEPTANCE.json'),JSON.stringify({schema:'linux-initial-host-integration-v1',unit,evidence,cleanup},null,2)+'\n');
  console.log(JSON.stringify({passed:evidence.every(e=>e.passed),cases:evidence.map(e=>({case:e.case,passed:e.passed})),cleanup}));
}
