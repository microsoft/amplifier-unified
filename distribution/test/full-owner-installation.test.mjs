import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,readFile,rm,chmod,lstat,readdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash,generateKeyPairSync,sign,randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {execFile,spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {releaseDigest,connectSupervisorFile} from '@amplifier/unified-distribution-update-owner';
import {inspectFullOwnerInstallation,installFullOwnerDistribution} from '../src/full-owner-installation.mjs';
import {OWNERS} from '../src/validate-config.mjs';
import {inventoryMcpRuntime,inventoryPythonRuntime} from '../src/release-runtime.mjs';
import {createHTTPSGitFixture} from './https-git-fixture.mjs';
const exec=promisify(execFile),hash=b=>createHash('sha256').update(b).digest('hex'),privateWrite=(p,v)=>writeFile(p,v,{mode:0o600});
const projection=r=>({id:r.id,version:r.version,revision:r.revision});

// Synthetic signed child: qualifies actual pristine claim, external supervisor,
// current Node, signed inventory and argv. It is deliberately NOT an acceptance
// test of all 21 Python/app owners; the release coordinator qualifies that graph.
const childCode=`
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {createHost} from ${JSON.stringify(import.meta.resolve('@amplifier/unified-host'))};
import * as api from ${JSON.stringify(import.meta.resolve('@amplifier/unified-distribution-update-owner'))};
const c=JSON.parse(await readFile(process.argv[2],'utf8'));
const initial=await api.inspectPristineInstallation(join(dirname(c.authority.supervisorDirectory),'initial-provisioning.json'));
let ready=false;
const runtime=await api.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:JSON.parse(await readFile(c.release.trustedKeysFile)),isReady:()=>ready,observeReady:()=>ready});
const expected=api.serviceIdentity({installationId:c.authority.installationId,ownerId:c.authority.ownerId,instanceId:runtime.instanceId,dataScope:runtime.dataScope,releaseDigest:runtime.identity.digest});
const supervisor=api.connectSupervisorFileLazy(c.authority.supervisorDiscoveryFile);
const host=await createHost({stateDirectory:c.application.stateDirectory,allowedWorkspaceRoots:c.application.allowedWorkspaceRoots,engines:[{id:'fixture',command:process.execPath,args:['-e','process.exit(2)']}],
 quiescence:{instanceId:runtime.instanceId,dataScope:runtime.dataScope,requiredOwners:[],coverage:{},participants:[],
 verifyRelease:api.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}),
 serviceLifecycle:{identity:expected,verifyRelease:api.createHostServiceReleaseVerifier({service:supervisor.service,inspectRunningService:()=>expected})}}});
const control=await api.serveHostControl({host,inspectRunning:runtime.inspectRunning,token:(await readFile(c.authority.hostTokenFile,'utf8')).trim(),
 discovery:{file:c.authority.hostDiscoveryFile,tokenFile:c.authority.hostTokenFile,dataScope:runtime.dataScope}});
await mkdir(c.receiptDirectory,{recursive:true,mode:0o700});
await writeFile(join(c.receiptDirectory,'child.json'),JSON.stringify({argv:process.argv,node:process.execPath,pid:process.pid,initial,expected}),{mode:0o600});ready=true;
process.on('SIGTERM',async()=>{await control.close();await host.close();supervisor.close();process.exit(0);});
`;
async function fixture(t,{changeDescriptor,childBody=childCode,terminal=false}={}){
 const root=await mkdtemp(join(tmpdir(),'fresh-')),directory=join(root,'i'),compositionFile=join(root,'composition.json');
 const git=await createHTTPSGitFixture(),assets=new Map();
 const publisher=createServer((q,s)=>{const b=assets.get(q.url);s.writeHead(b?200:404);s.end(b??'missing');});
 await new Promise(r=>publisher.listen(0,'127.0.0.1',r));
 t.after(async()=>{publisher.closeAllConnections();await new Promise(r=>publisher.close(r));await git.close();await rm(root,{recursive:true,force:true});});
 const origin='http://127.0.0.1:'+publisher.address().port;
 const {publicKey,privateKey}=generateKeyPairSync('ed25519');
 const keys={fixture:publicKey.export({type:'spki',format:'pem'})};
 const pkg=join(root,'package'),pythonRoot=join(root,'python'),workspace=join(root,'workspace');
 for(const p of [pkg,pythonRoot,workspace,join(pkg,'src'),join(pkg,'web'),join(pkg,'release-inputs')])await mkdir(p);
 await privateWrite(join(root,'trust.json'),JSON.stringify(keys));
 await privateWrite(join(root,'access-code'),'a'.repeat(64));
 await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(root,'key.pem'),'-out',join(root,'cert.pem'),'-days','1','-subj','/CN=localhost']);
 await chmod(join(root,'key.pem'),0o600);
 await writeFile(join(pythonRoot,'python'),'synthetic unexecuted interpreter',{mode:0o755});
 for(const [folder,file] of [['amplifier_acp','__main__.py'],['amplifier_session_catalog','__main__.py'],['amplifier_unified_media','worker.py']]){
  await mkdir(join(pythonRoot,folder));await writeFile(join(pythonRoot,folder,file),'# qualification fixture');
 }
 const nativeFile=join(root,'native.json'),native={home:join(directory,'native-home'),appHome:join(directory,'native-app'),adminMaintenance:true,adminWorkspaceRoots:[workspace]};
 await privateWrite(nativeFile,JSON.stringify(native));
 const python=join(pythonRoot,'python'),authority={installationId:randomUUID(),ownerId:'fresh-owner',dataScope:'fresh-scope',
  ...Object.fromEntries(Object.entries({sourceDirectory:'source',claimDirectory:'claim',supervisorDirectory:'supervisor',supervisorDiscoveryFile:'supervisor.json',supervisorTokenFile:'supervisor-token',hostDiscoveryFile:'host-control.json',hostTokenFile:'host-token'}).map(([k,v])=>[k,join(directory,v)]))};
 const initial={id:'fresh-v1',version:'1.0.0',revision:git.first};
 const c={schema:'unified-full-owner-fresh-composition-v1',expectedOwners:[...OWNERS],authority,
  review:{status:'approved',combinedLinuxReceiptSha256:'a'.repeat(64),catalogWriterConcurrency:'qualified',nativeModeProjection:'qualified',operationsPortabilityResolver:'qualified'},
  release:{initial,entrypoint:'src/full-owner-launcher.mjs',trustedKeysFile:join(root,'trust.json'),updateOwnerVersion:'0.17.0',updateOwnerRevision:'a'.repeat(40),channelUrl:origin+'/channel.json',accessScope:'fixture',allowedArtifactOrigins:[origin],allowLoopbackHttp:true},
  access:{origin:'https://127.0.0.1:18489',host:'127.0.0.1',port:18489,backendPort:18490,keyFile:join(root,'key.pem'),certFile:join(root,'cert.pem'),codeFile:join(root,'access-code')},
  receiptDirectory:join(directory,'receipts'),sourcePolicy:[{repository:git.repository,ref:'main'}],application:{
   account:'fixture',stateDirectory:join(directory,'application'),manualIngress:{stateDirectory:join(directory,'ingress')},
   webDirectory:join(pkg,'web'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],
   gateway:{host:'127.0.0.1',port:18490,origin:'https://127.0.0.1:18489'},
   ...Object.fromEntries(['workspaces','nativeAdmin','maintenance','applicationUpdates','media','mcp','notifications','diagnostics','operations','coordination','worktrees','publishing','recall','feedback','portability','recovery','historyImport','historyCleanup','managedFiles'].map(k=>[k,{}])),
   nativeAdmin:{engine:'amplifier'},recovery:{authorization:'local-account',credentials:false},conversationPresentation:{},
   portability:{engines:['amplifier']},mcp:{python},media:{python},
   engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeFile]}],
   catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',native.home,'--app-home',native.appHome,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0']},
  }};
 if(terminal)c.application.terminal={origin:c.application.gateway.origin,artifacts:[{id:'fixture-qualified-feed',platform:'linux-arm64'}]};
 await privateWrite(compositionFile,JSON.stringify(c));
 const qualification={schema:'unified-python-runtime-qualification-v1',profile:'native-catalog-media-v1',python:{tree:'runtime',path:'python'},
  launches:[['native','amplifier_acp','__main__.py'],['catalog','amplifier_session_catalog','__main__.py'],['media','amplifier_unified_media.worker','worker.py']].map(([role,module,file])=>({role,module,flags:['-I','-B'],moduleFile:{tree:'runtime',path:module.split('.')[0]+'/'+file},importPaths:[pythonRoot],noRuntimeWrites:true,editableInstalls:false}))};
 const receipt=Buffer.from(JSON.stringify(qualification));
 await writeFile(join(pkg,'release-inputs/qualification.json'),receipt);
 for(const [name,inventory] of [['mcp',inventoryMcpRuntime],['owner',inventoryPythonRuntime]])
  await writeFile(join(pkg,'release-inputs/'+name+'.json'),JSON.stringify(await inventory({trees:[{id:'runtime',root:pythonRoot}],python:{tree:'runtime',path:'python'},qualificationReceiptSha256:hash(receipt)})));
 await writeFile(join(pkg,'release-inputs/native.json'),JSON.stringify({...native,adminGenerations:true,runtimeImmutable:true}));
 const descriptor={schema:'unified-release-runtime-v3',release:initial,baseConfigurationSha256:hash(await readFile(compositionFile)),webDirectory:'web',mcpRuntime:'release-inputs/mcp.json',
  nativeLauncher:{engineId:'amplifier',configuration:'release-inputs/native.json',baseConfigurationSha256:hash(await readFile(nativeFile)),grants:{adminGenerations:true,runtimeImmutable:true},qualificationReceiptSha256:'a'.repeat(64)},
  ownerRuntime:{profile:'native-catalog-media-v1',engineId:'amplifier',manifest:'release-inputs/owner.json',qualificationReceipt:'release-inputs/qualification.json',mediaMode:'installed'},ownerCensus:{profile:'native-message-metadata-v1'}};
 if(terminal)descriptor.ownerCensus={profile:'native-message-terminal-v1'};
 changeDescriptor?.(descriptor);
 await writeFile(join(pkg,'release-runtime.json'),JSON.stringify(descriptor));
 await writeFile(join(pkg,'web/index.html'),'synthetic');
 await writeFile(join(pkg,'package.json'),JSON.stringify({name:'fresh-installer-fixture',version:'1.0.0',type:'module'}));
 await writeFile(join(pkg,'src/full-owner-launcher.mjs'),childBody);
 const files=[];async function inventory(prefix=''){
  for(const name of await readdir(join(pkg,prefix))){const p=join(prefix,name),s=await lstat(join(pkg,p));if(s.isDirectory())await inventory(p);else{const b=await readFile(join(pkg,p));files.push({path:p,sha256:hash(b),bytes:b.length,mode:s.mode&0o777});}}
 }await inventory();files.sort((a,b)=>a.path.localeCompare(b.path));
 await exec('tar',['-czf',join(root,'release.tgz'),'-C',root,'package'],{env:{...process.env,COPYFILE_DISABLE:'1'}});
 const archive=await readFile(join(root,'release.tgz'));
 const release={identity:{...initial,digest:''},artifact:{url:origin+'/release.tgz',sha256:hash(archive),bytes:archive.length},entrypoint:c.release.entrypoint,platform:'any',arch:'any',files,
  components:[{name:'fresh-installer-fixture',version:'1.0.0',root:'',repository:git.repository,ref:'main',revision:git.first}]};
 release.identity.digest=releaseDigest(release);
 const payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+600000,recommendedId:initial.id,releases:[release]}));
 assets.set('/release.tgz',archive);assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')})));
 const input={schema:'unified-full-owner-installation-v1',directory,compositionFile};
 return {root,directory,compositionFile,c,input,git,release,assets};
}
test('fresh installer validates input and signed initial selection before namespace allocation',async t=>{
 const f=await fixture(t),before=await readFile(f.compositionFile);
 for(const mutate of [c=>{c.release.initial.version='9.0.0';},c=>{c.authority.installationId='old';},c=>{c.authority.hostTokenFile=join(f.root,'existing-token');},c=>{c.application.stateDirectory=f.root;},c=>{c.bootstrapRecovery={};},c=>{c.application.gateway.port=80;}]){
  const c=structuredClone(f.c);mutate(c);await privateWrite(f.compositionFile,JSON.stringify(c));
  await assert.rejects(installFullOwnerDistribution(f.input));await assert.rejects(lstat(f.directory),{code:'ENOENT'});
 }
 await privateWrite(f.compositionFile,before);
 const checked=await inspectFullOwnerInstallation(f.input);assert.deepEqual(projection(checked.selected),f.c.release.initial);
 await assert.rejects(lstat(f.directory),{code:'ENOENT'});
 await chmod(f.compositionFile,0o644);await assert.rejects(inspectFullOwnerInstallation(f.input),/private_installation_input_required/);
});
test('changed signed config binding refuses before target allocation or claim',async t=>{
 const f=await fixture(t);f.c.application.account='changed';await privateWrite(f.compositionFile,JSON.stringify(f.c));
 const envBefore={...process.env};Object.assign(process.env,f.git.env);
 try{
  await assert.rejects(installFullOwnerDistribution(f.input),/release_runtime_binding_invalid/);
  await assert.rejects(lstat(f.directory),{code:'ENOENT'});
  const preparation=(await readdir(f.root)).filter(n=>n.startsWith('.full-owner-prepare-'));
  assert.equal(preparation.length,1);
  assert.equal(JSON.parse(await readFile(join(f.root,preparation[0],'preparation.json'))).workReplayed,false);
 }finally{for(const key of Object.keys(process.env))if(!(key in envBefore))delete process.env[key];Object.assign(process.env,envBefore);}
});
for(const terminal of [false,true])test('external CLI consumes genuine first claim with '+(terminal?'Terminal22 descriptor':'message21 descriptor')+' and positional composition argv',async t=>{
 const f=await fixture(t,{terminal}),inputFile=join(f.root,'install.json');await privateWrite(inputFile,JSON.stringify(f.input));
 const child=spawn(process.execPath,[fileURLToPath(new URL('../src/full-owner-install-cli.mjs',import.meta.url)),'--config',inputFile],{env:f.git.env,stdio:['ignore','pipe','pipe']});
 let output='',error='';child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>error+=b);
 t.after(()=>{if(child.exitCode===null)child.kill('SIGTERM');});
 for(let i=0;i<300&&!output.includes('"ready":true')&&child.exitCode===null;i++)await delay(50);
 assert.ok(output.includes('"ready":true'),error||output);
 const authorityBytes=await readFile(join(f.directory,'initial-provisioning.json')),claimBytes=await readFile(join(f.directory,'initial-provisioning.claim'));
 const authority=JSON.parse(authorityBytes),claim=JSON.parse(claimBytes),proof=JSON.parse(await readFile(join(f.directory,'receipts/child.json')));
 assert.equal(authority.installationId,f.c.authority.installationId);assert.deepEqual(authority.initial,f.release.identity);
 assert.equal(claim.targetDigest,f.release.identity.digest);assert.equal(claim.instanceId,proof.expected.instanceId);
 assert.equal(proof.node,process.execPath);assert.deepEqual(proof.argv.slice(2),[f.compositionFile]);assert.notEqual(proof.pid,child.pid);
 const client=await connectSupervisorFile(join(f.directory,'supervisor.json'));
 try{
  await client.owner.setPreferences('fixture-no-background',{autoCheck:false,autoInstall:false,intervalMs:60000});
  const service=await client.service.inspect();
  await client.service.stop({commandId:'fixture-stop',expected:service.identity});
  let receipt;for(let i=0;i<200;i++){receipt=await client.service.receipt('fixture-stop');if(receipt?.status==='stopped')break;await delay(25);}
  assert.equal(receipt?.status,'stopped',JSON.stringify(receipt));
 }finally{client.close();}
 child.kill('SIGTERM');for(let i=0;i<100&&child.exitCode===null;i++)await delay(20);
 assert.equal(child.exitCode,0,error);
 await assert.rejects(installFullOwnerDistribution(f.input),/installation_already_exists/);
 assert.deepEqual(await readFile(join(f.directory,'initial-provisioning.json')),authorityBytes);
 assert.deepEqual(await readFile(join(f.directory,'initial-provisioning.claim')),claimBytes);
});


test('signed but incomplete owner descriptor refuses before installation allocation',async t=>{
 for(const changeDescriptor of [d=>{delete d.ownerCensus;},d=>{delete d.ownerRuntime;},d=>{d.schema='unified-release-runtime-v2';}]){
  const f=await fixture(t,{changeDescriptor}),before={...process.env};Object.assign(process.env,f.git.env);
  try{await assert.rejects(installFullOwnerDistribution(f.input),/release_runtime_binding_invalid/);await assert.rejects(lstat(f.directory),{code:'ENOENT'});}
  finally{for(const k of Object.keys(process.env))if(!(k in before))delete process.env[k];Object.assign(process.env,before);}
 }
});


test('failed initial child retains consumed claim and unknown attempt instead of replaying',async t=>{
 const f=await fixture(t,{childBody:"throw Error('synthetic_startup_failure');"}),before={...process.env};Object.assign(process.env,f.git.env);
 try{
  await assert.rejects(installFullOwnerDistribution(f.input));
  const claim=await readFile(join(f.directory,'initial-provisioning.claim'));
  const attempt=JSON.parse(await readFile(join(f.directory,'installer-attempt.json')));
  assert.equal(attempt.status,'unknown');assert.equal(attempt.phase,'launch_requested');assert.equal(attempt.workReplayed,false);
  await assert.rejects(installFullOwnerDistribution(f.input),/installation_already_exists/);
  assert.deepEqual(await readFile(join(f.directory,'initial-provisioning.claim')),claim);
 }finally{for(const k of Object.keys(process.env))if(!(k in before))delete process.env[k];Object.assign(process.env,before);}
});


test('configured Terminal refuses old or missing signed profile before pristine allocation',async t=>{
 for(const changeDescriptor of [d=>{d.ownerCensus={profile:'native-message-metadata-v1'};},d=>{delete d.ownerCensus;},d=>{d.ownerCensus.owners=['terminal'];}]){
  const f=await fixture(t,{terminal:true,changeDescriptor}),before={...process.env};Object.assign(process.env,f.git.env);
  try{await assert.rejects(installFullOwnerDistribution(f.input),/release_runtime_binding_invalid/);await assert.rejects(lstat(f.directory),{code:'ENOENT'});}
  finally{for(const k of Object.keys(process.env))if(!(k in before))delete process.env[k];Object.assign(process.env,before);}
 }
});
