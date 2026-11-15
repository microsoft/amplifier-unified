// Hook lifecycle/owner policy with explicit DOM doubles. Rendered visibility,
// native Tab traversal and actual portals are owned by the browser journey.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useEffect} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useSettingsDrafts,useSettingsDraft,SettingsDraftProvider}=await vite.ssrLoadModule('/src/settings-drafts.jsx');
const {useSettingsHistory}=await vite.ssrLoadModule('/src/settings-layout.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.window={addEventListener(){},removeEventListener(){}};
test.after(()=>vite.close());

function dom(){
 const listeners=new Map(),events=[],doc={visibilityState:'visible',defaultView:{getComputedStyle:node=>({visibility:node.visibility||'visible'})},
  addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
 function node(name,parent=null,extra={}){
  const value={name,parent,isConnected:true,ownerDocument:doc,
   closest(selector){
    for(let n=this;n;n=n.parent){
     if(selector==='.a-dialog'&&n.modal)return n;
     if(selector.includes('[hidden]')&&(n.hidden||n.inert||n.ariaHidden))return n;
    }return null;
   },
   contains(target){for(let n=target;n;n=n.parent)if(n===this)return true;return false},
   matches(){return !!(this.disabled||this.disabledFieldset||this.ariaDisabled)},
   getAttribute(name){return this.attributes?.[name]??null},
   getClientRects(){return this.rendered===false?[]:[{width:100,height:30}]},
   querySelectorAll(){return this.candidates||[]},
   focus(){events.push(this.name);doc.activeElement=this;listeners.get('focusin')?.({target:this})},
   ...extra,
  };return value;
 }
 doc.body=node('body');doc.documentElement=node('html');doc.activeElement=doc.body;
 const owner=node('owner'),trigger=node('close',owner),editor=node('private-editor',owner),heading=node('heading',owner);
 owner.candidates=[heading,editor,trigger];
 return {doc,node,owner,trigger,editor,heading,events};
}
async function fixture({review=()=>{},discard=()=>false,target=true}={}){
 const d=dom(),ownerRef={current:d.owner};let guard,root,scope='current',dirty=true;
 const entry={label:'Private settings',dirty,discard,review,reviewTarget:target?()=>[d.editor]:undefined};
 function Draft(){useSettingsDraft('private',{...entry,dirty});return null}
 function Frame(){guard=useSettingsDrafts({ownerRef,scope});return React.createElement(SettingsDraftProvider,{value:guard.context},React.createElement(Draft),guard.dialog)}
 await act(async()=>{root=create(React.createElement(Frame),{createNodeMock:element=>element.props.role==='alertdialog'?(d.prompt=d.node('prompt')):null})});
 return {...d,ownerRef,get guard(){return guard},get root(){return root},
  async open(){d.doc.activeElement=d.trigger;let pending;await act(async()=>{pending=guard.confirmClose()});return {pending}},
  async settle(choice='Keep settings open'){
   const button=root.root.findAllByType('button').find(button=>button.children.includes(choice));
   // The actual browser autofocus is emulated only here, not assumed as proof.
   d.doc.activeElement=d.node('prompt-button',d.prompt);await act(async()=>button.props.onClick());
  },
  async escape(){d.doc.activeElement=d.node('prompt-button',d.prompt);await act(async()=>root.root.findByProps({role:'alertdialog'}).props.onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}}))},
  async render(patch={}){if('scope'in patch)scope=patch.scope;if('dirty'in patch)dirty=patch.dirty;await act(async()=>root.update(React.createElement(Frame)))},
  async unmount(){await act(async()=>root.unmount())},
 };
}

for(const choice of ['Keep settings open','Escape'])test(choice+' returns focus after prompt removal, preserving shared promise and private draft',async()=>{
 const f=await fixture();
 try{
  const {pending}=await f.open();assert.equal(f.guard.confirmClose(),pending);
  if(choice==='Escape')await f.escape();else await f.settle();
  assert.equal(await pending,false);assert.equal(f.root.root.findAllByProps({role:'alertdialog'}).length,0);
  assert.equal(f.doc.activeElement,f.trigger);assert.deepEqual(f.events,['close']);assert.equal(f.guard.hasUnsaved(),true);
 }finally{await f.unmount()}
});

test('same-page Review focuses the editor, never saves or discards',async()=>{
 const calls=[],f=await fixture({review:()=>calls.push('review'),discard:()=>calls.push('discard')});
 try{
  const {pending}=await f.open();await f.settle('Review and save');
  assert.equal(await pending,false);assert.deepEqual(calls,['review']);assert.equal(f.doc.activeElement,f.editor);
  const targets=[];
  assert.equal(f.guard.takeReviewFocus(target=>targets.push(target)),true,'page orientation can preserve only this explicit committed review focus');
  assert.deepEqual(targets,[f.editor],'only the explicit active Review target can yield navigation scroll ownership');
  assert.equal(f.guard.takeReviewFocus(),false,'review ownership is consumed, not a global focus override');
  assert.equal(f.guard.hasUnsaved(),true);
 }finally{await f.unmount()}
});

test('refused discard preserves the draft and returns focus; accepted discard leaves closing focus owner alone',async()=>{
 const f=await fixture();
 try{const {pending}=await f.open();await f.settle('Discard changes');assert.equal(await pending,false);assert.equal(f.doc.activeElement,f.trigger);assert.equal(f.guard.hasUnsaved(),true)}finally{await f.unmount()}
 const accepted=await fixture({discard:()=>true});
 try{const {pending}=await accepted.open();await accepted.settle('Discard changes');assert.equal(await pending,true);assert.deepEqual(accepted.events,[])}finally{await accepted.unmount()}
});

for(const flag of ['hidden','inert','ariaHidden','disabled','disabledFieldset','rendered'])test('Keep rejects unavailable invoker: '+flag,async()=>{
 const f=await fixture();
 try{
  const {pending}=await f.open();f.trigger[flag]=flag==='rendered'?false:true;
  await f.settle();assert.equal(await pending,false);assert.equal(f.doc.activeElement,f.heading);
 }finally{await f.unmount()}
});

test('hidden Review target uses current owner fallback once; later reveal cannot replay stale focus',async()=>{
 const f=await fixture();f.editor.hidden=true;
 try{
  await f.open();await f.settle('Review and save');assert.deepEqual(f.events,['heading']);
  f.editor.hidden=false;f.doc.activeElement=f.doc.body;await f.render();
  assert.equal(f.doc.activeElement,f.doc.body);assert.deepEqual(f.events,['heading']);
  await f.open();f.editor.hidden=true;await f.settle('Review and save');
  const other=f.node('new-focus');f.doc.activeElement=other;f.editor.hidden=false;
  await f.render();assert.equal(f.doc.activeElement,other);
 }finally{await f.unmount()}
});

test('owner change, hidden owner and whole-owner unmount cancel pending restoration',async()=>{
 for(const change of ['scope','owner','hidden','unmount']){
  const f=await fixture();f.editor.hidden=true;
  try{
   const {pending}=await f.open();
   if(change==='unmount'){await f.unmount();assert.equal(await pending,false);assert.deepEqual(f.events,[]);continue}
   // Change the owner before prompt removal; stale capture cannot refocus it.
   if(change==='owner')f.ownerRef.current=f.node('replacement');
   if(change==='hidden')f.owner.hidden=true;
   if(change==='scope')await f.render({scope:'replacement'});
   await f.settle('Review and save');f.editor.hidden=false;f.doc.activeElement=f.doc.body;
   assert.deepEqual(f.events,[]);
  }finally{if(change!=='unmount')await f.unmount()}
 }
});

test('modal header invoker belongs to its Settings surface, not a background or foreign owner',async()=>{
 const f=await fixture();
 try{
  const modal=f.node('modal',null,{modal:true});f.owner.parent=modal;f.trigger.parent=modal;
  const {pending}=await f.open();await f.settle();assert.equal(await pending,false);assert.equal(f.doc.activeElement,f.trigger);
  await f.open();f.trigger.parent=f.node('background');await f.settle();assert.equal(f.doc.activeElement,f.heading);
 }finally{await f.unmount()}
});

test('workspace identity reuse and a hidden document invalidate pending focus without a global fallback',async()=>{
 for(const change of ['workspace','document']){
  const f=await fixture();f.editor.hidden=true;f.owner.attributes={'data-settings-focus-scope':'one'};
  try{
   await f.open();
   if(change==='workspace')f.owner.attributes['data-settings-focus-scope']='two';
   else f.doc.visibilityState='hidden';
   await f.settle('Review and save');assert.deepEqual(f.events,[]);
  }finally{await f.unmount()}
 }
});

test('Review respects deliberate visible destination focus instead of returning to Close or the old field',async()=>{
 let destination;const f=await fixture({review:()=>destination.focus()});destination=f.heading;
 try{
  const {pending}=await f.open();await f.settle('Review and save');
  assert.equal(await pending,false);assert.equal(f.doc.activeElement,destination);assert.deepEqual(f.events,['heading']);
 }finally{await f.unmount()}
});

test('Review receipt bounds delayed navigation, and an intervening focus permanently cancels its restore',async()=>{
 for(const interrupt of [false,true]){
  let resolve;const f=await fixture({review:()=>new Promise(done=>{resolve=done})});f.editor.hidden=true;
  try{
   await f.open();await f.settle('Review and save');assert.deepEqual(f.events,[]);
   if(interrupt){
    const newer=f.node('newer');newer.focus();newer.isConnected=false;f.doc.activeElement=f.doc.body;
   }
   f.editor.hidden=false;f.doc.activeElement=f.doc.body;
   await act(async()=>resolve());await f.render();
   assert.equal(f.doc.activeElement,interrupt?f.doc.body:f.editor);
  }finally{await f.unmount()}
 }
});

test('refused review navigation falls back once when its receipt settles, never later into a hidden draft',async()=>{
 let reject;const f=await fixture({review:()=>new Promise((_,no)=>{reject=no})});f.editor.hidden=true;
 try{
  await f.open();await f.settle('Review and save');
  f.doc.activeElement=f.doc.body;await act(async()=>reject(Error('Refused')));
  assert.equal(f.doc.activeElement,f.heading);
  f.editor.hidden=false;f.doc.activeElement=f.doc.body;await f.render();
  assert.equal(f.doc.activeElement,f.doc.body);
 }finally{await f.unmount()}
});

for(const movedFocus of [false,true])test('Review coordinates the existing history frame; newer focus='+movedFocus,async()=>{
 const previous={requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
 const frames=new Map();let next=0,root,guard,navigation,page='external-agents',visit='one';
 globalThis.requestAnimationFrame=fn=>{frames.set(++next,fn);return next};
 globalThis.cancelAnimationFrame=id=>frames.delete(id);
 const flush=()=>{const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn())};
 const d=dom(),ownerRef={current:d.owner},body={current:{scrollTop:0}};
 const focus=d.editor.focus.bind(d.editor);
 d.editor.focus=()=>{focus();body.current.scrollTop=100}; // Native focus reveal, not a geometry assertion.
 function Draft(){
  useSettingsDraft('private',{label:'Private draft',dirty:true,review:()=>show('external-agents'),reviewTarget:()=>d.editor,discard:()=>false});
  return null;
 }
 function Frame(){
  guard=useSettingsDrafts({ownerRef});
  navigation=useSettingsHistory({compact:false,trail:[{key:page}],view:{settingsRootVisit:visit},
   body,act:()=>{},close:()=>{},confirmClose:guard.confirmClose,hasUnsaved:guard.hasUnsaved});
  // Same order as SettingsExperience: history schedules its frame first, then
  // page orientation consumes the layout-committed explicit Review target.
  useEffect(()=>{guard.takeReviewFocus(navigation.preserveReviewScroll)},[page,visit]);
  return React.createElement(SettingsDraftProvider,{value:guard.context},React.createElement(Draft),guard.dialog);
 }
 function show(value){page=value;root.update(React.createElement(Frame))}
 try{
  await act(async()=>{root=create(React.createElement(Frame),{createNodeMock:element=>element.props.role==='alertdialog'?(d.prompt=d.node('prompt')):null})});
  flush();body.current.scrollTop=600;navigation.rememberScroll();
  await act(async()=>show('privacy'));flush();assert.equal(body.current.scrollTop,0);
  d.doc.activeElement=d.trigger;
  await act(async()=>{void guard.confirmClose()});
  d.doc.activeElement=d.node('prompt-button',d.prompt);
  await act(async()=>root.root.findAllByType('button').find(node=>node.children.includes('Review and save')).props.onClick());
  assert.equal(d.doc.activeElement,d.editor);assert.equal(body.current.scrollTop,100);
  assert.equal(frames.size,1,'Exercise the real pending history restore, not a synchronous surrogate');
  const newer=d.node('newer-owner');
  if(movedFocus)newer.focus();
  flush();
  assert.equal(body.current.scrollTop,movedFocus?600:100,'Only still-active explicit Review may supersede the saved history position');
  assert.equal(d.doc.activeElement,movedFocus?newer:d.editor,'History must never move unrelated focus');
  body.current.scrollTop=730;navigation.rememberScroll();
  await act(async()=>show('privacy'));flush();
  await act(async()=>show('external-agents'));flush();
  assert.equal(body.current.scrollTop,730,'Later ordinary Back/Forward-style level changes keep their saved scroll restoration');
  visit='fresh';await act(async()=>root.update(React.createElement(Frame)));flush();
  assert.equal(body.current.scrollTop,0,'A later new root visit is not suppressed by a stale Review marker');
 }finally{
  if(root)await act(async()=>root.unmount());
  Object.assign(globalThis,previous);
 }
});