import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SettingsDraftProvider,useSettingsDraft,useSettingsDrafts,useGuardedSettingsPanel}=await server.ssrLoadModule('/src/settings-drafts.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const handlers=new Map();globalThis.window={addEventListener:(key,fn)=>handlers.set(key,fn),removeEventListener:key=>handlers.delete(key)};
test.after(()=>server.close());
function Draft({entry}){useSettingsDraft('connection',entry);return null}
let guard;
function Fixture({entry}){guard=useSettingsDrafts();return React.createElement(SettingsDraftProvider,{value:guard.context},React.createElement(Draft,{entry}),guard.dialog)}
const click=(root,label)=>root.root.findAllByType('button').find(button=>button.children.includes(label)).props.onClick();
test('closing dirty connection setup requires review, keep, or explicit discard; secrets stay out of the prompt',async()=>{
 const calls=[],entry={label:'AI connection setup',dirty:true,review:()=>calls.push('review'),discard:()=>calls.push('discard')};let root,pending;
 await renderAct(async()=>root=create(React.createElement(Fixture,{entry})));
 assert.equal(guard.count,1);assert.ok(handlers.has('beforeunload'));
 await renderAct(async()=>{pending=guard.confirmClose()});
 assert.equal(root.root.findByProps({role:'alertdialog'}).props['aria-modal'],'true');
 await renderAct(async()=>click(root,'Keep settings open'));assert.equal(await pending,false);assert.deepEqual(calls,[]);
 await renderAct(async()=>{pending=guard.confirmClose()});
 await renderAct(async()=>click(root,'Review and save'));assert.equal(await pending,false);assert.deepEqual(calls,['review']);
 await renderAct(async()=>{pending=guard.confirmClose();assert.equal(guard.confirmClose(),pending)});
 await renderAct(async()=>click(root,'Discard changes'));assert.equal(await pending,true);assert.deepEqual(calls,['review','discard']);
 await renderAct(async()=>root.update(React.createElement(Fixture,{entry:{...entry,dirty:false}})));
 assert.equal(guard.count,0);assert.equal(await guard.confirmClose(),true);assert.equal(handlers.has('beforeunload'),false);
 await renderAct(async()=>root.unmount());
});

test('incoming shared panel changes retain the editor until confirmed, including a newer request',async()=>{
 let visible,finish,dirty=true,root;const restored=[],navigation={current:{hasUnsaved:()=>dirty,confirmClose:()=>new Promise(resolve=>{finish=resolve})}};
 function Panel({requested}){visible=useGuardedSettingsPanel(requested,navigation,panel=>restored.push(panel));return React.createElement('div',{'data-panel':visible})}
 await renderAct(async()=>root=create(React.createElement(Panel,{requested:'settings'})));
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 assert.equal(visible,'settings');
 await renderAct(async()=>finish(false));assert.deepEqual(restored,['settings']);assert.equal(visible,'settings');
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'settings'})));
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 const stale=finish;
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'runtime'})));
 await renderAct(async()=>stale(true));assert.equal(visible,'settings');
 await renderAct(async()=>finish(true));assert.equal(visible,'runtime');
 await renderAct(async()=>root.update(React.createElement(Panel,{requested:'settings'})));
 dirty=false;await renderAct(async()=>root.update(React.createElement(Panel,{requested:null})));
 assert.equal(visible,null);await renderAct(async()=>root.unmount());
});
