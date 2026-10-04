import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,stat,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {composeMCP} from '../src/mcp.js';
import {createDistribution} from '../src/index.js';
import {inspectConfig,validMCPInstallerConfiguration} from '../src/validate-config.mjs';

const invalid=[null,undefined,{},[],true,'/reviewed/uv',
 {executable:'uv'},{executable:'./uv'},{executable:'~/bin/uv'},
 {executable:''},{executable:42},{executable:'/a'.repeat(2049)},
 {executable:'/reviewed/uv\n'},{executable:'/reviewed/uv\0'},
 {executable:'/reviewed/uv\x7f'},{executable:'/reviewed/uv\x85'},
 {executable:'/reviewed/uv',args:[]},{executable:'/reviewed/uv',env:{}}];
const origin='https://client.example:18473';
async function fixture(t,config){
 const directory=await mkdtemp(join(tmpdir(),'mcp-installer-composition-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const owner=composeMCP(typeof config==='function'?config(directory):config,{directory,inspectSession:async()=>({workingDirectory:directory}),registerExternal:async(_context,value)=>value});
 t.after(()=>owner.close());
 return {directory,owner,launch:join(directory,'mcp-launch.json')};
}

test('trusted installer is copied exactly into the private broker launcher',async t=>{
 const installer={executable:'/reviewed/tools with spaces/uv'},f=await fixture(t,{python:'/reviewed/python',installer});
 // Keep the admitted configuration stable while the gateway obtains its origin.
 installer.executable='/changed/after-composition';
 await f.owner.initializeOrigin(origin);
 const launch=JSON.parse(await readFile(f.launch,'utf8'));
 assert.deepEqual(launch,{dataDir:join(f.directory,'mcp'),server:{port:18473,public_origins:[origin]},installer:{executable:'/reviewed/tools with spaces/uv'}});
 assert.equal((await stat(f.launch)).mode&0o777,0o600);
});

test('omitted installer preserves the existing generated launcher',async t=>{
 const f=await fixture(t,{python:'/reviewed/python'});await f.owner.initializeOrigin(origin);
 assert.deepEqual(Object.keys(JSON.parse(await readFile(f.launch,'utf8'))).sort(),['dataDir','server']);
 const inherited=await fixture(t,Object.assign(Object.create({installer:{executable:'not-local-authority'}}),{python:'/reviewed/python'}));
 await inherited.owner.initializeOrigin(origin);
 assert.equal(Object.hasOwn(JSON.parse(await readFile(inherited.launch,'utf8')),'installer'),false);
});

test('external brokers retain their own configuration and reject a local installer',async t=>{
 const broker={request:async()=>{throw Error('must not connect');},close:async()=>{}},f=await fixture(t,{broker});
 await f.owner.initializeOrigin(origin);await assert.rejects(stat(f.launch),{code:'ENOENT'});
 assert.equal(validMCPInstallerConfiguration({broker,installer:{executable:'/reviewed/uv'}}),false);
 assert.throws(()=>composeMCP({broker,installer:{executable:'/reviewed/uv'}},{directory:f.directory}),/mcp_installer_configuration_invalid/);
});

test('invalid installer authority is refused by preflight and composition before effects',async()=>{
 for(const installer of invalid){
  const mcp={installer};
  assert.equal(validMCPInstallerConfiguration(mcp),false);
  assert.ok(inspectConfig({application:{mcp}}).issues.includes('mcp-installer-config'));
  assert.throws(()=>composeMCP(mcp,{}),/mcp_installer_configuration_invalid/);
  await assert.rejects(createDistribution({stateDirectory:'/not-created',webDirectory:'/not-created',defaultWorkspace:'/not-created',mcp}),/mcp_installer_configuration_invalid/);
 }
 assert.equal(validMCPInstallerConfiguration({installer:{executable:'/reviewed/uv'}}),true);
 assert.equal(validMCPInstallerConfiguration({}),true);
 assert.ok(!inspectConfig({application:{mcp:{}}}).issues.includes('mcp-installer-config'));
});

test('installed broker obeys the generated installer and rejects client overrides',{
 skip:!process.env.MCP_INSTALLER_BROKER_COMMAND,
},async t=>{
 const f=await fixture(t,directory=>({command:process.env.MCP_INSTALLER_BROKER_COMMAND,env:{PATH:join(directory,'ambient-bin')},installer:{executable:join(directory,'missing-uv')}}));
 const bin=join(f.directory,'ambient-bin'),uv=join(bin,'uv');await mkdir(bin);
 await writeFile(uv,'#!/bin/sh\nexit 91\n',{mode:0o700});
 // A different available uv on PATH cannot replace the explicit missing path.
 // This only adjusts the fixture subprocess environment, never the parent PATH.
 await f.owner.initializeOrigin(origin);
 const action=(args,owner=f.owner)=>owner.action({channel:'ahp-root://',topic:'connectors',version:1,operation:'smartTools.installerReadiness',args,commandId:randomUUID()},{clientId:'fixture-client',origin:'ui'});
 const missing=await action({});assert.equal(missing.result.status,'unavailable');assert.equal(missing.result.source,'configured');
 await assert.rejects(action({installer:{executable:uv}}));
 await assert.rejects(action({executable:uv}));
 assert.deepEqual((await action({})).result,missing.result);
 assert.equal(JSON.parse(await readFile(f.launch,'utf8')).installer.executable,join(f.directory,'missing-uv'));
 const ready=await fixture(t,{command:process.env.MCP_INSTALLER_BROKER_COMMAND,installer:{executable:uv}});
 await ready.owner.initializeOrigin(origin);
 assert.equal((await action({},ready.owner)).result.status,'available');
});
