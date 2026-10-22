import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DestinationHistory}=await vite.ssrLoadModule('/src/destination-history.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalDocument=globalThis.document;
globalThis.document={addEventListener(){},removeEventListener(){},activeElement:null};
test.after(()=>{globalThis.document=originalDocument;return vite.close()});
const history={ready:true,pending:false,entries:[{key:'a',kind:'chat',id:'a',label:'Alpha',workspaceLabel:'Project'},{key:'b',kind:'chat',id:'b',label:'Beta',workspaceLabel:'Project'}],cursor:1,detached:false,backKey:'a',forwardKey:null,memoryOnly:false};
const element=(props={})=>React.createElement(DestinationHistory,{history,onVisit(){},...props});
const label=(root,name)=>root.root.findByProps({'aria-label':name});

test('header always has explicit controls, with no remembered-chat return label',()=>{
 const html=renderToStaticMarkup(element());
 assert.match(html,/Back in navigation history/);assert.match(html,/Forward in navigation history/);assert.match(html,/Navigation history/);
 assert.doesNotMatch(html,/Back to chat|Working|Stop|Alpha|Beta/);
 assert.match(renderToStaticMarkup(element({history:null})),/disabled/);
});

test('one private menu opens by click, Escape restores opener, selection uses a visit key',async()=>{
 let root;const visits=[];let focus=0;
 await act(async()=>{root=create(element({onVisit:key=>visits.push(key)}))});
 await act(async()=>label(root,'Navigation history').props.onClick({currentTarget:{focus(){focus++}}}));
 assert.equal(root.root.findAllByProps({role:'menu'}).length,1);
 assert.equal(root.root.findByProps({role:'menu'}).props['data-private'],'true');
 const items=root.root.findAllByProps({role:'menuitem'});assert.equal(items[1].props['aria-current'],'page');assert.equal(items[1].props.disabled,true);
 assert.equal(items[0].props['data-view-label'],'Private navigation destination');
 await act(async()=>items[0].props.onClick());assert.deepEqual(visits,['a']);assert.equal(focus,1);
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>root.unmount());
});

test('mouse hold opens one menu and consumes the follow-up click; touch does not hold',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let root;const visits=[];
 await act(async()=>{root=create(element({onVisit:key=>visits.push(key)}))});
 const button=()=>label(root,'Back in navigation history');
 const event={pointerType:'mouse',button:0,clientX:10,clientY:10,currentTarget:{focus(){}}};
 await act(async()=>{button().props.onPointerDown(event);t.mock.timers.tick(449)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>t.mock.timers.tick(1));assert.equal(root.root.findAllByProps({role:'menu'}).length,1);
 await act(async()=>{button().props.onPointerUp();button().props.onClick({preventDefault(){}})});assert.deepEqual(visits,[]);
 await act(async()=>root.unmount());
 await act(async()=>{root=create(element({onVisit:key=>visits.push(key)}))});
 await act(async()=>{button().props.onPointerDown({...event,pointerType:'touch'});t.mock.timers.tick(500)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>button().props.onClick({preventDefault(){}}));assert.deepEqual(visits,['a']);
 await act(async()=>root.unmount());t.mock.timers.reset();
});

test('pointer movement cancels hold and pending state disables all controls',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let root;
 await act(async()=>{root=create(element())});
 const button=label(root,'Back in navigation history');
 await act(async()=>{button.props.onPointerDown({pointerType:'pen',button:0,clientX:0,clientY:0,currentTarget:{}});button.props.onPointerMove({clientX:20,clientY:0});t.mock.timers.tick(500)});
 assert.equal(root.root.findAllByProps({role:'menu'}).length,0);
 await act(async()=>root.update(element({history:{...history,pending:true}})));
 assert.ok(root.root.findAllByType('button').every(button=>button.props.disabled));
 await act(async()=>root.unmount());t.mock.timers.reset();
});
