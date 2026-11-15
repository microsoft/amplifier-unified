import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TextSelection} from 'prosemirror-state';
import {undo,redo} from 'prosemirror-history';
import {composerState,composerMarkdown,composerDocument,composerSendDocument,insertQuote} from '../src/markdown-composer.js';
import {automaticReturnMode,returnKeyMode,returnKeyHelp,composerReturnKeyDown,composerReturnBeforeInput,composerReturnEvents,composingReturn,createReturnKeyPreference,RETURN_KEY_STORAGE} from '../src/composer-return.js';

const desktop=(nav={},queries={})=>({
 navigator:{userAgent:'Desktop browser',maxTouchPoints:0,...nav},
 matchMedia:query=>({matches:queries[query]??['(pointer: fine)','(hover: hover)'].includes(query)}),
});
test('Automatic sends only with affirmative desktop-only evidence',()=>{
 assert.equal(automaticReturnMode(desktop()),'desktop-send');
 for(const environment of [
  desktop({userAgent:'Mozilla Android tablet'}),
  desktop({userAgent:'Mozilla iPhone Mobile'}),
  desktop({userAgent:'Mozilla iPad'}),
  desktop({userAgent:'Mozilla MacIntel',platform:'MacIntel',maxTouchPoints:5}),
  desktop({maxTouchPoints:1}), // fine/hover hybrid is still touch-capable
  desktop({userAgentData:{mobile:true}}),
  desktop({maxTouchPoints:undefined}),
  desktop({}, {'(any-pointer: coarse)':true}),
  desktop({}, {'(pointer: fine)':false}),
  desktop({}, {'(hover: hover)':false}),
  {},{navigator:{maxTouchPoints:0}},
  {...desktop(),matchMedia:()=>{throw Error('unavailable');}},
  {...desktop(),matchMedia:()=>({})},
 ])assert.equal(automaticReturnMode(environment),'newline');
 // Layout and key identity are intentionally not inputs.
 for(const innerWidth of [320,390,1280])assert.equal(automaticReturnMode({...desktop(),innerWidth}),'desktop-send');
 assert.equal(returnKeyMode('newline','desktop-send'),'newline');
 assert.equal(returnKeyMode('automatic','desktop-send'),'desktop-send');
 assert.equal(returnKeyMode('automatic','newline'),'newline');
 assert.equal(returnKeyMode('invalid','desktop-send'),'newline');
 assert.match(returnKeyHelp('newline'),/Use Send/);
 assert.match(returnKeyHelp('desktop-send'),/Ctrl\+Enter/);
});

function editor(text='First'){
 const view={state:composerState(text),composing:false};
 view.dispatch=tr=>view.state=view.state.apply(tr);
 view.dispatch(view.state.tr.setSelection(TextSelection.atEnd(view.state.doc)));
 return view;
}
function event(overrides={}){return {key:'Enter',cancelable:true,prevented:false,preventDefault(){this.prevented=true;},...overrides};}
const before=(view,overrides={},options={})=>composerReturnBeforeInput(view,event({inputType:'insertParagraph',...overrides}),options);
test('desktop plain Enter sends exactly once; repeated Enter cannot submit another draft',()=>{
 const view=editor();let sends=0;
 assert.equal(composerReturnKeyDown(view,event(),{mode:'desktop-send',onSend:()=>sends++}),true);
 view.dispatch(view.state.tr.insertText(' newer draft'));
 composerReturnKeyDown(view,event({repeat:true}),{mode:'desktop-send',onSend:()=>sends++});
 assert.equal(sends,1);assert.equal(composerMarkdown(view.state.doc),'First newer draft');
});
for(const [label,options] of [['safe default',{}],['safe mode',{mode:'newline'}],['Shift continuation',{mode:'desktop-send',shiftKey:true}],['Ctrl continuation',{mode:'desktop-send',ctrlKey:true}]])test(label+' continues prose without sending',()=>{
 const view=editor(),input=event(options);let sends=0;
 assert.equal(composerReturnKeyDown(view,input,{mode:options.mode,onSend:()=>sends++}),true);
 assert.equal(input.prevented,true);assert.equal(view.state.doc.childCount,2);
 view.dispatch(view.state.tr.insertText('Second'));
 assert.equal(composerMarkdown(view.state.doc),'First\n\nSecond');assert.equal(sends,0);
 assert.ok(composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc)).doc.eq(view.state.doc));
});
for(const inputType of ['insertParagraph','insertLineBreak'])test('bare '+inputType+' always edits and never sends',()=>{
 const view=editor(),input=event({inputType});let sends=0;
 assert.equal(composerReturnBeforeInput(view,input,{mode:'desktop-send',onSend:()=>sends++}),true);
 assert.equal(input.prevented,true);assert.equal(view.state.doc.childCount,2);assert.equal(sends,0);
});
test('noncancelable beforeinput leaves editing to the browser, with no custom transaction/send',()=>{
 const view=editor(),doc=view.state.doc,input=event({inputType:'insertParagraph',cancelable:false});
 assert.equal(composerReturnBeforeInput(view,input),false);
 assert.equal(input.prevented,false);assert.ok(view.state.doc.eq(doc));
 assert.equal(before(view,{inputType:'insertText'}),false);
});
test('a platform-deferred native Return finishes through ProseMirror, not a second custom beforeinput edit',()=>{
 const view=editor(),doc=view.state.doc,input=event({inputType:'insertParagraph'});let sends=0;
 assert.equal(composerReturnBeforeInput(view,input,{nativeReturnPending:true}),false);
 assert.equal(input.prevented,false);assert.ok(view.state.doc.eq(doc));
 // The DOM observer/platform fallback invokes the editor key handler with a
 // different synthesized event. Its editing-only mode consumes one operation.
 composerReturnKeyDown(view,event(),{mode:'newline',onSend:()=>sends++});
 assert.equal(view.state.doc.childCount,2);assert.equal(sends,0);
});
test('event routing lets ProseMirror protect and synchronize native keydown; synthesized edits never send',()=>{
 const view=editor();let sends=0;
 const events=composerReturnEvents({mode:()=>'desktop-send',onSend:()=>sends++});
 const native=event();
 assert.equal(events.observeKeyDown(view,native),false,'ProseMirror runs its guards/flush first');
 assert.equal(native.prevented,false);
 assert.equal(events.keyDown(view,event()),true,'A separate synthesized Enter is editing-only');
 assert.equal(view.state.doc.childCount,2);assert.equal(sends,0);
 // DOM flush can synthesize an editing event before passing the original
 // keydown through. It must not consume the original event identity.
 assert.equal(events.keyDown(view,native),true);assert.equal(sends,1);
 assert.equal(events.keyDown(view,native),true);assert.equal(sends,1,'Identity is usable only once');
});
test('deferred native keydown+beforeinput has one editing owner and bare beforeinput retains rich semantics',()=>{
 for(const inputType of ['insertParagraph','insertLineBreak']){
  const view=editor(),doc=view.state.doc;let sends=0;
  const events=composerReturnEvents({mode:()=>'desktop-send',onSend:()=>sends++});
  events.observeKeyDown(view,event());
  const input=event({inputType});
  assert.equal(events.beforeInput(view,input),false,'Native edit must reach ProseMirror');
  assert.equal(input.prevented,false);assert.ok(view.state.doc.eq(doc));
  events.keyDown(view,event());assert.equal(view.state.doc.childCount,2);assert.equal(sends,0);
  assert.equal(events.beforeInput(view,event({inputType})),true,'A later bare edit is handled normally');
  assert.equal(view.state.doc.childCount,3);assert.equal(sends,0);
 }
});
test('a synthesized flush edit cannot consume a platform-deferred native Return beforeinput',()=>{
 const view=editor(),events=composerReturnEvents({mode:()=>'desktop-send'});
 events.observeKeyDown(view,event());
 events.keyDown(view,event()); // Flush of a prior native mutation, not this Return
 const doc=view.state.doc,input=event({inputType:'insertParagraph'});
 assert.equal(events.beforeInput(view,input),false);
 assert.equal(input.prevented,false);assert.ok(view.state.doc.eq(doc));
});
test('IME, legacy 229, disabled and read-only adapters do not edit, prevent native commit or send',()=>{
 for(const overrides of [{isComposing:true},{keyCode:229},{viewComposing:true},{editable:false}]){
  const view=editor(),doc=view.state.doc;view.composing=!!overrides.viewComposing;
  const key=event(overrides),input=event({...overrides,inputType:'insertParagraph'});let sends=0;
  assert.equal(composerReturnKeyDown(view,key,{editable:overrides.editable,onSend:()=>sends++}),false);
  assert.equal(composerReturnBeforeInput(view,input,{editable:overrides.editable}),false);
  assert.equal(key.prevented,false);assert.equal(input.prevented,false);
  assert.ok(view.state.doc.eq(doc));assert.equal(sends,0);
  if(overrides.editable!==false)assert.ok(composingReturn(view,key),'DOM guard must stop later keymaps without preventing IME');
 }
});
test('compositionend followed by a deliberate eligible desktop Enter can send',()=>{
 const view=editor();let sends=0;
 view.composing=true;composerReturnKeyDown(view,event(),{mode:'desktop-send',onSend:()=>sends++});
 view.composing=false;composerReturnKeyDown(view,event(),{mode:'desktop-send',onSend:()=>sends++});
 assert.equal(sends,1);
});
test('safe Return retains code indentation, fence opening, Alt exit and source literals',()=>{
 const view=editor('');view.dispatch(view.state.tr.insertText('```js'));let sends=0;
 const key=overrides=>composerReturnKeyDown(view,event(overrides),{mode:'newline',onSend:()=>sends++});
 key();assert.equal(view.state.doc.firstChild.type.name,'code_block');
 view.dispatch(view.state.tr.insertText('  literal'));key();
 assert.equal(view.state.doc.textContent,'  literal\n  ');
 key({altKey:true});assert.equal(view.state.selection.$from.parent.type.name,'paragraph');
 const source=editor('Before[^n]\n\n[^n]: literal');before(source);
 assert.equal(source.state.doc.firstChild.type.name,'markdown_source');assert.match(composerMarkdown(source.state.doc),/literal\n$/);
 assert.equal(sends,0);
});
test('safe Return keeps list/task/heading/table structure, selection replacement and undo',()=>{
 for(const text of ['- First','- [x] First','# First']){
  const view=editor(text);before(view);view.dispatch(view.state.tr.insertText('Second'));
  assert.match(composerMarkdown(view.state.doc),/Second/);
  if(text.startsWith('-'))assert.equal(view.state.doc.firstChild.childCount,2);
  if(text.startsWith('- [x]'))assert.equal(view.state.doc.firstChild.child(1).attrs.checked,false);
 }
 const table=editor('| A | B |\n| --- | --- |\n| left | right |'),original=table.state.doc.firstChild;
 let pos;table.state.doc.descendants((node,p)=>{if(node.isText&&node.text==='left')pos=p+2;});
 table.dispatch(table.state.tr.setSelection(TextSelection.create(table.state.doc,pos)));before(table);
 assert.ok(table.state.doc.firstChild.eq(original));assert.equal(table.state.selection.$from.parent.type.name,'paragraph');
 const selected=editor('First second');selected.dispatch(selected.state.tr.setSelection(TextSelection.create(selected.state.doc,6,13)));
 before(selected);assert.equal(selected.state.doc.textContent,'First');
 assert.ok(undo(selected.state,selected.dispatch));assert.equal(composerMarkdown(selected.state.doc),'First second');
 assert.ok(redo(selected.state,selected.dispatch));assert.equal(selected.state.doc.childCount,2);
});
test('safe Return beside an attributed quote preserves its immutable send document',()=>{
 const view=editor();
 const quote={id:'source',sessionId:'synthetic',messageId:'source',speaker:'Amplifier',origin:'assistant',role:'assistant',excerpt:'verbatim',spans:[{start:0,end:8}],sourceDigest:'a'.repeat(64)};
 insertQuote(view,quote,{doc:view.state.doc,pos:view.state.doc.content.size});
 const original=composerSendDocument(view.state.doc).segments.find(part=>part.type==='quote');
 view.dispatch(view.state.tr.setSelection(TextSelection.atEnd(view.state.doc)));before(view);
 assert.deepEqual(composerSendDocument(view.state.doc).segments.find(part=>part.type==='quote'),original);
});

function storageFixture(){
 const data=new Map(),events=new EventTarget();
 return {data,events,storage:{getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)}};
}
test('browser preference persists independently, validates values and notifies all mounted consumers',()=>{
 const {storage,data,events}=storageFixture();
 const store=createReturnKeyPreference({storage:()=>storage,eventTarget:events});let notices=0;
 const stop=store.subscribe(()=>notices++);
 const initial=store.getSnapshot();assert.equal(initial.value,'automatic');assert.equal(store.getSnapshot(),initial);
 store.set('newline');assert.equal(data.get(RETURN_KEY_STORAGE),'newline');assert.equal(store.getSnapshot().value,'newline');assert.equal(notices,1);
 const reloaded=createReturnKeyPreference({storage:()=>storage,eventTarget:events});assert.equal(reloaded.getSnapshot().value,'newline');
 const otherBrowser=createReturnKeyPreference({storage:()=>storageFixture().storage});assert.equal(otherBrowser.getSnapshot().value,'automatic');
 store.set('send');assert.equal(notices,1);
 data.set(RETURN_KEY_STORAGE,'automatic');const changed=new Event('storage');changed.key=RETURN_KEY_STORAGE;events.dispatchEvent(changed);
 assert.equal(store.getSnapshot().value,'automatic');assert.equal(notices,2);
 stop();events.dispatchEvent(changed);assert.equal(notices,2);
});
test('unavailable or unconfirmed browser storage remains explicit; chosen safe mode applies in tab',()=>{
 for(const [storage,readable] of [[undefined,false],[{getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}},false],[{getItem:()=>null,setItem(){}},true]]){
  const store=createReturnKeyPreference({storage:()=>storage});
  if(!readable)assert.match(store.getSnapshot().error,/could not read/);
  store.set('newline');assert.equal(store.getSnapshot().value,'newline');assert.match(store.getSnapshot().error,/tab only/);
 }
 const corrupt=createReturnKeyPreference({storage:()=>({getItem:()=>'send'})});
 assert.equal(corrupt.getSnapshot().value,'automatic');assert.match(corrupt.getSnapshot().error,/invalid/);
});