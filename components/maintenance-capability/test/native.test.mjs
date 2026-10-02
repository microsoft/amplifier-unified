import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,rm,mkdir} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {spawn} from 'node:child_process';import {Readable,Writable} from 'node:stream';import {ClientSideConnection,ndJsonStream} from '@agentclientprotocol/sdk';import {createMaintenanceCapabilities} from '../dist/index.js';
test('public owner consumes real ACP admin without starting session runtime',async()=>{
 const root=await mkdtemp(join(tmpdir(),'native-maint-'));const home=join(root,'home'),appHome=join(root,'app'),workspace=join(root,'workspace');await mkdir(workspace);await mkdir(home);const manifest=join(root,'runtime.toml');await writeFile(manifest,'[project]\nname="empty-qualified-fixture"\nversion="0.1.0"\nrequires-python=">=3.13"\ndependencies=[]\n');
 const config=join(root,'config.json');await writeFile(config,JSON.stringify({home,appHome,adminWorkspaceRoots:[workspace],adminGenerations:true,runtimeManifest:manifest,workerCommand:['/must/not/start']}));
 const child=spawn(process.env.NATIVE_ACP_PYTHON||resolve('../../../../repos/amplifier-agent-acp/.venv/bin/python'),['-m','amplifier_acp','--config',config],{stdio:'pipe'});child.stderr.on('data',()=>{});
 const client=new ClientSideConnection(()=>({sessionUpdate:async()=>{throw Error('Unexpected session execution');},requestPermission:async()=>({outcome:{outcome:'cancelled'}})}),ndJsonStream(Writable.toWeb(child.stdin),Readable.toWeb(child.stdout)));
 try{
  await client.initialize({protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}});
  const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:(operation,args)=>client.extMethod('_amplifier/admin',{cwd:workspace,operation,args})});
  const read=await provider.read({topic:'maintenance',scope:'host',clientId:'test',uri:provider.manifest.topics.maintenance.uri+'?scope=host'});assert.equal(read.data.updates.scope,'native-runtime');
  const action=await provider.action({version:1,topic:'maintenance',channel:'ahp-root://',operation:'updates.check',commandId:'check1',args:{}},{clientId:'test'});assert.equal(action.result.state,'succeeded');assert.equal(action.result.result.available,1);
  const receipt=await provider.action({version:1,topic:'maintenance',channel:'ahp-root://',operation:'updates.runtime.receipt',commandId:'read',args:{commandId:'check1'}},{clientId:'test'});assert.equal(receipt.result.receipts[0].state,'succeeded');
  await provider.close();
 }finally{child.stdin.end();await new Promise(resolve=>child.once('exit',resolve));await rm(root,{recursive:true,force:true});}
});
