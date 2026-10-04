import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {once} from 'node:events';import {randomUUID} from 'node:crypto';import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const current=process.env.AMPLIFIER_ACP_PYTHON,old=process.env.AMPLIFIER_SIGNIN_OLD_PYTHON;
async function fixture(python){
 const root=await mkdtemp(join(process.env.AMPLIFIER_SIGNIN_TEST_HOME??tmpdir(),'signin-composition-'));const workspace=join(root,'workspace'),home=join(root,'home'),web=join(root,'web'),source=join(root,'provider'),pkg=join(source,'amplifier_module_provider_openai_chatgpt');
 for(const p of [workspace,home,web,pkg])await mkdir(p,{recursive:true});
 await writeFile(join(web,'index.html'),'<html><head></head><body>Owned fixture</body></html>');await writeFile(join(pkg,'__init__.py'),'');
 await writeFile(join(source,'pyproject.toml'),'[project]\nname="amplifier-module-provider-openai-chatgpt"\nversion="0.0.1"\n');
 await writeFile(join(pkg,'oauth.py'),"import asyncio\nasync def login(token_file_path,print_fn):\n print_fn('Open https://auth.openai.com/codex/device')\n await asyncio.Event().wait()\n");
 const original=join(root,'original.json');await writeFile(original,'{}');await writeFile(join(home,'settings.yaml'),`bundle:\n  app: []\nconfig:\n  providers:\n    - id: original\n      module: provider-openai-chatgpt\n      config:\n        token_file_path: ${JSON.stringify(original)}\n        auth_mode: chatgpt_codex\n`);
 const native=join(root,'native.json');await writeFile(native,JSON.stringify({home,appHome:join(root,'app'),registryHome:join(root,'registry'),runtimeImmutable:true,adminWorkspaceRoots:[workspace],moduleSources:{'provider-openai-chatgpt':source}}));
 const app=await createDistribution({account:'owned-signin',stateDirectory:join(root,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'native',label:'Owned offline fixture',command:python,args:['-I','-B','-m','amplifier_acp','--config',native],env:{HOME:root,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_RUNTIME_IMMUTABLE:'1',AMPLIFIER_SESSION_STATE_HOME:join(root,'writers'),AMPLIFIER_SOURCE_STORE:join(root,'sources')}}],nativeAdmin:{engine:'native'}});
 const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const message=JSON.parse(raw),entry=pending.get(message.id);if(entry){pending.delete(message.id);clearTimeout(entry.timer);message.error?entry.reject(Error(message.error.message)):entry.resolve(message.result);}});
 const rpc=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>reject(Error('Owned fixture timed out')),10000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 await rpc('initialize',{channel:'ahp-root://',clientId:'owned-signin-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return{app,original,rpc,async close(){socket.close();await once(socket,'close');await app.close();}};
}
for(const [name,python,available] of [['marked',current,true],['old',old,false]])test(`public composed provider resource negotiates ${name} peer through the same genuine administration connection`,{skip:!python},async()=>{
 const f=await fixture(python);try{
  const r=await f.rpc('resourceRead',{channel:'ahp-root://',uri:'amplifier-capability://native/provider-catalog?scope=host',encoding:'utf-8'});
  const descriptor=JSON.parse(r.data).data.setup.providerSignIn;
  assert.deepEqual(descriptor,{version:1,available,route:'administration',provider:'provider-openai-chatgpt',selectors:available?['sessionId','scope','loginId']:[]});
  const schemas=await f.app.capabilities.getActionSchemas();assert.equal(Boolean(schemas['providers.loginCancel'].schema.properties.sessionId),available);
  for(const origin of ['ui','agent']){
   const call=(operation,args)=>f.app.host.invokeCapability({version:1,topic:'provider-catalog',channel:'ahp-root://',operation,args,commandId:randomUUID()},{actorId:'owned-'+origin,clientId:'owned-signin',origin});
   if(!available){await assert.rejects(call('providers.loginCancel',{id:'original',sessionId:'ahp-root://',scope:'local',loginId:'a'.repeat(32)}));continue;}
   const start=await call('providers.login',{id:'original',scope:'local',authMode:'chatgpt_codex'});assert.equal(start.accepted,true);
   // Persist only authority selectors; never copy provider URLs or credentials into this binding.
   const binding={providerId:'original',sessionId:'ahp-root://',scope:start.result.login.scope,loginId:start.result.login.loginId};
   assert.match(binding.loginId,/^[a-f0-9]{32}$/);assert.equal(binding.scope,'local');
   const args={id:binding.providerId,sessionId:binding.sessionId,scope:binding.scope,loginId:binding.loginId};
   let status;for(let i=0;i<100;i++){status=await call('providers.loginStatus',args);if(status.result.login.status==='waiting')break;await new Promise(r=>setTimeout(r,10));}
   assert.equal(status.result.login.status,'waiting');
   await assert.rejects(call('providers.loginCancel',{...args,sessionId:'host'}));
   const cancel=await call('providers.loginCancel',args);assert.equal(cancel.accepted,true);assert.equal(cancel.result.login.status,'cancelled');assert.equal(cancel.result.login.loginId,binding.loginId);
   assert.deepEqual(cancel.updates.find(u=>u.topic==='provider-catalog').data.setup.providerSignIn,descriptor);
  }
  assert.equal(await readFile(f.original,'utf8'),'{}');
 }finally{await f.close();}
});
