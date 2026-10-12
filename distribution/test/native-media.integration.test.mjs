import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {pathToFileURL} from 'node:url';
import {createHash, randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';

const python=process.env.AMPLIFIER_ACP_PYTHON;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]);home=p/'native-home';home.mkdir();provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-media-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_media_fixture';module.mkdir()
(module/'__init__.py').write_bytes(Path(sys.argv[2]).read_bytes())
(module/'media.json').write_text(sys.argv[3])
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: media-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-media-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'native-app'),'bundle':str(bundle),'startupTimeout':90}))
`;

test('resident Core agent discovers its saved commentary and attaches immutable media in the same turn',
 {skip:!python,timeout:90000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'unified-native-media-'));
 const workspace=join(directory,'workspace'),web=join(directory,'web');
 await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 const bytes=Buffer.from('47494638396101000100800000ff00000000ff21ff0b4e45545343415045322e30030101000021f90404070000002c000000000100010000020244010021f90404130000002c00000000010001000002024401003b','hex');
 await writeFile(join(workspace,'animation.gif'),bytes);
 let app,socket;
 try{
  const prepared=spawnSync(python,['-I','-B','-c',setup,directory,new URL('./fixtures/media_provider.py',import.meta.url).pathname,JSON.stringify({path:'animation.gif',sha256:createHash('sha256').update(bytes).digest('hex')})],{encoding:'utf8'});
  assert.equal(prepared.status,0,prepared.stderr);
  app=await createDistribution({account:'offline-media',stateDirectory:join(directory,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',join(directory,'native-home'),'--initially-empty-home',join(directory,'native-home'),'--app-home',join(directory,'native-app'),'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0']},engines:[{id:'amplifier',label:'Native media fixture',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')]}]});
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');
  let next=0;const pending=new Map();
  socket.on('message',raw=>{const item=JSON.parse(raw),wait=pending.get(item.id);if(wait){pending.delete(item.id);clearTimeout(wait.timer);item.error?wait.reject(Error(item.error.message)):wait.resolve(item.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>reject(Error('Media fixture request timed out')),60000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  await request('initialize',{channel:'ahp-root://',clientId:'media-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
  const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  await request('subscribe',{channel:session});
  const invoke=app.host.invokeCapability.bind(app.host);let capabilityError;
  app.host.invokeCapability=async(...args)=>{try{return await invoke(...args);}catch(error){capabilityError=String(error.stack||error);throw error;}};
  const commandId=randomUUID();await app.host.submitTurn(session,{commandId,clientId:'media-browser',text:'Attach the prepared animation to your saved commentary.'});
  const settled=await app.host.waitForTurn(session,commandId,45000);
  const provider=join(directory,'provider/amplifier_module_provider_media_fixture');
  const state=JSON.parse(await readFile(join(provider,'result-1.json'),'utf8'));
  const attachment=await readFile(join(provider,'result-2.json'),'utf8').then(JSON.parse).catch(()=>null);
  assert.equal(settled.status,'completed',JSON.stringify({settled,state,attachment,capabilityError}));
  assert.match(settled.text,/Saved animation attached/);
  assert.equal(state.output.messages.length,1);assert.equal(state.output.source,'native');
  const saved=state.output.messages[0];assert.match(saved.messageId,/^native:/);
  const action=async(operation,args)=>(await request('x-amplifier/capabilityAction',{channel:session,topic:'outputs',operation,version:1,args:{sessionId:session,...args},commandId:randomUUID()})).result;
  const attached=await action('outputs.media',{messageId:saved.messageId});assert.equal(attached.items.length,1);
  assert.equal(attached.items[0].sha256,createHash('sha256').update(bytes).digest('hex'));
  assert.equal(await readFile(join(provider,'calls.txt'),'utf8'),'0\n1\n2\n','Discovery and attachment never admit another turn');
  const refreshed=await app.host.refreshSessionHistory(session);
  assert.equal(refreshed.refreshedTurns,1,'Normal idle client refresh binds saved message identities');
  const history=await request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:5}});
  const parts=history.snapshot.state.turns.flatMap(turn=>turn.responseParts);
  assert.ok(parts.some(part=>part.kind==='markdown'&&part.content==='Here is the saved animation.'&&part._meta?.['amplifier.dev/history']?.messageId===saved.messageId),'Saved attachment identity must match the visible persisted message: '+JSON.stringify({saved,parts:parts.filter(p=>p.kind==='markdown')}));
 }finally{socket?.terminate();await app?.close();await rm(directory,{recursive:true,force:true});}
});
