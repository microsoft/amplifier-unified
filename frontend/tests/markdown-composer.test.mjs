import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composerState,composerMarkdown,parseComposer,composerNewline,composerClipboard} from '../src/markdown-composer.js';
function editor(){const view={state:composerState(''),composing:false};view.dispatch=tr=>{view.state=view.state.apply(tr)};return view;}
function type(view,text){for(const char of text){const {from,to}=view.state.selection;const handled=view.state.plugins.some(plugin=>plugin.props.handleTextInput?.(view,from,to,char,()=>view.state.tr.insertText(char)));if(!handled)view.dispatch(view.state.tr.insertText(char));}}
test('closing Markdown marks format immediately and stop the mark at the cursor',()=>{
 const view=editor();type(view,'**bold** _emphasis_ `code` plain');
 assert.equal(composerMarkdown(view.state.doc),'**bold** *emphasis* `code` plain');
 assert.equal(view.state.doc.textContent,'bold emphasis code plain');
 assert.deepEqual(view.state.doc.firstChild.lastChild.marks,[]);
});
test('headings and ordered/unordered lists use Markdown and continue through newline',()=>{
 for(const [prefix,node] of [['# ','heading'],['* ','bullet_list'],['1. ','ordered_list']]){
  const view=editor();type(view,prefix+'First');assert.equal(view.state.doc.firstChild.type.name,node);
  composerNewline(view.state,view.dispatch);type(view,'Second');
  if(node.endsWith('list'))assert.equal(view.state.doc.firstChild.childCount,2);
  assert.match(composerMarkdown(view.state.doc),/Second/);
 }
});
test('code remains literal and saved Markdown parses back to equivalent content',()=>{
 const value='## Header\n\n* One\n* Two\n\n```js\nconst raw = "**not bold**";\n```';
 const doc=parseComposer(value);assert.ok(parseComposer(composerMarkdown(doc)).eq(doc));
 const view=editor();type(view,'```js ');type(view,'**literal**');
 assert.equal(view.state.doc.firstChild.type.name,'code_block');assert.equal(view.state.doc.textContent,'**literal**');
});
test('image references stay textual and do not create an image element',()=>{
 const doc=parseComposer('![diagram](https://example.test/image.png)');
 const image=doc.firstChild.firstChild;assert.equal(image.type.name,'image');
 assert.equal(image.type.spec.toDOM(image)[0],'span');
 assert.equal(composerMarkdown(doc),'![diagram](https://example.test/image.png)');
});

test('copying rich text keeps Markdown marks for plain-text paste',()=>{
 const doc=parseComposer('Keep **this** and *that*.');
 assert.equal(composerClipboard(doc.slice(6,10)),'**this**');
});

import {TextSelection,AllSelection} from 'prosemirror-state';
import {formatComposerBlock,setComposerLink} from '../src/markdown-composer.js';
test('raw and labeled links survive paste, typing and Markdown round trips',()=>{
 for(const text of ['https://example.com/path?q=1','[Example](https://example.com/path)']){
  const doc=parseComposer(text);assert.equal(doc.firstChild.firstChild.marks[0].type.name,'link');
  assert.ok(parseComposer(composerMarkdown(doc)).eq(doc));
  const view=editor();type(view,text+' ');assert.ok(view.state.doc.firstChild.firstChild.marks.some(m=>m.type.name==='link'));
 }
 for(const text of ['[bad](javascript:alert)','[bad](data:text/html,bad)'])assert.equal(parseComposer(text).firstChild.firstChild.marks.length,0);
});
test('block formatting converts only selected list items and preserves inline marks',()=>{
 const view={state:composerState('- **one**\n- two\n- three'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};
 let pos;view.state.doc.descendants((node,p)=>{if(node.isText&&node.text==='two')pos=p});
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,pos,pos+3)));
 formatComposerBlock(view,'h2');
 assert.match(composerMarkdown(view.state.doc),/## two/);assert.match(composerMarkdown(view.state.doc),/\*\*one\*\*/);
 assert.equal(view.state.doc.lastChild.type.name,'bullet_list');
 formatComposerBlock(view,'ordered_list');assert.match(composerMarkdown(view.state.doc),/1\. two/);
 formatComposerBlock(view,'text');assert.equal(view.state.selection.$from.parent.type.name,'paragraph');
});
test('link edits are undoable, preserve labels and reject unsafe schemes',()=>{
 const view={state:composerState('label'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};
 view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,6)));
 assert.equal(setComposerLink(view,'javascript:alert(1)'),false);
 assert.equal(setComposerLink(view,'https://example.com'),true);assert.equal(composerMarkdown(view.state.doc),'[label](https://example.com)');
 assert.equal(setComposerLink(view,''),true);assert.equal(composerMarkdown(view.state.doc),'label');
});

test('select all converts list types without nesting',()=>{
 const view={state:composerState('1. **one**'),focus(){}};view.dispatch=tr=>{view.state=view.state.apply(tr)};view.dispatch(view.state.tr.setSelection(new AllSelection(view.state.doc)));
 formatComposerBlock(view,'bullet_list');assert.equal(view.state.doc.firstChild.firstChild.firstChild.type.name,'paragraph');assert.equal(view.state.doc.textContent,'one');
});
