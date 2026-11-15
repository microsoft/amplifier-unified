import {test,after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useState} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const saved=new Map(),events=new EventTarget();
let failWrite=false;
const originals=Object.fromEntries(['navigator','matchMedia','localStorage','window','IS_REACT_ACT_ENVIRONMENT'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
Object.defineProperties(globalThis,{
 navigator:{configurable:true,value:{userAgent:'Desktop',maxTouchPoints:0}},
 matchMedia:{configurable:true,value:query=>({matches:['(pointer: fine)','(hover: hover)'].includes(query)})},
 localStorage:{configurable:true,value:{getItem:key=>saved.get(key)??null,setItem(key,value){if(failWrite)throw Error('blocked');saved.set(key,value);}}},
 window:{configurable:true,value:events},
 IS_REACT_ACT_ENVIRONMENT:{configurable:true,value:true},
});
const {ComposerReturnSettings,useComposerReturn}=await server.ssrLoadModule('/src/composer-return-preference.jsx');
after(async()=>{
 await server.close();
 for(const [key,descriptor] of Object.entries(originals)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
});
const text=node=>node.children.map(child=>typeof child==='string'?child:text(child)).join('');
test('Writing preference is labeled, immediately shared with a mounted editor and does not replace its draft',async()=>{
 let mounts=0,root;
 function EditorConsumer(){
  const {mode}=useComposerReturn();
  const [draft]=useState(()=>{mounts++;return 'Unsent draft';});
  return React.createElement('output',{'data-mode':mode},draft);
 }
 const render=()=>React.createElement(React.Fragment,null,React.createElement(ComposerReturnSettings),React.createElement(EditorConsumer));
 try{
  await act(async()=>root=create(render()));
  const select=()=>root.root.findByType('select'),output=()=>root.root.findByType('output');
  assert.equal(output().props['data-mode'],'desktop-send');
  const label=root.root.findByType('label');
  assert.equal(label.props.htmlFor,select().props.id);assert.equal(text(label),'Return key');
  assert.equal(select().props['aria-describedby'],root.root.findByType('p').props.id);
  assert.match(text(root.root.findByType('p')),/This browser only.*Automatic: Enter to send/);
  assert.equal(root.root.findAllByType('button').length,0,'No accidental composer-form submit control');
  await act(async()=>select().props.onChange({target:{value:'newline'}}));
  assert.equal(output().props['data-mode'],'newline');assert.equal(text(output()),'Unsent draft');assert.equal(mounts,1);
  assert.equal(saved.get('amplifier.composer.returnKey.v1'),'newline');
  await act(async()=>root.update(render()));
  assert.equal(select().props.value,'newline');assert.equal(mounts,1);
  // Cross-tab storage updates use the same local setting, not host actions.
  saved.set('amplifier.composer.returnKey.v1','automatic');
  const changed=new Event('storage');changed.key='amplifier.composer.returnKey.v1';
  await act(async()=>events.dispatchEvent(changed));
  assert.equal(output().props['data-mode'],'desktop-send');assert.equal(select().props.value,'automatic');
  // A failed safe preference write takes effect in this tab, with honest help.
  failWrite=true;
  await act(async()=>select().props.onChange({target:{value:'newline'}}));
  assert.equal(output().props['data-mode'],'newline');
  assert.match(text(root.root.findByProps({role:'alert'})),/tab only/);
  assert.equal(text(output()),'Unsent draft');assert.equal(mounts,1);
 }finally{await act(async()=>root?.unmount());}
});