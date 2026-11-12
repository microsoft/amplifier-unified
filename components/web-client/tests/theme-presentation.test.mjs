import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const bootstrap=readFileSync(new URL('../public/appearance-bootstrap.js',import.meta.url),'utf8');
const cacheKey='amplifier.appearance-composition.v4';
const namespace='amplifier.appearance.v2:'+'a'.repeat(32);
const replacementNamespace='amplifier.appearance.v2:'+'b'.repeat(32);
const palette={
 light:{background:'rgb(232, 238, 255)',foreground:'#263459',chrome:'rgb(20, 54, 90)'},
 dark:{background:'#151b31',foreground:'#eef2ff',chrome:'oklch(40% 0.1 260)'},
};
const saved=(scheme='light',colors=palette)=>({version:4,namespace,activeScope:'shared',storageVersion:'global-version',compositionId:'composition-selected',themeKey:'committed-theme',scheme,colors});

// Exercise the actual synchronous script. CSS.supports is a controlled boundary,
// not a substitute for browser CSS parsing or observed rendering.
function boot({value=saved(),raw=JSON.stringify(value),dark=false,storageError=false,meta=true,supports=()=>true}={}){
 const styles=new Map(),reads=[],queries=[],checked=[],chrome={content:'#e8eeff'};
 const root={dataset:{},style:{setProperty:(key,value)=>styles.set(key,value)}};
 runInNewContext(bootstrap,{
  document:{documentElement:root,querySelector:selector=>{
   assert.equal(selector,'meta[name="theme-color"]');return meta?chrome:null;
  }},
  sessionStorage:{getItem:key=>{reads.push(key);if(storageError)throw Error('Storage denied');return key===cacheKey?raw:null}},
  matchMedia:query=>{queries.push(query);return {matches:dark}},
  CSS:{supports:(property,value)=>{assert.equal(property,'color');checked.push(value);return supports(value)}},
 },{filename:'appearance-bootstrap.js',timeout:1000});
 return {root,styles,reads,queries,checked,chrome};
}

test('synchronous bootstrap restores only namespaced committed v4 colors before authoritative boot',()=>{
 for(const scheme of ['light','dark']){
  const f=boot({value:saved(scheme),dark:scheme==='light'});
  assert.equal(f.root.style.colorScheme,scheme);
  assert.equal(f.root.dataset.bootScheme,scheme);
  assert.equal(f.chrome.content,palette[scheme].chrome);
  assert.deepEqual([...f.styles],[['--boot-bg',palette[scheme].background],['--boot-ink',palette[scheme].foreground]]);
  assert.deepEqual(f.reads,[cacheKey]);assert.deepEqual(f.queries,[]);
 }
});

test('system resolves the current device scheme and missing palettes use safe defaults',()=>{
 for(const dark of [false,true]){
  const mode=dark?'dark':'light',f=boot({value:saved('system'),dark});
  assert.deepEqual(f.queries,['(prefers-color-scheme: dark)']);
  assert.equal(f.root.dataset.bootScheme,mode);assert.equal(f.chrome.content,palette[mode].chrome);
  const missing=boot({value:saved('system',{}),dark});
  assert.equal(missing.root.dataset.bootScheme,mode);
  assert.equal(missing.chrome.content,dark?'#151b31':'#e8eeff');
 }
});

test('opaque percentage alpha restores the exact rgba background, ink and chrome',()=>{
 for(const alpha of ['100%','100.0%','100.000%','1','1.0']){
  const background=`rgba(10,20,30,${alpha})`,foreground=`rgba(200,210,220,${alpha})`;
  const f=boot({value:saved('dark',{dark:{background,foreground,chrome:background}})});
  assert.equal(f.root.dataset.bootScheme,'dark');
  assert.equal(f.styles.get('--boot-bg'),background,alpha);
  assert.equal(f.styles.get('--boot-ink'),foreground,alpha);
  assert.equal(f.chrome.content,background,alpha);
 }
});

test('modern opaque colors restore while transparent backgrounds and chrome are rejected',()=>{
 for(const chrome of ['oklch(40% .1 260)','oklab(0.4 0.01 -0.1 / 1)','lab(40% 10 -20 / 100%)',
  'lch(40% 20 260)','color(display-p3 0.1 0.2 0.3)','rgb(20 54 90 / 100%)','rgba(20, 54, 90, 1)','hsla(220, 50%, 40%, 1)']){
  const f=boot({value:saved('light',{light:{...palette.light,chrome}})});
  assert.equal(f.chrome.content,chrome);assert.ok(f.checked.includes(chrome));
 }
 for(const value of ['transparent','rgba(20,54,90,0.5)','rgba(20,54,90,99%)','rgb(20 54 90 / 50%)',
  'oklch(40% .1 260 / .5)','color(display-p3 0.1 0.2 0.3 / 0)']){
  const f=boot({value:saved('dark',{dark:{background:value,foreground:'#ffffff',chrome:value}})});
  assert.equal(f.styles.get('--boot-bg'),'#151b31',value);assert.equal(f.chrome.content,'#151b31',value);
 }
});

test('malformed, unsupported and injectable colors cannot escape the CSS color boundary',()=>{
 const invalid=['url(https://example.invalid/x)','rgb(1 2 3);background:url(https://example.invalid/x)',
  '</style><script>bad()</script>','var(--preview)','currentColor','rgb(1 2 3)\nbody{}',
  'rgba(10,20,30,100x0%)','rgba(10,20,30,100%) trailing','rgb('+'1'.repeat(160)+')',
  42,{},null,'not-a-color','rgb(unsupported)'];
 for(const value of invalid){
  const f=boot({value:saved('dark',{dark:{background:value,foreground:value,chrome:value}}),
   supports:color=>!['rgb(unsupported)','rgba(10,20,30,100x0%)'].includes(color)});
  assert.equal(f.root.dataset.bootScheme,'dark');
  assert.deepEqual([...f.styles],[['--boot-bg','#151b31'],['--boot-ink','#eef2ff']],JSON.stringify(value));
  assert.equal(f.chrome.content,'#151b31',JSON.stringify(value));
 }
 // These inputs pass the controlled CSS.supports stub deliberately: opacity's
 // own anchored alpha guard still must not accept a wildcard decimal separator.
 for(const alpha of ['100x0%','100.1%','100% 0','100%%']){
  const color=`rgba(10,20,30,${alpha})`,f=boot({value:saved('dark',{dark:{background:color,chrome:color}})});
  assert.equal(f.styles.get('--boot-bg'),'#151b31',alpha);assert.equal(f.chrome.content,'#151b31',alpha);
 }
});

test('invalid, unavailable or legacy storage leaves initial boot presentation intact',()=>{
 for(const value of [null,[],{},1,{...saved(),version:1},{...saved(),version:2},{...saved(),version:3},{...saved(),version:'4'},
  {...saved(),namespace:undefined},{...saved(),namespace:''},{...saved(),namespace:'legacy'},
  {...saved(),namespace:'amplifier.appearance.v1:'+'a'.repeat(32)},
  {...saved(),namespace:'amplifier.appearance-experiment-0801:'+'a'.repeat(32)},
  {...saved(),namespace:'amplifier.appearance.v2:'+'a'.repeat(31)},
  {...saved(),namespace:'amplifier.appearance.v2:'+'A'.repeat(32)},
  {...saved(),activeScope:'preview'},{...saved(),storageVersion:''},{...saved(),compositionId:null},
  {...saved(),scheme:'device'},{...saved(),themeKey:null},{...saved(),themeKey:''},
  {...saved(),colors:null},{...saved(),colors:[]}]){
  const f=boot({value});
  assert.equal(f.styles.size,0);assert.equal(f.root.style.colorScheme,undefined);
  assert.deepEqual(Object.keys(f.root.dataset),[]);assert.equal(f.chrome.content,'#e8eeff');
 }
 for(const options of [{raw:'{'},{raw:null},{storageError:true}]){
  const f=boot(options);assert.equal(f.styles.size,0);assert.equal(f.chrome.content,'#e8eeff');assert.deepEqual(f.reads,[cacheKey]);
 }
 assert.equal(boot({meta:false}).styles.get('--boot-bg'),palette.light.background);
});

const presentation=readFileSync(new URL('../src/theme-presentation.jsx',import.meta.url),'utf8');
const editorSource=readFileSync(new URL('../src/appearance-editor.jsx',import.meta.url),'utf8');
test('actual editor key/load/write guards keep ownerless and invalid-owner drafts private without touching any storage key',()=>{
 // Execute actual guard code with single-call hook facades. React lifecycle and
 // remount recovery remain owned by appearance-settings.test.mjs.
 for(const namespace of [undefined,null,'','unattached','appearance-test',
  'amplifier.appearance.v1:'+'a'.repeat(32),'amplifier.appearance-experiment-0801:'+'a'.repeat(32),
  'amplifier.appearance.v2:'+'A'.repeat(32),'amplifier.appearance.v2:'+'a'.repeat(31)]){
  const calls=[],states=[];
  const functions=runInNewContext(editorSource.replace(/^import .*;\n/gm,'')
   .replaceAll('export function ','function ').replaceAll('export const ','const ')+
   '\n({appearanceEditorStorageKey,useAppearanceEditor})',{
   useCallback:fn=>fn,useEffect:()=>{},useRef:value=>({current:value}),
   useState:initial=>{
    const index=states.length;states.push(typeof initial==='function'?initial():initial);
    return [states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value}];
   },
  });
  assert.equal(functions.appearanceEditorStorageKey(namespace),null);
  assert.equal(functions.appearanceEditorStorageKey('amplifier.appearance.v2:'+'b'.repeat(32)),
   'amplifier.appearance-editor.v3:amplifier.appearance.v2:'+'b'.repeat(32));
  const editor=functions.useAppearanceEditor({name:'Production appearance',css:'production CSS'},'',{
   namespace,storage:()=>{calls.push('storage accessor');return {
    getItem:key=>{calls.push(['read',key]);return JSON.stringify({version:3,draft:{name:'Old unattached draft'}})},
    setItem:(...args)=>calls.push(['write',...args]),removeItem:key=>calls.push(['delete',key]),
   }},
  });
  assert.equal(editor.draft.name,'Production appearance');assert.equal(editor.draft.dirty,false);
  assert.deepEqual(calls,[]);
  editor.setDraft({...editor.draft,name:'Private pending text',dirty:true});
  assert.equal(states[0].name,'Private pending text');assert.equal(states[0].dirty,true);
  assert.match(states[1],/draft stays in this tab.*could not be saved for reload.*Keep this tab open/);
  assert.deepEqual(calls,[],'Neither load nor explicit edit may access storage, even with a null or unattached key.');
 }
});

function cacheHarness(entries=[]){
 const records=new Map(entries),reads=[],writes=[],deletes=[],bootStyles=new Map();
 const root={dataset:{themeScheme:'light'},querySelector:()=>null};
 const documentRoot={dataset:{},style:{
  setProperty:(key,value)=>bootStyles.set(key,value),
  removeProperty:key=>{bootStyles.delete(key);if(key==='color-scheme')delete documentRoot.style.colorScheme},
 }};
 const functions=runInNewContext(presentation
  .replace(/^import .*;\n/gm,'')
  .replaceAll('export function ','function ')+'\n({useAppearanceCache})',{
  useLayoutEffect:fn=>fn(),
  sessionStorage:{
   getItem(key){reads.push(key);return records.get(key)??null},
   setItem(key,value){writes.push([key,value]);records.set(key,value)},
   removeItem(key){deletes.push(key);records.delete(key)},
  },
  getComputedStyle:()=>({backgroundColor:'#ffffff',color:'#222222'}),
  document:{documentElement:documentRoot},
 });
 const update=(overrides={})=>functions.useAppearanceCache({
  root:{current:root},shell:{ready:true},namespace,scheme:'light',mode:'light',preview:false,
  compositionId:'composition-selected',activeScope:'shared',storageVersion:'global-version',confirmed:true,...overrides,
 });
 return {records,reads,writes,deletes,bootStyles,documentRoot,update};
}

test('confirmed cache hook writes the exact production v4 record and never reads or deletes retained keys',()=>{
 const legacyKeys=['amplifier.appearance','amplifier.appearance-composition.v3','amplifier.appearance-experiment-0801-composition.v3'];
 const retained=legacyKeys.map(key=>[key,JSON.stringify({...saved(),version:3})]);
 const h=cacheHarness(retained);
 h.update();
 const record=JSON.parse(h.records.get(cacheKey));
 assert.deepEqual(record,{
  version:4,namespace,activeScope:'shared',storageVersion:'global-version',compositionId:'composition-selected',
  themeKey:'composition-selected',scheme:'light',colors:{light:{background:'#ffffff',foreground:'#222222',chrome:'#ffffff'}},
 });
 assert.deepEqual(h.writes.map(([key])=>key),[cacheKey]);assert.deepEqual(h.deletes,[]);
 assert.equal(h.reads.every(key=>key===cacheKey),true);
 for(const [key,value] of retained)assert.equal(h.records.get(key),value);
});

test('cache hook remains inert for preview, unconfirmed, unready and invalid namespace states',()=>{
 for(const overrides of [{preview:true},{confirmed:false},{shell:{ready:false}},{root:{current:null}},
  {namespace:undefined},{namespace:'legacy'},{namespace:'amplifier.appearance.v2:'+'A'.repeat(32)},
  {compositionId:null},{activeScope:'preview'},{storageVersion:''}]){
  const h=cacheHarness();h.update(overrides);
  assert.deepEqual(h.writes,[],JSON.stringify(overrides));
  assert.equal(h.bootStyles.size,0,JSON.stringify(overrides));
  if(overrides.preview)assert.deepEqual(h.reads,[],'A preview never even inspects the committed cache.');
 }
});

test('matching unconfirmed browser cache is retained without writes, explicit invalidation removes it, and exact old confirmation may repopulate it',()=>{
 const prior={
  version:4,namespace,activeScope:'client',storageVersion:'confirmed-browser-v1',
  compositionId:'confirmed-browser-composition',themeKey:'confirmed-browser-composition',scheme:'light',
  colors:{light:{background:'#ffffff',foreground:'#222222',chrome:'#ffffff'}},
 };
 const raw=JSON.stringify(prior),h=cacheHarness([[cacheKey,raw]]);
 const identity={namespace,activeScope:prior.activeScope,storageVersion:prior.storageVersion,
  compositionId:prior.compositionId,scheme:prior.scheme,mode:'light'};
 h.bootStyles.set('--boot-bg',prior.colors.light.background);h.bootStyles.set('--boot-ink',prior.colors.light.foreground);
 h.documentRoot.style.colorScheme='light';h.documentRoot.dataset.bootScheme='light';
 h.update({...identity,confirmed:false});
 assert.equal(h.records.get(cacheKey),raw,'Lack of current confirmation does not discard a matching last-confirmed composition.');
 assert.deepEqual(h.writes,[]);assert.deepEqual(h.deletes,[]);
 assert.equal(h.bootStyles.get('--boot-bg'),prior.colors.light.background);
 assert.equal(h.documentRoot.style.colorScheme,'light');assert.equal(h.documentRoot.dataset.bootScheme,'light');
 h.update({...identity,confirmed:false,invalidate:true});
 assert.equal(h.records.has(cacheKey),false);assert.deepEqual(h.writes,[]);assert.deepEqual(h.deletes,[cacheKey]);
 assert.equal(h.bootStyles.size,0);assert.equal(h.documentRoot.style.colorScheme,undefined);
 assert.equal(h.documentRoot.dataset.bootScheme,undefined);
 h.update({...identity,confirmed:false});
 assert.equal(h.records.has(cacheKey),false,'A failed/in-flight state cannot recreate even the old cache without confirmation.');
 assert.deepEqual(h.writes,[]);assert.deepEqual(h.deletes,[cacheKey]);
 h.update({...identity,confirmed:true});
 assert.deepEqual(JSON.parse(h.records.get(cacheKey)),prior,'Reconfirmation can recreate the same old client identity and palette; persistent null is not required.');
 assert.deepEqual(h.writes,[[cacheKey,raw]]);assert.deepEqual(h.deletes,[cacheKey]);
 const reloaded=boot({raw:h.records.get(cacheKey)});
 assert.equal(reloaded.root.dataset.bootScheme,'light');
 assert.deepEqual([...reloaded.styles],[['--boot-bg',prior.colors.light.background],['--boot-ink',prior.colors.light.foreground]]);
 assert.equal(reloaded.chrome.content,prior.colors.light.chrome);
});

test('cache hook invalidates replacement namespace, scope, storage version and composition before confirmation',()=>{
 for(const overrides of [{namespace:replacementNamespace},{activeScope:'client'},{storageVersion:'replacement-version'},
  {compositionId:'replacement-composition'},{invalidate:true}]){
  const h=cacheHarness([[cacheKey,JSON.stringify(saved())]]);
  h.bootStyles.set('--boot-bg','#old-background');h.bootStyles.set('--boot-ink','#old-ink');
  h.documentRoot.style.colorScheme='dark';h.documentRoot.dataset.bootScheme='dark';
  h.update({...overrides,confirmed:false});
  assert.equal(h.records.has(cacheKey),false,JSON.stringify(overrides));
  assert.deepEqual(h.deletes,[cacheKey]);assert.deepEqual(h.writes,[]);
  assert.equal(h.bootStyles.size,0);assert.equal(h.documentRoot.style.colorScheme,undefined);
  assert.equal(h.documentRoot.dataset.bootScheme,undefined);
 }
});

test('cache hook cannot reuse old-version or foreign-namespace palettes in a new confirmed composition',()=>{
 const current={...saved(),themeKey:'composition-selected'};
 for(const prior of [{...current,version:3},{...current,version:'4'},{...current,namespace:replacementNamespace}]){
  const h=cacheHarness([[cacheKey,JSON.stringify(prior)]]);
  h.update();
  const next=JSON.parse(h.records.get(cacheKey));
  assert.equal(next.version,4);assert.equal(next.namespace,namespace);
  assert.equal(next.colors.dark,undefined,'Only a correctly versioned record in this exact namespace may contribute a palette.');
  assert.equal(next.colors.light.background,'#ffffff');
 }
 const h=cacheHarness([[cacheKey,JSON.stringify(current)]]);
 h.update();
 assert.deepEqual(JSON.parse(h.records.get(cacheKey)).colors.dark,palette.dark,'A matching committed record retains the other scheme.');
});

test('main cache confirmation is fenced by the exact production namespace, generation and acknowledged profile record',()=>{
 const main=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
 const start=main.indexOf(' const appearanceCacheLocal='),end=main.indexOf(' useWindowChrome(',start);
 assert.ok(start>0&&end>start);
 const local={revision:3,storageVersion:'browser-version',reported:true,error:null};
 const appearance={namespace,generation:'generation-B',availability:'connected',activeScope:'client',local};
 const ack={namespace,generation:'generation-B',revision:3,storageVersion:'browser-version',activeScope:'client'};
 const confirmed=(overrides={},ackOverrides={},status='ready')=>runInNewContext(
  main.slice(start,end)+'\nappearanceCacheConfirmed',{
   appearance:{...appearance,...overrides},appearancePreferenceStatus:{status},
   appearancePreferenceClient:{current:{getSnapshot:()=>({acknowledged:{...ack,...ackOverrides}})}},
  });
 assert.equal(confirmed(),true);
 for(const mismatch of [{namespace:replacementNamespace},{generation:'generation-A'},{revision:2},
  {storageVersion:'older-version'},{activeScope:'shared'}])assert.equal(confirmed({},mismatch),false);
 for(const overrides of [{availability:'failed'},{availability:'disconnected'},
  {local:{...local,reported:false}},{local:{...local,error:'malformed'}}])assert.equal(confirmed(overrides),false);
 for(const status of ['pending','failed','disconnected'])assert.equal(confirmed({}, {},status),false);
 assert.equal(confirmed({}, {},'stored'),true);
 assert.equal(confirmed({command:{id:'quota-select-global',status:'failed'}}),true,
  'A failed source-switch command does not prevent a fresh exact ACK of the still-active browser record from confirming its cache.');
 assert.match(main,/useAppearanceCache\(\{root,shell,namespace:appearance\.namespace,/);
 assert.match(main,/useAppearanceEditor\(state\?\.appearance\?\.theme,''(?:,|\))/);
});

test('HTML loads bootstrap synchronously after theme-color and before the app module',()=>{
 const html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),script='<script src="/appearance-bootstrap.js"></script>';
 assert.ok(html.includes(script),'Bootstrap must not be async, defer or module.');
 assert.ok(html.indexOf('<meta name="theme-color"')<html.indexOf(script));
 assert.ok(html.indexOf(script)<html.indexOf('type="module"'));
});

test('Reference retains accepted delivery recovery geometry without restoring the full skin',()=>{
 const css=readFileSync(new URL('../src/reference.css',import.meta.url),'utf8');
 assert.match(css,/\.a-message-delivery\{display:inline-flex;align-items:center;gap:\.4rem/);
 assert.match(css,/\.a-delivery-detail,\.a-delivery-confirm\{flex:0 0 100%;min-width:0;max-width:100%;box-sizing:border-box;overflow-wrap:break-word\}/);
 assert.match(css,/#amp-one \.a-delivery-confirm \.a-dialog-actions\{margin-top:\.5rem\}/);
 assert.match(css,/#amp-one \.a-delivery-content\{margin:0;min-width:0;max-width:100%\}/);
 assert.match(css,/#amp-one \.a-delivery-content button\{max-width:100%;overflow-wrap:normal\}/);
 assert.match(css,/#amp-one \.a-delivery-detail,#amp-one \.a-delivery-confirm\{text-align:left;font-size:var\(--a-text-sm,13px\);color:var\(--a-ink\);line-height:1\.5\}/);
 assert.match(css,/\.a-delivery-confirm \.a-primary\{background:var\(--a-accent\);color:var\(--a-on-accent\);padding:8px 12px/);
 assert.match(css,/@media\(pointer:coarse\)\{#amp-one \.a-delivery-content button,#amp-one \.a-message-delivery button\{min-height:44px\}\}/);
 assert.match(css,/\.a-delivery-warning>summary\[hidden\]\{display:none\}/);
 assert.doesNotMatch(css,/\.a-delivery-confirm button\{margin:/,'Obsolete per-button spacing must not override the shared action row.');
});

test('shared dialog action rows keep native coarse-pointer targets, including feedback reconciliation',()=>{
 const css=readFileSync(new URL('../src/reference.css',import.meta.url),'utf8');
 const coarse=css.match(/@media\s*\(pointer:\s*coarse\)\s*\{([\s\S]*?)\n\}/)?.[1]||'';
 assert.match(coarse,/#amp-one button\s*\{\s*min-height:\s*44px;\s*\}/,'Shared native buttons include dialog actions, not just a preview overlay.');
 assert.match(coarse,/#amp-one :is\(\.a-icon, \.a-send\)\s*\{\s*min-width:\s*44px;\s*\}/);
});
