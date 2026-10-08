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
