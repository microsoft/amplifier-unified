import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useState} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {StarterSettings}=await server.ssrLoadModule('/src/workspace-starters.jsx');
const {SettingsDraftProvider,useSettingsDrafts}=await server.ssrLoadModule('/src/settings-drafts.jsx');
const {SettingsLayoutContext}=await server.ssrLoadModule('/src/settings-layout.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.window={addEventListener(){},removeEventListener(){}};
test.after(()=>server.close());

const builtin={id:'builtin',revision:'release-1',builtIn:true,bundleDefault:'release',source:'shipped',name:'Everyday',description:'General work',instructions:'Be helpful',bundle:'work',repositories:[{url:'https://example.com/old.git',directory:'old',ref:'main',automatic:'private metadata'}],trackResources:false,scratch:false,rootGit:false};
const custom={...builtin,id:'custom',builtIn:false,revision:7,name:'My starter'};
const editable=['name','description','instructions','bundle','repositories','trackResources','scratch','rootGit'];
const fields=row=>Object.fromEntries(editable.map(key=>[key,key==='repositories'?row.repositories.map(({url,directory,ref})=>({url,directory,ref})):row[key]]));
const ok=result=>({accepted:true,result});
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.includes(label));
const control=(root,label)=>{
 const node=root.root.findAllByType('label').find(row=>row.children.includes(label));
 return node.props.htmlFor?root.root.findByProps({id:node.props.htmlFor}):node.findByType('input');
};
const edit=(root,label,value)=>act(async()=>control(root,label).props.onChange({target:{value,checked:value}}));
const click=(root,label)=>act(async()=>button(root,label).props.onClick());
const submit=(root,copy=false)=>act(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){},nativeEvent:{submitter:{value:copy?'copy':''}}}));

async function fixture({selected='builtin',items=[builtin,custom],save,compact=false,failReload=false}={}){
 let root,guard,listReads=0,updateState,catalog={items:structuredClone(items),bundles:[{value:'work',label:'Work'},{value:'other',label:'Other'}]};
 const calls=[],routes=[],focus=[];
 const dispatch=async(action,args,meta)=>{
  if(action==='workspace.starters.list'){if(failReload&&listReads++)throw Error('List unavailable');return ok(catalog)}
  calls.push(structuredClone({action,args,meta}));
  assert.equal(action,'workspace.starters.save','Copy must save the edited draft, not duplicate or remove the stored definition');
  const response=save?await save(args,meta,calls.length):ok({...args.starter,id:args.id||'saved-copy',revision:args.id?args.expectedRevision+1:1,builtIn:false});
  if(response?.accepted&&response.result?.id){
   catalog={...catalog,items:[...catalog.items.filter(row=>row.id!==response.result.id),response.result]};
  }
  return response;
 };
 function Frame(){
  const [state,setState]=useState({workspaceStarters:catalog,view:{workspaceStarterEditor:{id:selected,detailOpen:true}}});
  updateState=setState;
  guard=useSettingsDrafts();
  const route=(action,args)=>{assert.equal(action,'view.update');routes.push(args.patch);setState(current=>({...current,view:{...current.view,...args.patch}}));return Promise.resolve(ok({}))};
  return React.createElement(SettingsDraftProvider,{value:guard.context},
   React.createElement(SettingsLayoutContext.Provider,{value:{compact,active:true}},
    React.createElement(StarterSettings,{state,act:route,dispatch}),guard.dialog));
 }
 await act(async()=>{root=create(React.createElement(Frame),{createNodeMock:element=>element.props.role==='alert'?{focus:()=>focus.push('alert')}:null})});
 return {root,calls,routes,focus,get guard(){return guard},async refresh(items){await act(async()=>updateState(current=>({...current,workspaceStarters:{...catalog,items}})))},async clean(){await act(async()=>root.unmount())}};
}

test('built-in fields are immediately editable; one submit copies every edit without host metadata',async()=>{
 const original=structuredClone(builtin),f=await fixture();
 try{
  assert.ok(f.root.root.findAllByType('fieldset').every(row=>!row.props.disabled));
  assert.equal(button(f.root,'Save changes'),undefined);
  assert.equal(button(f.root,'Duplicate'),undefined);
  assert.doesNotMatch(JSON.stringify(f.root.toJSON()),/read-only|Duplicate to customize/);
  await edit(f.root,'Description','For my work');
  await edit(f.root,'Workspace bundle default','other');
  await edit(f.root,'Agent instructions','My edited guidance');
  await edit(f.root,'Create SCRATCH.md working memory',true);
  await edit(f.root,'Track external resources',true);
  await edit(f.root,'Initialize local workspace Git',true);
  await act(async()=>f.root.root.findByProps({'aria-label':'Git URL 1'}).props.onChange({target:{value:'https://example.com/new.git'}}));
  await act(async()=>f.root.root.findByProps({'aria-label':'Repository folder 1'}).props.onChange({target:{value:'new'}}));
  await act(async()=>f.root.root.findByProps({'aria-label':'Repository ref 1'}).props.onChange({target:{value:'topic'}}));
  await submit(f.root);
  assert.deepEqual(f.calls[0].args,{starter:{name:'Everyday copy',description:'For my work',bundle:'other',instructions:'My edited guidance',scratch:true,trackResources:true,rootGit:true,repositories:[{url:'https://example.com/new.git',directory:'new',ref:'topic'}]}});
  assert.ok(f.calls[0].meta.id);
  assert.deepEqual(builtin,original,'Shipped definition must stay unchanged');
  assert.equal(f.routes.at(-1).workspaceStarterEditor.id,'saved-copy');
  assert.equal(control(f.root,'Starter name').props.value,'Everyday copy');
  assert.ok(button(f.root,'Save changes'),'The saved custom copy is selected for further editing');
  assert.match(JSON.stringify(f.root.toJSON()),/Starter saved. Existing workspaces are unchanged/);
  assert.equal(f.guard.hasUnsaved(),false);
 }finally{await f.clean()}
});

test('custom primary keyboard submit saves changes at the loaded expected revision',async()=>{
 const f=await fixture({selected:'custom'});
 try{
  assert.ok(button(f.root,'Save changes'));
  assert.ok(button(f.root,'Save as new starter'));
  await edit(f.root,'Starter name','Renamed');
  await submit(f.root);
  assert.deepEqual(f.calls[0].args,{id:'custom',expectedRevision:7,starter:{...fields(custom),name:'Renamed'}});
  await edit(f.root,'Description','Next edit');
  await submit(f.root);
  assert.equal(f.calls[1].args.expectedRevision,8,'Further edits use the confirmed saved revision');
 }finally{await f.clean()}
});

test('custom copy uses current unsaved edits, omits identity and preserves a chosen name',async()=>{
 const f=await fixture({selected:'custom'});
 try{
  await edit(f.root,'Starter name','Another setup');
  await edit(f.root,'Description','Unsaved edit to copy');
  assert.equal(button(f.root,'Save as new starter').props.type,'submit');
  assert.equal(button(f.root,'Save as new starter').props.value,'copy');
  await submit(f.root,true);
  assert.deepEqual(f.calls[0].args,{starter:{...fields(custom),name:'Another setup',description:'Unsaved edit to copy'}});
  assert.equal(f.routes.at(-1).workspaceStarterEditor.id,'saved-copy');
 }finally{await f.clean()}
});

test('unchanged copy names are useful and bounded without a rename dialog',async()=>{
 for(const selected of ['builtin','custom']){
  const source=selected==='builtin'?builtin:custom;
  const f=await fixture({selected,items:[builtin,custom,{...custom,id:'copy',name:source.name+' copy'}]});
  try{await submit(f.root,true);assert.equal(f.calls[0].args.starter.name,source.name+' copy 2');assert.equal(f.root.root.findAllByProps({role:'alertdialog'}).length,0)}finally{await f.clean()}
 }
 const f=await fixture({items:[{...builtin,name:'x'.repeat(200)}]});
 try{await submit(f.root);assert.equal(f.calls[0].args.starter.name.length,200);assert.match(f.calls[0].args.starter.name,/ copy$/)}finally{await f.clean()}
});

test('new blank starter has a single create action and sends only editable fields',async()=>{
 const f=await fixture();
 try{
  await click(f.root,'New starter');
  assert.ok(button(f.root,'Create starter').props.disabled);
  assert.equal(button(f.root,'Save as new starter'),undefined);
  await edit(f.root,'Starter name','From scratch');
  await submit(f.root);
  assert.deepEqual(f.calls[0].args,{starter:{name:'From scratch',description:'',instructions:'',bundle:'',repositories:[],trackResources:false,scratch:false,rootGit:false}});
 }finally{await f.clean()}
});

test('dirty built-in navigation retains edits until explicit cancel',async()=>{
 const f=await fixture();
 try{
  await edit(f.root,'Starter name','Unfinished');
  assert.equal(f.guard.hasUnsaved(),true);
  assert.ok(button(f.root,'New starter').props.disabled);
  await act(async()=>f.root.root.findByProps({'data-collection-id':'custom'}).findByType('button').props.onClick());
  assert.equal(control(f.root,'Starter name').props.value,'Unfinished');
  assert.match(JSON.stringify(f.root.toJSON()),/Save or cancel the current starter/);
  assert.deepEqual(f.focus,['alert']);
  await click(f.root,'Cancel edits');
  assert.equal(control(f.root,'Starter name').props.value,'Everyday');
  assert.equal(f.guard.hasUnsaved(),false);
  assert.equal(f.calls.length,0);
 }finally{await f.clean()}
});

test('validation and revision errors retain an editable draft without weakening expectedRevision',async()=>{
 for(const status of [400,409]){
  const f=await fixture({selected:'custom',save:async()=>({accepted:false,status,error:status===409?'Starter changed elsewhere.':'Invalid repository.'})});
  try{
   await edit(f.root,'Description','Keep my edit');
   await submit(f.root);
   assert.equal(control(f.root,'Description').props.value,'Keep my edit');
   assert.ok(f.root.root.findAllByType('fieldset').every(row=>!row.props.disabled));
   assert.ok(button(f.root,'Save changes'));
   assert.equal(f.guard.hasUnsaved(),true);
   await submit(f.root);
   assert.equal(f.calls[1].args.expectedRevision,7);
   assert.notEqual(f.calls[0].meta.id,f.calls[1].meta.id);
  }finally{await f.clean()}
 }
});

for(const failure of [Error('Connection lost'),Object.assign(Error('Unreadable response'),{status:403,unconfirmed:true})]){
 test(`unknown save is locked and checks the same command without creating a second copy: ${failure.message}`,async()=>{
  let release;
  const f=await fixture({save:async(args,meta,count)=>{
   if(count===1)return new Promise((resolve,reject)=>{release=()=>reject(failure)});
   return ok({...args.starter,id:'confirmed-copy',revision:1,builtIn:false});
  }});
  try{
   await edit(f.root,'Description','Kept across uncertainty');
   await submit(f.root);await submit(f.root,true);
   assert.equal(f.calls.length,1,'Pending submission blocks same-render and subsequent submits');
   assert.ok(f.root.root.findAllByType('fieldset').every(row=>row.props.disabled));
   await act(async()=>release());
   assert.ok(button(f.root,'Check the same save'));
   assert.ok(button(f.root,'Cancel edits').props.disabled);
   assert.ok(button(f.root,'New starter').props.disabled);
   let close;
   await act(async()=>{close=f.guard.confirmClose()});
   await click(f.root,'Discard changes');
   assert.equal(await close,false,'An unknown save cannot discard its recovery token while closing Settings');
   assert.equal(control(f.root,'Description').props.value,'Kept across uncertainty');
   assert.equal(f.guard.hasUnsaved(),true);
   await submit(f.root);
   assert.deepEqual(f.calls[1],f.calls[0],'Checking must preserve both args and command identity');
   assert.equal(f.routes.at(-1).workspaceStarterEditor.id,'confirmed-copy');
   assert.equal(f.guard.hasUnsaved(),false);
  }finally{await f.clean()}
 });
}

test('an accepted but incomplete response does not clear edits or authorize a new save',async()=>{
 const f=await fixture({save:async()=>ok({})});
 try{
  await edit(f.root,'Description','Keep this');
  await submit(f.root);await submit(f.root);
  assert.equal(control(f.root,'Description').props.value,'Keep this');
  assert.deepEqual(f.calls[0],f.calls[1]);
  assert.ok(button(f.root,'Check the same save'));
 }finally{await f.clean()}
});

test('a recovery fingerprint conflict keeps the original uncertain save locked',async()=>{
 const f=await fixture({save:async(args,meta,count)=>{if(count===1)throw Error('Connection lost');return {accepted:false,status:409,error:'Command fingerprint changed.'}}});
 try{
  await submit(f.root);await submit(f.root);
  assert.deepEqual(f.calls[1],f.calls[0]);
  assert.ok(button(f.root,'Check the same save'));
  assert.ok(button(f.root,'Cancel edits').props.disabled);
  assert.ok(f.root.root.findAllByType('fieldset').every(row=>row.props.disabled));
 }finally{await f.clean()}
});

test('a post-acceptance state-read 403 does not authorize a new creation',async()=>{
 const f=await fixture({save:async()=>{throw Object.assign(Error('Could not read state'),{status:403})}});
 try{
  await submit(f.root);await submit(f.root);
  assert.deepEqual(f.calls[0],f.calls[1]);
  assert.ok(button(f.root,'Check the same save'));
 }finally{await f.clean()}
});

test('same-render submits and an unchanged in-flight copy retain a single protected operation',async()=>{
 let finish;
 const f=await fixture({save:async args=>new Promise(resolve=>{finish=()=>resolve(ok({...args.starter,id:'copy',revision:1,builtIn:false}))})});
 try{
  await act(async()=>{
   const handler=f.root.root.findByType('form').props.onSubmit;
   handler({preventDefault(){}});
   handler({preventDefault(){},nativeEvent:{submitter:{value:'copy'}}});
  });
  assert.equal(f.calls.length,1);
  assert.equal(f.guard.hasUnsaved(),true,'Even an unchanged copied source protects its pending operation');
  await act(async()=>finish());
  assert.equal(f.guard.hasUnsaved(),false);
 }finally{await f.clean()}
});

test('catalog updates retain draft edits and the loaded expected revision',async()=>{
 const f=await fixture({selected:'custom'});
 try{
  await edit(f.root,'Description','My unfinished edit');
  await f.refresh([builtin,{...custom,revision:99,description:'Edited elsewhere'}]);
  assert.equal(control(f.root,'Description').props.value,'My unfinished edit');
  await submit(f.root);
  assert.equal(f.calls[0].args.expectedRevision,7);
 }finally{await f.clean()}
});

test('confirmed creation stays selected when only the following list refresh fails',async()=>{
 const f=await fixture({failReload:true});
 try{
  await submit(f.root);
  assert.ok(button(f.root,'Save changes'));
  assert.equal(button(f.root,'Check the same save'),undefined);
  assert.equal(f.routes.at(-1).workspaceStarterEditor.id,'saved-copy');
  assert.match(JSON.stringify(f.root.toJSON()),/The change was saved, but the starter list could not refresh/);
  await submit(f.root);
  assert.equal(f.calls[1].args.id,'saved-copy','Next intentional save updates the confirmed copy, never creates another');
 }finally{await f.clean()}
});

test('an open delete confirmation cannot discard an unconfirmed copy or its edits',async()=>{
 const f=await fixture({selected:'custom',save:async()=>{throw Error('Connection lost')}});
 try{
  await click(f.root,'Delete this starter');
  assert.ok(button(f.root,'Delete starter'));
  await edit(f.root,'Description','Copy this edit');
  assert.ok(button(f.root,'Delete starter').props.disabled);
  await submit(f.root,true);
  assert.ok(button(f.root,'Delete starter').props.disabled);
  await click(f.root,'Delete starter');
  assert.equal(f.calls.length,1,'The retained confirmation must not invoke remove while copy recovery is pending');
  assert.equal(control(f.root,'Description').props.value,'Copy this edit');
  assert.ok(button(f.root,'Check the same save'));
 }finally{await f.clean()}
});

test('compact actions target the editor form and retain accessible field labels',async()=>{
 const f=await fixture({compact:true});
 try{
  assert.equal(button(f.root,'Save as new starter').props.form,f.root.root.findByType('form').props.id);
  assert.equal(button(f.root,'Save as new starter').props.type,'submit');
  for(const label of ['Starter name','Description','Workspace bundle default','Agent instructions']){
   const input=control(f.root,label);assert.ok(input.props.id);
  }
 }finally{await f.clean()}
});