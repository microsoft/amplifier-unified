import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ExternalAgentSettings}=await server.ssrLoadModule('/src/external-agent-settings.jsx');
const {newChatSetup}=await server.ssrLoadModule('/src/new-chat.jsx');
const {ComposerOptions}=await server.ssrLoadModule('/src/composer-options.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.crypto??=webcrypto;
test.after(()=>server.close());
const button=(root,label)=>root.root.findAllByType('button').find(node=>node.children.includes(label));
const edit=(root,id,value)=>renderAct(async()=>root.root.findByProps({id}).props.onChange({target:{value}}));
const click=(root,label)=>renderAct(async()=>button(root,label).props.onClick());
const agent={id:'one',label:'My agent',command:'/agents/connect',args:['acp','--token-file','/private/token'],cwd:'/launch',sessionCwd:'/remote'};
const catalog=[{id:agent.id,label:agent.label,revision:'row-one'}];
const ok=result=>({accepted:true,result});
async function mount(act){let root;await renderAct(async()=>{root=create(React.createElement(ExternalAgentSettings,{act}))});return root}

test('create saves private launch fields only through the connection action',async()=>{
 const calls=[];const act=async(name,args,meta)=>{calls.push({name,args,meta});return ok(name.endsWith('list')?{agents:[]}:{savedId:'new',agents:[{id:'new',label:args.agent.label,revision:'new-row'}]})};
 const root=await mount(act);await click(root,'Add external agent');
 await edit(root,'external-agent-name','Constellation');await edit(root,'external-agent-command','/agents/instance');
 await edit(root,'external-agent-arguments','acp\n--token-file\n/private/file with spaces');
 await edit(root,'external-agent-session-cwd','/remote/project');
 for(const input of root.root.findAll(node=>['input','textarea'].includes(node.type))){
  let boundary=input;while(boundary&&boundary.props['data-private']!=='true')boundary=boundary.parent;
  assert.ok(boundary,`${input.props.id} must be excluded from ordinary rendered-view reports`);
 }
 await click(root,'Save connection');
 assert.deepEqual(calls.map(row=>row.name),['externalAgents.list','externalAgents.save']);
 assert.deepEqual(calls[1].args,{agent:{label:'Constellation',command:'/agents/instance',args:['acp','--token-file','/private/file with spaces'],sessionCwd:'/remote/project'}});
 assert.ok(calls[1].meta.id);assert.match(JSON.stringify(root.toJSON()),/Connection saved/);
 assert.equal(root.root.findAllByType('input').length,0);await renderAct(async()=>root.unmount());
});

test('edit and remove use the loaded revision; removing preserves history wording',async()=>{
 const calls=[];const act=async(name,args)=>{calls.push({name,args});return ok(name.endsWith('list')?{agents:catalog}:name.endsWith('get')?{agent,revision:'row-one'}:{agents:[]})};
 const root=await mount(act);await click(root,'Edit');
 assert.equal(root.root.findByProps({id:'external-agent-arguments'}).props.value,agent.args.join('\n'));
 assert.match(JSON.stringify(root.toJSON()),/Removing this connection keeps saved history/);
 await click(root,'Remove connection');assert.deepEqual(calls.at(-1),{name:'externalAgents.remove',args:{id:'one',expectedRevision:'row-one'}});
 assert.match(JSON.stringify(root.toJSON()),/Saved chats are kept/);await renderAct(async()=>root.unmount());
});

test('stale edit preserves form until explicit reload; it cannot silently overwrite',async()=>{
 let loads=0;const writes=[];
 const root=await mount(async(name,args)=>{
  if(name.endsWith('list'))return ok({agents:catalog});
  if(name.endsWith('get'))return ok({agent:{...agent,label:loads++?'Changed elsewhere':agent.label},revision:loads===1?'row-one':'row-two'});
  writes.push(args);throw Object.assign(Error('Connection changed elsewhere. Reload before saving.'),{status:409});
 });
 await click(root,'Edit');await edit(root,'external-agent-name','My edit');await click(root,'Save connection');
 assert.equal(writes[0].expectedRevision,'row-one');assert.equal(root.root.findByProps({id:'external-agent-name'}).props.value,'My edit');
 assert.equal(button(root,'Check the same change'),undefined);assert.match(JSON.stringify(root.toJSON()),/changed elsewhere/);
 await click(root,'Discard edits and reload');assert.equal(root.root.findByProps({id:'external-agent-name'}).props.value,'Changed elsewhere');
 await renderAct(async()=>root.unmount());
});

test('lost save acknowledgement retries the same command without retaining private fields in browser storage',async()=>{
 const saves=[];const root=await mount(async(name,args,meta)=>{
  if(name.endsWith('list'))return ok({agents:[]});saves.push({name,args,meta});if(saves.length===1)throw Error('Connection lost');return ok({agents:catalog,savedId:'one'});
 });
 await click(root,'Add external agent');await edit(root,'external-agent-name','My agent');await edit(root,'external-agent-command','/agent');await click(root,'Save connection');
 assert.equal(root.root.findByType('fieldset').props.disabled,true);assert.match(JSON.stringify(root.toJSON()),/Connection lost/);
 await click(root,'Check the same change');assert.deepEqual(saves[0],saves[1]);assert.match(JSON.stringify(root.toJSON()),/Connection saved/);
 await renderAct(async()=>root.unmount());
});

test('external creation carries only the endpoint and existing workspace; native defaults are unchanged',()=>{
 const state={selectedWorkspaceId:'w',workspaces:[{id:'w',path:'/work'}],view:{newSessionDraft:{externalAgentId:'one',title:'Hello',bundle:'native-bundle',selection:{provider:'native'},inheritContext:true,location:{kind:'managed'}}}};
 assert.deepEqual(newChatSetup(state),{externalAgentId:'one',title:'Hello',workspace:'/work',location:{kind:'workspace'}});
 const native=newChatSetup({...state,view:{newSessionDraft:{bundle:'native-bundle',selection:{provider:'native'}}}});
 assert.equal(native.bundle,'native-bundle');assert.deepEqual(native.selection,{provider:'native'});
 assert.equal(native.location.kind,'workspace');
 assert.deepEqual(newChatSetup({view:{newSessionDraft:{externalAgentId:'one'}}}),{externalAgentId:'one',title:'',workspace:'',location:{kind:'workspace'}});
});

test('a name-only edit preserves exact existing launch bytes, including unusual arguments and paths',async()=>{
 const original={...agent,command:'/agent with trailing space ',args:['','line one\nline two','\r'],cwd:'/launch ',sessionCwd:'/remote '};let saved;
 const root=await mount(async(name,args)=>{
  if(name.endsWith('list'))return ok({agents:catalog});if(name.endsWith('get'))return ok({agent:original,revision:'row-one'});
  saved=args;return ok({agents:catalog,savedId:'one'});
 });
 await click(root,'Edit');await edit(root,'external-agent-name','Renamed');await click(root,'Save connection');
 assert.deepEqual(saved,{agent:{...original,label:'Renamed'},expectedRevision:'row-one'});await renderAct(async()=>root.unmount());
});

test('external draft mounts no native model/bundle discovery controls',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(ComposerOptions,{state:{view:{newSessionDraft:{externalAgentId:'one'}}},act:async(...args)=>calls.push(args)}))});
 assert.match(JSON.stringify(root.toJSON()),/Model and tools are managed by the external agent/);
 assert.equal(root.root.findAllByType('button').length,0);assert.deepEqual(calls,[]);
 await renderAct(async()=>root.unmount());
});
