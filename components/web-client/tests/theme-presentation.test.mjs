import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const bootstrap=readFileSync(new URL('../public/appearance-bootstrap.js',import.meta.url),'utf8');
const cacheKey='amplifier.appearance-composition.v3';
const palette={
 light:{background:'rgb(232, 238, 255)',foreground:'#263459',chrome:'rgb(20, 54, 90)'},
 dark:{background:'#151b31',foreground:'#eef2ff',chrome:'oklch(40% 0.1 260)'},
};
const saved=(scheme='light',colors=palette)=>({version:3,activeScope:'shared',storageVersion:'global-version',compositionId:'composition-selected',themeKey:'committed-theme',scheme,colors});

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

test('synchronous bootstrap restores only committed v3 colors before authoritative boot',()=>{
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
 for(const value of [null,[],{},1,{...saved(),version:1},{...saved(),version:2},{...saved(),version:'3'},
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
 assert.match(css,/@media\s*\(pointer:\s*coarse\)\s*\{\s*#amp-one \.a-dialog-actions button\s*\{\s*min-height:\s*44px;\s*\}\s*\}/);
});
