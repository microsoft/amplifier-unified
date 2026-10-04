import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const {createMaintenanceCapabilities}=await import(process.env.MAINTENANCE_ENTRY||'../dist/index.js');
const refusal=()=>Object.assign(Error('Native runtime updates unavailable'),{data:{reason:'native-generations-unavailable',executed:false,replayed:false}});
const request=(operation,commandId='original',args={})=>({version:1,channel:'ahp-root://',topic:'maintenance',operation,commandId,args});
const context={clientId:'reviewer'};

test('only the exact disabled-generation proof settles a mutation before effects',async()=>{
 for(const [operation,args] of [['updates.check',{}],['updates.install',{}],['updates.rollback',{expectedCurrent:null}],['updates.runtime.repair',{generation:'old',expectedSourceHash:'a'.repeat(64),expectedCurrent:null}],['updates.runtime.select',{generation:'new',expectedCurrent:null}]]){
  let calls=0,invalidations=0;const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async()=>{calls++;throw refusal()},onInvalidate:()=>{invalidations++}});
  const result=await provider.action(request(operation,'exact',args),context);
  assert.deepEqual(result,{accepted:false,result:{commandId:'exact',operation,state:'failed',executed:false,applied:false,replayed:false,reason:'native-generations-unavailable',message:'Native runtime updates are turned off for this installation. No update was started.'},updates:[],invalidate:[],_meta:{'amplifier.dev/operation':{id:'exact'}}});
  assert.equal(calls,1);assert.equal(invalidations,0);await provider.close();
 }
});

test('a refusal after passive install inspection still establishes no preparation',async()=>{
 const calls=[];const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async op=>{calls.push(op);if(op==='generations.inspect')return {pointer:{current:'old'}};throw refusal();}});
 const result=await provider.action(request('updates.install'),context);assert.equal(result.accepted,false);assert.deepEqual(calls,['generations.inspect','generations.prepare']);
});

test('nearby, conflicting and transport errors retain uncertainty',async()=>{
 for(const data of [undefined,{executed:false},{executed:false,replayed:false,reason:'other'},{executed:false,reason:'native-generations-unavailable'},{executed:true,replayed:false,reason:'native-generations-unavailable'},{executed:false,replayed:false,reason:'native-generations-unavailable',outcome:'unknown'},{executed:false,replayed:false,reason:'native-generations-unavailable',applied:true}]){
  const error=Object.assign(Error('Original uncertain result'),{data});
  const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async()=>{throw error}});
  await assert.rejects(provider.action(request('updates.check'),context),value=>value===error);
 }
});

test('successful preparation followed by promotion refusal remains uncertain for the outer install',async()=>{
 const calls=[],error=refusal();const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async(op,args)=>{calls.push([op,args.commandId]);if(op==='generations.inspect')return {pointer:{current:'old'}};if(op==='generations.prepare')return {state:'succeeded',result:{generation:'prepared'}};throw error;}});
 await assert.rejects(provider.action(request('updates.install'),context),value=>value===error);
 assert.deepEqual(calls,[['generations.inspect',undefined],['generations.prepare','original:prepare'],['generations.promote','original:promote']]);
});

test('passive receipt refusal and post-effect listener failure are not original-command refusals',async()=>{
 const error=refusal();const reads=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async()=>{throw error}});
 await assert.rejects(reads.action(request('updates.runtime.receipt','inspection',{commandId:'old-unknown'}),context),value=>value===error);
 await assert.rejects(reads.action(request('updates.runtime.inspect'),context),value=>value===error);
 const post=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async()=>({state:'succeeded'}),onInvalidate:()=>{throw error}});
 await assert.rejects(post.action(request('updates.check'),context),value=>value===error);
});

test('installed bridge and native disabled grant produce a durable failed public host receipt without a worker',{
 skip:![process.env.MAINTENANCE_HOST_ENTRY,process.env.MAINTENANCE_BRIDGE_ENTRY,process.env.NATIVE_ACP_PYTHON].every(Boolean),
},async t=>{
 const hostEntry=pathToFileURL(process.env.MAINTENANCE_HOST_ENTRY),bridgeEntry=pathToFileURL(process.env.MAINTENANCE_BRIDGE_ENTRY);
 const {createHost}=await import(hostEntry);const {AdminConnection}=await import(bridgeEntry);
 // Follow the installed package's public import exports; createRequire cannot
 // resolve AHP's import-only subpaths under its require condition.
 const require=createRequire(hostEntry);let protocol;
 for(const directory of require.resolve.paths('@microsoft/agent-host-protocol')){
  const root=join(directory,'@microsoft/agent-host-protocol');
  try{protocol={root,manifest:JSON.parse(await readFile(join(root,'package.json'),'utf8'))};break}catch(error){if(error.code!=='ENOENT')throw error}
 }
 assert.ok(protocol);const publicImport=subpath=>import(pathToFileURL(join(protocol.root,protocol.manifest.exports[subpath].import)));
 const {AhpClient}=await publicImport('./client');const {WebSocketTransport}=await publicImport('./ws');
 const directory=await mkdtemp(join(tmpdir(),'maintenance-preflight-')),workspace=join(directory,'workspace'),config=join(directory,'native.json');
 await mkdir(workspace);await writeFile(config,JSON.stringify({home:join(directory,'home'),appHome:join(directory,'app'),adminWorkspaceRoots:[workspace],workerCommand:['/must/not/start']}));
 const admin=new AdminConnection({command:process.env.NATIVE_ACP_PYTHON,args:['-I','-B','-m','amplifier_acp','--config',config],cwd:workspace,resolveWorkspace:()=>workspace,initializationTimeoutMs:10000});
 const calls=[];const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:(operation,args,context)=>{calls.push(operation);return admin.perform(operation,args,context)}});
 const configuration={stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[workspace],engines:[{id:'unused',command:'/must/not/start'}],capabilities:provider};
 let host,client,passed=false;
 const open=async()=>{host=await createHost(configuration);client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'reviewer',protocolVersions:['0.9.0']});};
 const receipt=commandId=>client.request('x-commandReceipt',{channel:'ahp-root://',commandId});
 try{
  assert.equal(await admin.generationsCapabilities(),undefined);await open();
  let result;
  try{result=await client.request('x-amplifier/capabilityAction',request('updates.check'))}
  catch(error){t.diagnostic(JSON.stringify({originalHostReceipt:await receipt('original'),calls,activeAgents:host.diagnostics().activeAgents}));throw error;}
  assert.equal(result.accepted,false);assert.equal(result.result.executed,false);assert.equal(result.result.reason,'native-generations-unavailable');assert.equal((await receipt('original')).status,'failed');
  await assert.rejects(client.request('x-amplifier/capabilityAction',request('updates.check')),/already admitted/);assert.deepEqual(calls,['generations.check']);
  await assert.rejects(client.request('x-amplifier/capabilityAction',request('updates.runtime.receipt','inspect-original',{commandId:'original'})));
  assert.equal((await receipt('original')).status,'failed');assert.equal(host.diagnostics().activeAgents,0);
  await client.shutdown();client=null;await host.close();host=null;const count=calls.length;await open();
  assert.equal((await receipt('original')).status,'failed');assert.equal(calls.length,count);assert.equal(host.diagnostics().activeAgents,0);
  passed=true;
 }finally{await client?.shutdown();await host?.close();await admin.close();await provider.close();if(passed)await rm(directory,{recursive:true,force:true});else t.diagnostic('Retained failed fixture: '+directory);}
});
