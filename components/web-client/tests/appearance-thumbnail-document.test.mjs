import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {appearanceThumbnailDocument,thumbnailScene} from '../src/appearance-thumbnail-document.js';
const make=(css='',scheme='light')=>appearanceThumbnailDocument({css,scheme,hostCss:'#amp-one{color:black}',markup:'<main>Synthetic specimen</main>'});
test('thumbnail generation is deterministic, scheme-specific and versioned',()=>{
 assert.equal(make('a{}'),make('a{}'));assert.notEqual(make('a{}'),make('a{}','dark'));
 assert.match(make('a{}','dark'),/data-theme-scheme="dark"/);
 assert.match(make(),new RegExp(`data-thumbnail-scene="${thumbnailScene.version}"`));
 assert.match(make(),/inert/);assert.match(make(),/animation:none!important/);
});
test('CSS cannot escape the style element or inject script/markup',()=>{
 const html=make('p::before{content:"</style><script>bad()</script>"}');
 assert.doesNotMatch(html,/<script>/);assert.match(html,/\\3c \/style>/);
 assert.equal((html.match(/<style>/g)||[]).length,2);
});
test('document denies network, execution, form submission and base changes',()=>{
 const html=make();
 for(const text of ["default-src 'none'","img-src data:","font-src data:","base-uri 'none'","form-action 'none'"])
  assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/script-src|https?:\/\//);
});
test('unknown modes and oversized/non-string CSS fail closed',()=>{
 assert.throws(()=>make('','system'),/color mode/);
 assert.throws(()=>make(null),/Invalid/);
 assert.throws(()=>make('x'.repeat(1000001)),/Invalid/);
});
test('miniature conversation uses the native shared artwork consumer, not preview-only paint',()=>{
 const scene=readFileSync(new URL('../src/appearance-thumbnail-scene.jsx',import.meta.url),'utf8');
 const refresh=readFileSync(new URL('../src/main-refresh.css',import.meta.url),'utf8');
 assert.match(scene,/<main className="a-conversation" data-part="conversation">/);
 assert.match(scene,/import refresh from '\.\/main-refresh\.css\?raw'/);
 assert.match(refresh,/#amp-one\.a-approachable-shell \.a-conversation\{[^}]*background-image:var\(--a-conversation-art,none\)/);
 const sceneLayout=make().split('<style>')[2].split('</style>')[0];
 assert.doesNotMatch(sceneLayout,/background(?:-image)?\s*:|--a-conversation-art/,'Scene geometry must not duplicate shared artwork paint');
});
test('native miniature remains a bounded chat surface in both color modes',()=>{
 for(const scheme of ['light','dark']){
  const html=make('',scheme);
  assert.match(html,/<div id="amp-one"[^>]*data-work-surface="chat"/,'Shared non-chat CSS must not hide conversation children');
  assert.match(html,/\.a-thumbnail-workspace\{display:grid;grid-template-columns:180px minmax\(0,1fr\);flex:1;min-height:0\}/);
  assert.match(html,/\[data-part=conversation\]\{display:flex;flex-direction:column;min-width:0;min-height:0;margin:8px\}/);
  assert.match(html,/\[data-part=messages\]\{position:relative;flex:1;min-height:0;padding:20px;overflow:hidden\}/);
 }
});