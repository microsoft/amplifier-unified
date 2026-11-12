import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {hasAppearanceIdentity} from '../src/appearance-composition.js';
import {createAppearancePreferenceClient} from '../src/appearance-preferences.js';
import {createShellHttpFixture} from './shell-http-fixture.mjs';

const source=path=>readFileSync(new URL(path,import.meta.url),'utf8');
const cacheKey='amplifier.appearance-composition.v4';
const namespace='amplifier.appearance.v2:'+'a'.repeat(32);

test('lifecycle init probe excludes sandboxed child frames before any storage or DOM access',()=>{
 const journey=source('./appearance-lifecycle-browser.mjs');
 const start=journey.indexOf(' await page.addInitScript(');
 const end=journey.indexOf(' },{retained,cacheKey});',start);
 assert.ok(start>=0&&end>start);
 const callback=runInNewContext('('+journey.slice(start+' await page.addInitScript('.length,end)+' })');
 let accessed=false;
 const denied=()=>{accessed=true;throw Error('Opaque sandbox storage must not be accessed')};
 const storage=new Proxy({},{get:denied});
 const sandbox={top:{}};
 // Compile the actual callback in a context with throwing browser APIs.
 const script='('+journey.slice(start+' await page.addInitScript('.length,end)+' })({retained:{},cacheKey:"probe"})';
 runInNewContext(script,{window:sandbox,sessionStorage:storage,localStorage:storage,
  document:new Proxy({},{get:denied}),requestAnimationFrame:denied});
 assert.equal(typeof callback,'function');assert.equal(accessed,false);
 // The main frame must still run its seeding path; errors are not swallowed.
 const top={};top.top=top;
 assert.throws(()=>runInNewContext(script,{window:top,sessionStorage:storage}),/Opaque sandbox/);
 assert.equal(accessed,true);
 assert.match(journey,/assert\.deepEqual\(evidence\.pageErrors,\[\]\)/,'All actual page errors remain fatal');
});

function boot(records){
 const reads=[],writes=[],deletes=[],styles={},root={style:{setProperty:(key,value)=>{styles[key]=value}},dataset:{}};
 runInNewContext(source('../public/appearance-bootstrap.js'),{
  document:{documentElement:root,querySelector:()=>({})},
  sessionStorage:{
   getItem:key=>{reads.push(key);return records.get(key)??null},
   setItem:(key,value)=>{writes.push([key,value]);records.set(key,value)},
   removeItem:key=>{deletes.push(key);records.delete(key)},
   clear:()=>{deletes.push('*');records.clear()},
  },
  CSS:{supports:()=>true},
  matchMedia:()=>({matches:false}),
 });
 return {reads,writes,deletes,styles,root};
}

test('production first paint does not read, change, migrate or activate retained legacy or 0.80.1 caches',()=>{
 const saved=JSON.stringify({version:1,scheme:'dark',colors:{dark:{background:'#111111',foreground:'#eeeeee'}}});
 const records=new Map([
  ['amplifier.appearance',saved],
  ['amplifier.appearance-composition.v3',JSON.stringify({version:3,themeKey:'retained',scheme:'dark'})],
  ['amplifier.appearance-experiment-0801-composition.v3',JSON.stringify({
   version:3,themeKey:'trial',activeScope:'shared',storageVersion:'missing',
   compositionId:'trial-composition',scheme:'dark',colors:{dark:{background:'#111111',foreground:'#eeeeee'}},
  })],
 ]);
 const before=[...records];
 const result=boot(records);
 assert.deepEqual(result.reads,[cacheKey]);
 assert.deepEqual(result.styles,{});
 assert.deepEqual(result.writes,[]);
 assert.deepEqual(result.deletes,[]);
 assert.deepEqual([...records],before);
});

test('production first paint accepts only its namespaced v4 confirmed composition cache',()=>{
 const records=new Map([[cacheKey,JSON.stringify({
  version:4,namespace,themeKey:'production',activeScope:'shared',storageVersion:'missing',
  compositionId:'composition-1',scheme:'light',
  colors:{light:{background:'#ffffff',foreground:'#222222',chrome:'#ffffff'}},
 })]]);
 const result=boot(records);
 assert.equal(result.styles['--boot-bg'],'#ffffff');
 assert.equal(result.styles['--boot-ink'],'#222222');
 assert.equal(result.root.dataset.bootScheme,'light');
 assert.deepEqual(result.writes,[]);
 assert.deepEqual(result.deletes,[]);
});

test('paired production candidate requires identity and availability, not an experiment flag or legacy rendering fallback',()=>{
 const main=source('../src/main.jsx'),runtime=source('../src/shell/runtime.jsx');
 assert.match(main,/if\(!hasAppearanceIdentity\(appearance\)\|\|appearance\.availability==='failed'\)\{appearancePreferenceClient\.current\.disconnect\(\);return\}/);
 assert.match(main,/if\(receipt\.result\.availability==='failed'\|\|!hasAppearanceIdentity\(receipt\.result\)\)/);
 assert.match(main,/const appearance=hasAppearanceIdentity\(state\?\.appearance\)&&state\.appearance\.availability!=='failed'\?state\.appearance:\{\}/);
 assert.match(main,/if\(!hasAppearanceIdentity\(state\.appearance\)\|\|state\.appearance\.availability==='failed'\)return .*<h1>Appearance unavailable<\/h1>/);
 assert.match(main,/>Retry connection<\/button>/);
 assert.doesNotMatch(main,/appearance\?\.enabled|appearance\.enabled|Appearance review unavailable|AMPLIFIER_APPEARANCE_EXPERIMENT/);
 const readSnapshot=main.slice(main.indexOf(' const readAppearanceSnapshot='),main.indexOf(' const observeAppearanceSnapshot='));
 assert.doesNotMatch(readSnapshot,/receipt\.result\.enabled/,'Only the appearance read gate is forbidden; unrelated enabled controls remain valid.');
 assert.match(main,/import '\.\/reference\.css'/);
 assert.doesNotMatch(main,/import .*unified\.css|useAppearanceEditor\(state\?\.theme/);
 assert.match(runtime,/displayState\?\.appearance\?\.availability!=='failed'/);
 assert.doesNotMatch(runtime,/displayState\?\.appearance\?\.enabled/);
});

test('production cache and private editor persistence are isolated from all retained keys',()=>{
 const presentation=source('../src/theme-presentation.jsx');
 assert.ok(presentation.includes(cacheKey));
 assert.doesNotMatch(presentation,/sessionStorage\.(?:getItem|setItem|removeItem)\('amplifier\.appearance(?:'|-composition\.v3'|-experiment-0801)/);
 const controller=source('../src/appearance-preferences.js');
 assert.match(controller,/storageValue\(\)\.setItem\(namespace,/);
 assert.doesNotMatch(controller,/localStorage\.clear|removeItem\(/);
 const editor=source('../src/appearance-editor.jsx');
 assert.match(editor,/appearanceEditorKey='amplifier\.appearance-editor\.v3'/);
 assert.match(editor,/record\?\.version===3/);
 assert.match(editor,/JSON\.stringify\(\{version:3,draft:value\}\)/);
 assert.doesNotMatch(editor,/appearance-editor-experiment-0801|removeItem\(|\.clear\(/);
});

test('non-appearance shell fixtures render a synthetic disconnected identity without claiming or starting profile persistence',async()=>{
 const input={revision:1,sessions:[],workspaces:[],view:{}},fixture=createShellHttpFixture(()=>input);
 const appearance=fixture.state().appearance;
 assert.equal(hasAppearanceIdentity(appearance),true);
 assert.equal(appearance.availability,'disconnected');
 assert.equal(Object.hasOwn(appearance,'enabled'),false);
 assert.equal(appearance.theme.id,'reference');assert.deepEqual(appearance.renderedPreferences,{});
 const calls=[],client=createAppearancePreferenceClient({
  dispatch:async(...args)=>{calls.push(['dispatch',...args]);throw Error('Unexpected profile protocol')},
  storage:{getItem:key=>{calls.push(['read',key]);return null},setItem:(...args)=>calls.push(['write',...args]),
   removeItem:key=>calls.push(['delete',key])},
 });
 await client.observe(appearance);
 assert.equal(client.getSnapshot().connected,false);assert.deepEqual(calls,[]);
 const request={url:()=> 'http://fixture/api/actions',postDataJSON:()=>({action:'appearance.preferences.read',args:{}})};
 const read=fixture.response(request);
 assert.deepEqual(read,{accepted:true,result:{renderedPreferences:{}}});
 assert.equal(hasAppearanceIdentity(read.result),false,'The neutral adapter does not simulate an accepted production preference read.');
 assert.equal(Object.hasOwn(input,'appearance'),false,'No fixture or production record is initialized as a side effect.');
});

test('shell adapter preserves explicit malformed production appearance failures instead of supplying neutral defaults',()=>{
 for(const appearance of [{availability:'failed',error:'Malformed production appearance record.'},null]){
  const input={revision:1,sessions:[],workspaces:[],view:{},appearance},fixture=createShellHttpFixture(()=>input);
  assert.deepEqual(fixture.state().appearance,appearance);
  assert.equal(hasAppearanceIdentity(fixture.state().appearance),false);
  assert.deepEqual(fixture.response({
   url:()=> 'http://fixture/api/actions',postDataJSON:()=>({action:'appearance.preferences.read',args:{}}),
  }).result,appearance);
 }
});

// Source migration guards only. Actual scroll, target size, footer reachability
// and responsive layouts are separately checked in the parent's paired DTU.
test('Reference retains bounded independent navigation and a separate footer',()=>{
 const css=source('../src/reference.css'),shell=source('../src/shell-panels.jsx');
 const nav=css.match(/#amp-one \.a-nav-content\s*\{([^}]+)\}/)?.[1]||'';
 for(const rule of [/overflow-y:\s*auto/,/overflow-x:\s*hidden/,/min-height:\s*0/,/flex:\s*1/])assert.match(nav,rule);
 assert.match(source('../src/work-shell.css'),/\.a-work-footer\{[^}]*flex-shrink:0/);
 assert.match(shell,/<div className="a-nav-content(?: [^"]+)?">[\s\S]*?<\/div>\s*\{footer\}/);
});

test('common forms and readers retain native geometry and bounded editing after skin consolidation',()=>{
 const forms=source('../src/settings-everyday.css'),canvas=source('../src/canvas-workspace.css'),chat=source('../src/chat-presentation.css');
 assert.match(forms,/\.a-inline-form\{display:flex/);
 assert.match(forms,/\.a-inline-form input\{flex:1;min-width:0/);
 assert.match(forms,/\.a-state-view\{[^}]*max-height:250px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere/);
 assert.match(forms,/\.a-json-editor,[^{]*\.a-full-plan\{[^}]*max-height:50vh;overflow:auto/);
 assert.match(forms,/input\[type=checkbox\],input\[type=radio\]\)\{appearance:auto/);
 assert.match(canvas,/\.a-browser-preview\{display:flex;flex-direction:column;min-height:0/);
 assert.match(canvas,/\.a-canvas-address input\{min-width:0;flex:1/);
 assert.match(canvas,/\.a-canvas-file-form\{[^}]*overflow:auto;flex-shrink:0/);
 assert.match(chat,/\.a-message-editor textarea\{[^}]*max-height:45vh/);
 assert.match(source('../src/reference.css'),/\.a-approvals\s*\{[^}]*max-height:\s*30vh;[^}]*overflow:\s*auto/);
});

test('material workspace paint follows semantic content kind, not tab position or labels',()=>{
 const css=source('../src/adaptive-work.css');
 assert.match(css,/\.a-adaptive-home\[data-content-kind=material\]\{background:var\(--a-surface\)\}/);
 assert.doesNotMatch(css,/a-work-tabs>button\[aria-pressed=true\]:not\(:first-child\)/);
 assert.match(source('../src/work-shell.jsx'),/contentKind=\{surface==='workspace'&&tab!=='chats'\?'material':'landing'\}/);
});

test('production Settings instances share the persistent client-document preview owner',()=>{
 const main=source('../src/main.jsx'),settings=source('../src/appearance-settings.jsx');
 assert.match(main,/const appearancePreviewOwner=useRef\(\{\}\)/);
 assert.match(main,/<AppearanceSettings [^>]*previewOwner=\{appearancePreviewOwner\}/);
 assert.match(settings,/const previewOwner=clientPreviewOwner\|\|ownPreviewOwner/);
});

test('common dialog separation honors opaque accessibility preferences without changing content paint',()=>{
 const css=source('../src/reference.css');
 const overlay=css.match(/#amp-one \.a-overlay\s*\{([^}]+)\}/)?.[1]||'';
 assert.match(overlay,/background-color:\s*color-mix\(in srgb, var\(--a-ink\) 24%, transparent\)/);
 assert.match(overlay,/backdrop-filter:\s*blur\(6px\)/);
 for(const [condition,color] of [['prefers-reduced-transparency: reduce','var(--a-bg)'],['forced-colors: active','Canvas']]){
  const block=css.slice(css.indexOf(`@media (${condition})`)).split('\n}')[0];
  assert.ok(block.includes('background-color: '+color+';'));
  assert.match(block,/backdrop-filter:\s*none/);
  assert.doesNotMatch(block,/pointer-events|opacity:|background-image|\.a-dialog\s*\{/);
 }
});

test('Markdown table header contrast is shared and token-based, not an appearance workaround',()=>{
 const css=source('../src/reference.css');
 assert.match(css,/#amp-one \.a-markdown th\s*\{\s*background-color:\s*var\(--a-soft\);\s*font-weight:\s*650;\s*\}/);
});

test('common typography keeps independent UI, heading, prose and code roles without forcing appearance fonts',()=>{
 const css=source('../src/reference.css');
 const root=css.match(/#amp-one\s*\{([^}]+)\}/)?.[1]||'';
 assert.match(root,/--a-font-ui:\s*ui-sans-serif, system-ui/);
 assert.match(root,/font-family:\s*var\(--a-font-ui\)/);
 assert.match(root,/--a-font-heading:\s*var\(--a-font-ui\)/);
 assert.match(root,/--a-font-chat:\s*var\(--a-font-ui\)/);
 assert.match(root,/--a-font-code:\s*ui-monospace/);
 assert.match(css,/#amp-one :where\(pre, code, kbd, samp\)\s*\{\s*font-family:\s*var\(--a-font-code\)/);
 assert.match(css,/#amp-one \.a-markdown\s*\{[^}]*font-family:\s*var\(--a-font-chat\)/);
 assert.doesNotMatch(css,/font-family:[^;{}]*!important/);
});

test('Appearance accent swatch keeps a native 48px target inside the wrapping reset row',()=>{
 const css=source('../src/appearance-settings.css'),settings=source('../src/appearance-settings.jsx');
 const picker=css.match(/#amp-one \.a-appearance-settings \.a-appearance-control input\[type=color\]\{([^}]+)\}/)?.[1]||'';
 for(const rule of ['flex:0 0 48px','width:48px','inline-size:48px','min-width:48px','min-inline-size:48px','height:48px','padding:3px','border:1px solid var(--a-line)']){
  assert.ok(picker.split(';').includes(rule),`Missing swatch geometry: ${rule}`);
 }
 assert.doesNotMatch(css,/\.a-appearance-control>input\[type=color\]/,'The input now lives inside a shared wrapping row.');
 assert.match(css,/\.a-appearance-settings \.a-detail-choices\{[^}]*flex-wrap:wrap/);
 assert.match(settings,/<div className="a-detail-choices"><input type="color" aria-label="Accent color"/);
 assert.match(settings,/>Use appearance accent<\/button><\/div>/);
 assert.doesNotMatch(picker,/appearance:none|pointer-events|!important|#[0-9a-f]{3,8}\b/i);
});

test('Appearance preferences finish quietly without removing pending, recovery or library feedback',()=>{
 const settings=source('../src/appearance-settings.jsx'),main=source('../src/main.jsx');
 assert.doesNotMatch(settings,/onPreferenceUndo|undoPreference|undoProfile|runFieldAction|focusAfterUndo|fieldRetries/);
 assert.doesNotMatch(settings,/message:'(?:Saved\.|Using appearance accent\.|Appearance profile changed\.|Preference change undone\.)'/);
 assert.doesNotMatch(main,/undoAppearancePreference|onPreferenceUndo/);
 assert.match(settings,/status:'pending',message:'Saving…'/);
 assert.match(settings,/data-action="appearance\.preferences\.retry"/);
 assert.match(settings,/>Use appearance accent<\/button>/);
 assert.match(settings,/>Restore neutral appearance<\/button>/);
 assert.match(settings,/Saved to your appearance library/);
 assert.match(settings,/added to your library/);
 assert.match(settings,/>Cancel preview<\/button>/);
});