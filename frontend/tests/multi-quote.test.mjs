import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TextSelection} from 'prosemirror-state';
import {composerState,composerDocument,composerMarkdown,insertQuote,changeQuote} from '../src/markdown-composer.js';
const quote=id=>({id,sessionId:'synthetic',messageId:id,speaker:'Amplifier',origin:'assistant',role:'assistant',kind:'passage',sourceDigest:'a'.repeat(64),spans:[{start:1,end:30}],excerpt:'  café e\u0301 東京\n\n• one\n    code\tvalue\n',truncated:false});
function editor(text){const view={state:composerState(text),composing:false};view.dispatch=tr=>view.state=view.state.apply(tr);return view;}
const at=view=>({doc:view.state.doc,pos:view.state.selection.to});
const texts=view=>(composerDocument(view.state.doc)?.segments||[]).filter(x=>x.type==='text').map(x=>x.text);
const quotes=view=>composerDocument(view.state.doc).segments.filter(x=>x.type==='quote');
test('three references accumulate; quote-only swaps/removal retain every commentary segment',()=>{
 const view=editor('Before\n\nBetween\n\nAfter');
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:7});
 insertQuote(view,quote('second'),{doc:view.state.doc,pos:view.state.doc.content.size});
 insertQuote(view,quote('first'),{doc:view.state.doc,pos:view.state.doc.content.size});
 const original=quotes(view),commentary=texts(view);assert.equal(original.length,3);assert.equal(new Set(original.map(x=>x.instanceId)).size,3);
 changeQuote(view,original[1].instanceId,-1);
 assert.deepEqual(quotes(view).map(x=>x.quote.id),['second','first','first']);assert.deepEqual(texts(view),commentary);
 changeQuote(view,original[0].instanceId,0);
 assert.deepEqual(quotes(view).map(x=>x.instanceId),[original[1].instanceId,original[2].instanceId]);assert.deepEqual(texts(view),commentary);
 assert.equal(quotes(view)[0].quote.excerpt,quote('second').excerpt);
 const restored=editor('');restored.state=composerState(composerMarkdown(view.state.doc),composerDocument(view.state.doc));
 assert.deepEqual(composerDocument(restored.state.doc),composerDocument(view.state.doc));
});
test('held insertion honors captured caret; changed draft and IME reject without mutation',()=>{
 const view=editor('Before and after'),captured={doc:view.state.doc,pos:7};
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1)));
 insertQuote(view,quote('first'),captured);
 const segments=composerDocument(view.state.doc).segments;assert.equal(segments[0].text,'Before');assert.equal(segments[1].type,'quote');assert.equal(segments[2].text,' and after');
 const old=at(view);view.dispatch(view.state.tr.insertText('new',1));const before=view.state.doc;
 assert.throws(()=>insertQuote(view,quote('second'),old),/draft changed/);assert.equal(view.state.doc,before);
 view.composing=true;assert.throws(()=>insertQuote(view,quote('second'),at(view)),/draft changed/);assert.equal(view.state.doc,before);
});
test('selected commentary is not replaced by adding a reference',()=>{
 const view=editor('Retain these words');view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,7)));
 insertQuote(view,quote('source'),at(view));
 assert.equal(texts(view).join(''),'Retain these words');
});
