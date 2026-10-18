import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {isVoiceWorkMessage,voiceWorkLabel} from '../src/voice-work-message.js';
import {foldInterimRows} from '../src/interim-replies.js';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MessageEntry,StreamingMessage,newestMessageIds}=await vite.ssrLoadModule('/src/message-actions.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>vite.close());
const backend={id:'work',role:'assistant',source:'amplifier',via:'call',inputId:'voice:call:request',generationId:'generation',text:'The requested change is complete.',createdAt:100};
const completed={version:1,status:'completed',phase:'final',generationId:'generation',finalMessageIds:['work']};
const props=message=>({message,session:{id:'chat'},state:{view:{}},act:()=>{},working:false});
const render=value=>React.createElement(MessageEntry,value);

test('only explicit backend voice provenance changes presentation, never matching prose',()=>{
 assert.equal(isVoiceWorkMessage(backend),true);
 assert.equal(isVoiceWorkMessage({...backend,via:'chat'}),true,'explicit voice input survives channel repair');
 for(const changes of [{source:undefined},{source:'native',via:'chat',inputId:undefined},{source:'amplifier',via:'chat',inputId:'typed'},{voiceId:'call'},{role:'user'}]){
  const message={...backend,...changes};assert.equal(isVoiceWorkMessage(message),false);
  const html=renderToStaticMarkup(render(props(message)));assert.doesNotMatch(html,/a-voice-work-summary/);assert.match(html,/The requested change is complete/);
 }
 const html=renderToStaticMarkup(render(props(backend)));
 assert.match(html,/aria-label="Amplifier work update"/);assert.match(html,/From your voice request/);
 assert.match(html,/aria-expanded="false"/);assert.doesNotMatch(html,/The requested change is complete|via call|a-assistant/);
});

test('result label requires the exact qualified final-message receipt',()=>{
 assert.equal(voiceWorkLabel({...backend,response:completed}),'Amplifier result');
 for(const response of [{...completed,generationId:'other'},{...completed,finalMessageIds:['other']},{...completed,status:'running'},{...completed,phase:'interim'}])assert.equal(voiceWorkLabel({...backend,response}),'Amplifier work update');
 assert.equal(voiceWorkLabel({...backend,source:'interrupted-stream',partial:true}),'Amplifier work interrupted');
});

test('streaming uses verified input provenance while typed and unknown streams keep their layout',()=>{
 const session={id:'chat',streamingId:'stream',streaming:'Live **backend** text'};
 const renderStream=value=>renderToStaticMarkup(React.createElement(StreamingMessage,{session:value,state:{view:{}}}));
 const voice=renderStream({...session,streamingInputId:'voice:call:input'});
 assert.match(voice,/Amplifier work update/);assert.doesNotMatch(voice,/Live|a-assistant/);
 for(const streamingInputId of [undefined,'typed']){
  const ordinary=renderStream({...session,streamingInputId});
  assert.match(ordinary,/a-message a-assistant/);assert.match(ordinary,/Live <strong>backend<\/strong> text/);
 }
 assert.equal(renderStream({...session,streaming:''}),'');
 const interrupted=renderToStaticMarkup(render(props({...backend,source:'interrupted-stream',partial:true,via:'call'})));
 assert.match(interrupted,/Amplifier work interrupted/);assert.doesNotMatch(interrupted,/a-assistant/);
});

test('expand retains rich text, copy and fork; updates retain expansion and reveal opens exact identity',async()=>{
 let root,value=props({...backend,response:completed,nativeMessageId:'native-work'});const calls=[];
 value.act=(...args)=>calls.push(args);value.forkTurn={messageId:'user',turn:1};
 const summary=()=>root.root.findByProps({className:'a-voice-work-summary'});
 try{
  await act(async()=>{root=create(render(value))});
  const controlled=summary().props['aria-controls'];assert.equal(summary().props['aria-expanded'],false);
  assert.equal(root.root.findAllByProps({id:controlled}).length,0);
  await act(async()=>summary().props.onClick());
  assert.equal(root.root.findByProps({id:controlled}).props.role,'region');
  assert.ok(JSON.stringify(root.toJSON()).includes(backend.text));
  await act(async()=>root.root.findByProps({'aria-label':'Copy message as Markdown'}).props.onClick());
  assert.deepEqual(calls,[['message.copy',{sessionId:'chat',messageId:'work'}]]);
  assert.equal(root.root.findAllByProps({'aria-label':'Fork conversation after turn 1'}).length,1);
  value={...value,state:{view:{draft:'Unrelated update'}}};await act(async()=>root.update(render(value)));
  assert.equal(summary().props['aria-expanded'],true);
  await act(async()=>summary().props.onClick());
  value={...value,state:{view:{messageFocus:{sessionId:'chat',messageId:'native-work',revision:1}}}};
  await act(async()=>root.update(render(value)));assert.equal(summary().props['aria-expanded'],true);
  value={...value,session:{id:'other'}};await act(async()=>root.update(render(value)));
  assert.equal(summary().props['aria-expanded'],false,'another chat cannot inherit expanded private content');
 }finally{await act(async()=>root?.unmount())}
});

test('work never replaces newest spoken reply and avoids nested interim disclosures',()=>{
 const message={...backend,response:{...completed,phase:'interim',finalMessageIds:['final'],inputIds:[backend.inputId]}};
 assert.deepEqual([...newestMessageIds([{id:'human',role:'user'},{id:'spoken',role:'assistant',voiceId:'call',via:'call'},message])],['human','spoken','work']);
 const rows=[{id:'work',kind:'message',message},{id:'spoken',kind:'message',message:{id:'spoken',role:'assistant',via:'call',voiceId:'call'}}];
 assert.deepEqual(foldInterimRows(rows,{turns:[]},true),rows);
});
