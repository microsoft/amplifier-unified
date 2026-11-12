import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatPages}=await server.ssrLoadModule('/src/chat-pages.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('external chat info retains distinct identities and never inspects native history',async()=>{
 const calls=[],session={id:'host-chat',status:'idle',workspace:'/project',externalAgent:{id:'remote',label:'Project assistant',sessionId:'remote-session'}};
 let root;
 try{
  for(const page of ['session-details','chat-models','chat-context','chat-advanced']){
   const element=React.createElement(ChatPages,{page,state:{view:{}},session,act:async(name,args)=>{calls.push({name,args});return {accepted:true}},open:()=>{}});
   await renderAct(async()=>{if(root)root.update(element);else root=create(element)});
   const text=JSON.stringify(root.toJSON());
   assert.match(text,/Project assistant/);assert.match(text,/host-chat/);assert.match(text,/remote-session/);
   assert.equal(root.root.findAllByProps({'data-external-agent-info':true}).length,1);
  }
  assert.deepEqual(calls,[],'No native history, model or context request is made');
 }finally{await renderAct(async()=>root?.unmount())}
});
test('chat info discards old locations and errors immediately and ignores late inspections',async()=>{
 const pending=new Map();
 const act=(name,args)=>name==='session.inspect'?new Promise((resolve,reject)=>pending.set(args.id,{resolve,reject})):Promise.resolve({accepted:true});
 const render=id=>React.createElement(ChatPages,{page:'session-details',state:{view:{}},session:{id,status:'idle',workspace:`/workspace/${id}`},act,open:()=>{}});
 let root;await renderAct(async()=>{root=create(render('a'))});
 await renderAct(async()=>pending.get('a').resolve({result:{historyDirectory:'/history/a',executionDirectory:'/execution/a'}}));
 assert.match(JSON.stringify(root.toJSON()),/\/history\/a/);
 await renderAct(async()=>root.update(render('b')));
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/\/history\/a|\/execution\/a/);
 await renderAct(async()=>pending.get('b').reject(Error('B inspection failed')));
 assert.match(JSON.stringify(root.toJSON()),/B inspection failed/);
 await renderAct(async()=>root.update(render('c')));
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/B inspection failed/);
 await renderAct(async()=>root.update(render('d')));
 await renderAct(async()=>pending.get('c').resolve({result:{historyDirectory:'/history/c'}}));
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/\/history\/c/);
 await renderAct(async()=>pending.get('d').resolve({result:{historyDirectory:'/history/d'}}));
 assert.match(JSON.stringify(root.toJSON()),/\/history\/d/);
 await renderAct(async()=>root.unmount());
});

test('external chat retains explicit Full access without mounting native file settings',async()=>{
 const calls=[],session={id:'host-chat',externalAgent:{id:'remote',label:'Project assistant'}};
 let root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true,result:{revision:'access-r1',mode:'ask',source:'default',overrides:{session:{mode:'inherit'},workspace:{mode:'inherit'},app:{mode:'inherit'}}}}};
 try{
  await renderAct(async()=>root=create(React.createElement(ChatPages,{page:'chat-write-files',state:{view:{}},session,act,open:()=>{}})));
  assert.deepEqual(calls,[{name:'access.get',args:{sessionId:'host-chat'}}]);
  await renderAct(async()=>root.root.findByProps({'aria-label':'Access mode'}).props.onChange({target:{value:'full'}}));
  await renderAct(async()=>root.root.findAllByType('button').find(node=>node.children.includes('Save access preference')).props.onClick());
  assert.deepEqual(calls[1],{name:'access.set',args:{sessionId:'host-chat',scope:'session',mode:'full',expectedRevision:'access-r1'}});
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/File restrictions/);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('external navigation never mounts previously visited native pages in the background',async()=>{
 const calls=[],session={id:'external-chat',title:'Project assistant',externalAgent:{id:'remote'}};
 let root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true,result:{revision:'r1',mode:'ask',source:'default',overrides:{session:{mode:'inherit'},workspace:{mode:'inherit'},app:{mode:'inherit'}}}}};
 try{
  for(const page of ['session-details','chat-models','chat-rename','chat-write-files','chat-help']){
   await renderAct(async()=>{const element=React.createElement(ChatPages,{page,state:{view:{}},session,act,open:()=>{}});if(root)root.update(element);else root=create(element)});
  }
  assert.deepEqual(calls.filter(call=>call.name!=='diagnostics.report'),[{name:'access.get',args:{sessionId:'external-chat'}}]);
  assert.equal(root.root.findAllByProps({'aria-label':'Access and approvals'}).length,0);
 }finally{await renderAct(async()=>root?.unmount())}
});
