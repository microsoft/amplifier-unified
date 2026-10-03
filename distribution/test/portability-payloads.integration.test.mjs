import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,copyFile,cp,rm,realpath,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
const required=['PORTABILITY_MODULE','PORTABILITY_RESOURCES_MODULE','PORTABILITY_HOST_MODULE','PORTABILITY_PYTHON','AMPLIFIER_ACP_PYTHON','PORTABILITY_NATIVE_FIXTURE'];
const available=required.every(name=>process.env[name]);
const assembled=Boolean(process.env.PORTABILITY_DISTRIBUTION_MODULE);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const git=(directory,...args)=>execFileSync('git',['-c','core.hooksPath=/dev/null','-C',directory,...args],{encoding:'utf8'}).trim();
const setup=String.raw`import importlib.util,json,sys,yaml
from pathlib import Path
p=Path(sys.argv[1]);home=p/'home';home.mkdir();provider=p/'provider';provider.mkdir();module=provider/'amplifier_module_provider_transfer_fixture';module.mkdir()
(module/'__init__.py').write_bytes(Path(sys.argv[2]).read_bytes());(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-transfer-fixture"\nversion="0.1.0"\n')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
config={'reasoning_effort':'low','readinessAudit':str(p/'probe.txt'),'normalAudit':str(p/'normal.txt')}
bundle=p/'bundle.yaml';bundle.write_text(yaml.safe_dump({'bundle':{'name':'transfer-fixture','version':'1.0.0'},'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple','source':str(context)}},'providers':[{'module':'provider-transfer-fixture','source':str(provider),'config':config}]}))
(home/'settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':'transfer-fixture','app':[]},'config':{'providers':[{'id':'fixture','module':'provider-transfer-fixture','source':str(provider),'config':config}]}}))
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'transferAuthorityDirectory':str(p/'owner'),'transferWorkspaceRoots':[str(p)],'transferProbeCommand':[sys.executable,'-I','-m','amplifier_acp.native.portability_probe']}))
`;

test(assembled?'actual assembled distributions authenticate detached history and recover without replay':'actual installed native hosts authenticate signed detached bodies and import only inactive history', {skip:!available,timeout:240000},async()=>{
 const {createPortabilityCapabilities,TransferConnection}=await import(process.env.PORTABILITY_MODULE);
 const {createResourcesCapability}=await import(process.env.PORTABILITY_RESOURCES_MODULE);
 const {createHost}=await import(process.env.PORTABILITY_HOST_MODULE);
 const {AhpClient}=await import(new URL('../../../@microsoft/agent-host-protocol/dist/client/index.js',pathToFileURL(process.env.PORTABILITY_HOST_MODULE)).href);
 const {WebSocketTransport}=await import(new URL('../../../@microsoft/agent-host-protocol/dist/ws/index.js',pathToFileURL(process.env.PORTABILITY_HOST_MODULE)).href);
 const directory=await realpath(await mkdtemp(join(tmpdir(),'signed-detached-native-'))),sides=[];
 const faults=new Map(),originalRpc=TransferConnection.prototype.rpc;
 // Test-only loss of one already-completed passive reply. Production composition,
 // signing, source reads, verifier closures and resource ingestion remain intact.
 if(assembled)TransferConnection.prototype.rpc=async function(method,params){const state=[...faults.values()].find(v=>this.launcher.args?.includes(join(v.root,'native.json')));if(method==='_amplifier/transfer'&&state)state.effects.push(params.operation);const result=await originalRpc.call(this,method,params);if(method==='_amplifier/transfer'&&params.operation==='inspect'&&state?.failInspect){state.failInspect=false;throw Error('lost passive inspect response');}return result;};
 async function side(name){
  const root=join(directory,name);await mkdir(root);const work=join(root,'workspace');await mkdir(work);
  const python=name==='target'?(process.env.TARGET_ACP_PYTHON??process.env.AMPLIFIER_ACP_PYTHON):process.env.AMPLIFIER_ACP_PYTHON;
  const ownerPython=name==='target'?(process.env.TARGET_OWNER_PYTHON??process.env.PORTABILITY_PYTHON):process.env.PORTABILITY_PYTHON;
  execFileSync(python,['-I','-c',setup,root,process.env.PORTABILITY_NATIVE_FIXTURE]);
  const config=join(root,'owner.json');await writeFile(config,JSON.stringify({dataDir:join(root,'owner'),exchangeDir:join(root,'exchange'),stageDir:join(root,'stages'),workspaceRoots:[root],label:name}));
  const launcher={command:python,args:['-I','-m','amplifier_acp','--config',join(root,'native.json')],cwd:root,env:{PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_SESSION_STATE_HOME:join(root,'writers')}};
  if(assembled){
   const {createDistribution}=await import(process.env.PORTABILITY_DISTRIBUTION_MODULE);
   const stateDirectory=join(root,'distribution'),ownerDirectory=join(stateDirectory,'capabilities/portability');
   const nativeConfig=JSON.parse(await readFile(join(root,'native.json')));nativeConfig.transferAuthorityDirectory=ownerDirectory;await writeFile(join(root,'native.json'),JSON.stringify(nativeConfig));
   const webDirectory=join(root,'web');await mkdir(webDirectory);await writeFile(join(webDirectory,'index.html'),'<html><head></head><body>Owned composed transfer fixture</body></html>');
   const state={root,effects:[],failInspect:false};faults.set(root,state);
   const distribution=await createDistribution({account:'detached-fixture',stateDirectory,webDirectory,defaultWorkspace:work,allowedWorkspaceRoots:[root],engines:[{id:'native',...launcher}],portability:{python:ownerPython,engines:['native'],stageDir:join(root,'stages'),exchangeDir:join(root,'exchange'),resourcePayloads:true}});
   const {host,resources}=distribution;
   const adopt=host.adoptTransferredSession;host.adoptTransferredSession=(...args)=>{state.effects.push('adopt');return adopt.apply(host,args);};
   const activate=resources.activateTransferEvidence;resources.activateTransferEvidence=(...args)=>{state.effects.push('import');return activate.apply(resources,args);};
   const client=new AhpClient(await WebSocketTransport.connect(distribution.url.replace(/^http/,'ws')+'/ahp'));client.connect();await client.initialize({clientId:'fixture',protocolVersions:['0.9.0']});
   const act=(operation,args={},scope='ahp-root://',commandId=randomUUID())=>client.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'portability',operation:'portability.'+operation,version:1,args,commandId}).then(v=>v.result);
   const info=await act('inspect');assert.ok(info.payloadCapabilities);
   const value={root,ownerDirectory,work,python,ownerPython,host,resources,client,act,info,effects:state.effects,interruptInspect:()=>{state.failInspect=true;},close:()=>distribution.close()};sides.push(value);return value;
  }
  const effects=[];let failInspect=false;let host,cap;const native=new TransferConnection(launcher);
  // Fixed trusted composition closure; no public mutable verifier registration.
  const resources=createResourcesCapability({directory:join(root,'resources'),inspectSession:uri=>host.inspectSession(uri),verifyTransferPayloadPlan:args=>cap.verifyTransferPayloadPlan(args)});
  cap=createPortabilityCapabilities({owner:{command:ownerPython,args:['-I','-m','amplifier_unified_portability.server','--config',config],cwd:root,env:{PYTHONDONTWRITEBYTECODE:'1'}},inspectSession:uri=>host.inspectSession(uri),beginTransfer:(uri,a)=>host.beginTransfer(uri,{...a,commandId:'portability:'+a.commandId+':begin'}),commitTransfer:(uri,a)=>host.commitTransfer(uri,{...a,commandId:'portability:'+a.commandId+':commit'}),cancelTransfer:(uri,a)=>host.cancelTransfer(uri,{...a,commandId:'portability:'+a.commandId+':cancel'}),adoptTransferredSession:a=>{effects.push('adopt');return host.adoptTransferredSession({...a,commandId:'portability:'+a.commandId+':adopt'});},nativeTransfer:args=>{effects.push(args.operation);if(args.operation==='inspect'&&failInspect){failInspect=false;throw Error('lost passive inspect response');}return native.perform(args);},
   exportTransferEvidence:async args=>({evidence:[await resources.exportTransferEvidence(args)],omissions:[]}),stageTransferEvidence:async args=>({receipts:await Promise.all(args.evidence.map(evidence=>resources.stageTransferEvidence({...args,evidence})))}),activateTransferEvidence:async args=>{effects.push('import');return {receipts:await Promise.all(args.evidence.map(evidence=>resources.activateTransferEvidence({...args,evidenceHash:evidence.sha256})))};} ,
   resourcePayloads:{metadata:args=>resources.readTransferAttachmentMetadata(args),readSource:async args=>{const provider=resources.resourceProviders.find(p=>p.scheme==='amplifier-attachment'),uri=new URL(args.resourceUri);assert.equal(uri.searchParams.get('session'),args.session);assert.equal(uri.hostname,args.resourceId);const descriptor=await provider.resolve({uri:uri.href});assert.equal(descriptor.size,args.size);assert.equal(descriptor.etag,'"sha256:'+args.sha256+'"');uri.searchParams.set('offset',String(args.offset));uri.searchParams.set('limit',String(args.maxBytes));return provider.read({uri:uri.href,encoding:'base64'});},stage:args=>resources.stageTransferPayloads(args)}});
  const act=(operation,args={},scope='ahp-root://',commandId=randomUUID())=>cap.action({channel:scope,topic:'portability',operation:'portability.'+operation,version:1,args,commandId},{clientId:'fixture',origin:'ui'}).then(v=>v.result);
  const info=await act('inspect');
  const capabilities={manifest:{version:1,topics:{...resources.manifest.topics,...cap.manifest.topics},actions:{...resources.manifest.actions,...cap.manifest.actions}},read:request=>(request.topic==='portability'?cap:resources).read(request),action:(request,context)=>(request.topic==='portability'?cap:resources).action(request,context),getActionSchemas:async()=>({...resources.actionSchemas,...await cap.actionSchemas()})};
  host=await createHost({stateDirectory:join(root,'host'),engines:[{id:'native',...launcher}],allowedWorkspaceRoots:[root],transferIdentity:info.host.id,capabilities,resourceProviders:resources.resourceProviders});
  const client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'fixture',protocolVersions:['0.9.0']});
  const value={root,ownerDirectory:join(root,'owner'),work,python,ownerPython,host,resources,cap,native,client,act,info,effects,interruptInspect:()=>{failInspect=true;},close:async()=>{await cap.close();await native.close();await host.close();resources.close();}};sides.push(value);return value;
 }
 try{
  const a=await side('source'),b=await side('target');git(a.work,'init','-q');git(a.work,'config','user.email','fixture@example.invalid');git(a.work,'config','user.name','Owned transfer fixture');await writeFile(join(a.work,'retained.txt'),'preserved source\n');git(a.work,'add','.');git(a.work,'commit','-qm','fixture');git(b.work,'clone',a.work,'.');
  const pairedA={...a.info.host,payloadCapabilities:a.info.payloadCapabilities},pairedB={...b.info.host,payloadCapabilities:b.info.payloadCapabilities};
  execFileSync(a.ownerPython,['-I','-c',"import json,sys;from pathlib import Path;from amplifier_worktrees.git import atomic;a,b=map(Path,sys.argv[1:3]);x,y=map(json.loads,sys.argv[3:]);atomic(a/'peers.json',{y['id']:y});atomic(b/'peers.json',{x['id']:x})",a.ownerDirectory,b.ownerDirectory,JSON.stringify(pairedA),JSON.stringify(pairedB)]);
  const session='ahp-session:/'+randomUUID();await a.client.request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(a.work).href]});await a.host.nativeControl(session,'provider.select',{provider:'fixture',model:'fixture-model',effort:'low'});await a.host.submitTurn(session,{commandId:'original',clientId:'fixture',text:'Preserve this accepted input without replay.'});assert.equal((await a.host.waitForTurn(session,'original',30000)).status,'completed');
  const before=await a.host.inspectSession(session),metadata=JSON.parse(execFileSync(a.python,['-I','-c',"import json,sys;from pathlib import Path;p=Path(sys.argv[1]);n=next((p/'home/projects').glob('*/sessions/'+sys.argv[2]));m=json.loads((n/'metadata.json').read_text());print(json.dumps({'path':str(n),'bundle':m.get('bundle') or m.get('bundle_name')}))",a.root,before.nativeSessionId],{encoding:'utf8'})),original=await readFile(join(metadata.path,'transcript.jsonl')),normal=await readFile(join(a.root,'normal.txt'));
  execFileSync(b.python,['-I','-c',"import sys,yaml;from pathlib import Path;p=Path(sys.argv[1]);v=yaml.safe_load(p.read_text());v['bundle']['active']=sys.argv[2];p.write_text(yaml.safe_dump(v))",join(b.root,'home/settings.yaml'),metadata.bundle]);
  const raw=Buffer.alloc(9*1024*1024,0x5a),sha=hash(raw);
  const create=await a.client.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',operation:'attachments.create',version:1,args:{requestId:'large',name:'large.bin',contentType:'application/octet-stream',size:raw.length,sha256:sha},commandId:'large-create'});const attachment=create.result.attachment,provider=a.resources.resourceProviders.find(p=>p.scheme==='amplifier-attachment');
  for(let offset=0;offset<raw.length;offset+=262144){const uri=new URL(attachment.uploadUri);uri.searchParams.set('offset',String(offset));await provider.write({uri:uri.href,encoding:'base64',mode:'append',data:raw.subarray(offset,offset+262144).toString('base64')});}
  await a.client.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',operation:'attachments.commit',version:1,args:{id:attachment.id,requestId:'large-commit'},commandId:'commit-large'});
  const duplicateCreated=await a.client.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',operation:'attachments.create',version:1,args:{requestId:'duplicate',name:'duplicate.bin',contentType:'application/octet-stream',size:raw.length,sha256:sha},commandId:'duplicate-create'}),duplicate=duplicateCreated.result.attachment;
  for(let offset=0;offset<raw.length;offset+=262144){const uri=new URL(duplicate.uploadUri);uri.searchParams.set('offset',String(offset));await provider.write({uri:uri.href,encoding:'base64',mode:'append',data:raw.subarray(offset,offset+262144).toString('base64')});}
  await a.client.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',operation:'attachments.commit',version:1,args:{id:duplicate.id,requestId:'duplicate-commit'},commandId:'commit-duplicate'});
  await a.client.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',operation:'attachments.create',version:1,args:{requestId:'unfinished',name:'unfinished.bin',contentType:'application/octet-stream',size:1,sha256:hash(Buffer.from('x'))},commandId:'unfinished-create'});
  const inspected=await a.act('inspect',{sessionId:session},session),out=await a.act('export',{sessionId:session,destination:b.info.host.id,sourceRevision:inspected.source.sourceRevision,expectedExecutionRevision:before.executionRevision,mode:'clean',reviewedContent:true,includeResourcePayloads:true},session,'payload-export');
  assert.equal(out.review.resourcePayload.bytes,2*raw.length);assert.ok(out.review.sourceSelection.omissions.length);assert.deepEqual(new Set(out.review.sourceSelection.items.map(item=>item.id)),new Set([attachment.id,duplicate.id]));
  const capsule=join(b.root,'exchange/incoming.json');await copyFile(out.package,capsule);const args={path:capsule,repository:b.work,reviewedCapsuleHash:out.review.capsuleHash};await assert.rejects(b.act('stage',args),/sidecar/);assert.equal(b.host.diagnostics().activeAgents,0);
  const sidecar=join(b.root,'exchange/incoming.payloads');await cp(out.payloadDirectory,sidecar,{recursive:true});const incoming=await b.act('stage',{...args,payloadDirectory:sidecar},'ahp-root://','payload-stage');assert.equal(incoming.phase,'ready');
  const resource=out.review.evidence.find(e=>e.owner==='unified.resources'),selection={session,transferId:incoming.id,evidenceHash:resource.sha256,planHash:out.review.resourcePayload.planHash};await assert.rejects(b.resources.readTransferPayloadMetadata(selection),/unavailable|Unknown resource/i);
  await assert.rejects(b.resources.activateTransferEvidence({...selection,releaseHash:'0'.repeat(64),payloadPlanHash:'0'.repeat(64)}),/plan/i);
  const ready=join(a.root,'exchange/ready.json');await copyFile(incoming.receiptPath,ready);const released=await a.act('release',{sessionId:session,id:out.id,expectedRevision:out.revision,path:ready,reviewedCapsuleHash:out.review.capsuleHash},session,'payload-release');const certificate=join(b.root,'exchange/release.json');await copyFile(released.receiptPath,certificate);
  b.interruptInspect();await assert.rejects(b.act('activate',{id:incoming.id,expectedRevision:incoming.revision,path:certificate},'ahp-root://','payload-activate'),/lost passive inspect/);const retained=join(b.ownerDirectory,'packages',incoming.id+'.json'),valid=await readFile(retained);const changed=JSON.parse(valid);changed.body.payload.originSession='ahp-session:///unsigned-forged';await writeFile(retained,JSON.stringify(changed));const effects=b.effects.length;await assert.rejects(b.act('reconcile',{id:incoming.id},'ahp-root://','tampered-reconcile'),/authentic/);assert.equal(b.effects.length,effects);await writeFile(retained,valid);const activated=await b.act('reconcile',{id:incoming.id},'ahp-root://','unchanged-reconcile');assert.equal(b.effects.filter(v=>v==='destination.install').length,1);assert.equal(b.effects.filter(v=>v==='destination.activate').length,1);assert.equal(activated.phase,'active');assert.equal(b.host.diagnostics().activeAgents,0);
  const page=await b.resources.readTransferPayloadMetadata(selection);assert.equal(page.items.length,2);assert.equal(page.items[0].sha256,sha);assert.equal(page.items[1].sha256,sha);assert.equal(page.items[0].executionAuthority,false);let offset=0;const whole=createHash('sha256');do{const value=await b.resources.readTransferPayloadBody({...selection,resourceId:attachment.id,offset,maxBytes:262144});whole.update(Buffer.from(value.data,'base64'));offset=value.nextOffset;}while(offset!==null);assert.equal(whole.digest('hex'),sha);
  assert.equal(b.resources.store.list(session).items.length,0);assert.deepEqual(await readFile(join(metadata.path,'transcript.jsonl')),original);assert.deepEqual(execFileSync(b.python,['-I','-c',"import sys;from pathlib import Path;n=next((Path(sys.argv[1])/'home/projects').glob('*/sessions/'+sys.argv[2]));sys.stdout.buffer.write((n/'transcript.jsonl').read_bytes())",b.root,before.nativeSessionId]),original);assert.deepEqual(await readFile(join(a.root,'normal.txt')),normal);await assert.rejects(readFile(join(b.root,'normal.txt')),/ENOENT/);await assert.rejects(a.host.submitTurn(session,{commandId:'source-replay',clientId:'fixture',text:'must remain fenced'}),/fenced|released|transfer/);
  const sourceBody=await provider.read({uri:attachment.resourceUri,encoding:'base64'});assert.equal(hash(Buffer.from(sourceBody.data,'base64')),hash(raw.subarray(0,262144)));
 }catch(error){if(process.env.PORTABILITY_FAILURE_DIRECTORY)await cp(directory,process.env.PORTABILITY_FAILURE_DIRECTORY,{recursive:true});throw error;}finally{for(const s of sides.toReversed()){await s.client.shutdown();await s.close();}if(assembled)TransferConnection.prototype.rpc=originalRpc;await rm(directory,{recursive:true,force:true});}
});
