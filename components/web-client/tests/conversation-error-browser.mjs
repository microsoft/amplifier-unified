// Canonical components/CSS with synthetic props; not live incident recovery or
// installed-app qualification. Run in the owning managed browser environment.
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {assertConversationErrorGeometry,assertConversationErrorFocus} from './conversation-error-geometry.mjs';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/conversation-error';
const fixture=`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ConversationError} from '/src/conversation-controls.jsx';
import '/src/reference.css';
import '/src/main-refresh.css';
window.calls=[];
const dispatch=async(name,args)=>{window.calls.push({name,args});throw Error('Geometry fixture refuses effects: '+name)};
function Fixture(){
 const [width,setWidth]=useState(null),[category,setCategory]=useState('context_limit');
 window.setPaneWidth=setWidth;window.setCategory=setCategory;
 const failure={category,summary:category==='context_limit'?'This conversation reached its context limit.':'The recorded request could not complete.',guidance:'Your saved messages are kept. Review recovery options. '+('long-provider-detail-'.repeat(15))};
 return <div className="a-layout" data-pane-layout="balanced" data-canvas-open="false" data-canvas-overlay="true">
  <section className="a-conversation" aria-label="Synthetic conversation" style={{width:width?width+'px':'100%',maxWidth:width?width+'px':'none',flex:'none',marginInline:'auto'}}>
   <div className="a-messages"><article className="a-message">Readable conversation content</article></div>
   <ConversationError state={{attention:{items:[{id:'session:fixture',fingerprint:'fixture-failure',read:false}]}}} session={{id:'fixture',status:'error',failure}} act={dispatch}/>
   <form className="a-composer" onSubmit={event=>event.preventDefault()}><label htmlFor="fixture-draft">Message</label><textarea id="fixture-draft" aria-label="Message"/></form>
  </section>
 </div>;
}
createRoot(document.getElementById('fixture')).render(<Fixture/>);
`;
let vite,browser,page;
const metrics=[],errors=[],failures=[];
try{
 await mkdir(artifacts,{recursive:true});
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},plugins:[{
  name:'conversation-error-fixture',resolveId(id){if(id==='/conversation-error-fixture.jsx')return id},
  async load(id){if(id==='/conversation-error-fixture.jsx')return (await transformWithEsbuild(fixture,'conversation-error-fixture.jsx',{loader:'jsx',jsx:'automatic'})).code},
  configureServer(server){server.middlewares.use((req,res,next)=>{
   if(req.url!=='/conversation-error-fixture')return next();
   res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0}#fixture{width:100%;height:100%}#amp-one .a-layout{height:100%;min-height:0}#amp-one .a-messages{min-height:100px}#amp-one .a-conversation{height:100%}</style><main id="amp-one" class="a-chat-shell a-approachable-shell"><div id="fixture"></div></main><script type="module" src="/conversation-error-fixture.jsx"></script>');
  })}
 }]});
 await vite.listen();browser=await chromium.launch({headless:true});
 for(const viewport of [{width:1920,height:1080},{width:1280,height:900},{width:768,height:1024},{width:420,height:844},{width:390,height:844},{width:320,height:740}]){
  page=await browser.newPage({viewport});page.on('pageerror',error=>errors.push(error.message));
  for(const scheme of ['light','dark']){
   await page.emulateMedia({colorScheme:scheme});await page.goto(vite.resolvedUrls.local[0]+'conversation-error-fixture');
   const banner=page.locator('[data-part=conversation-trouble]'),composer=page.locator('.a-composer');
   await expect(banner).toBeVisible();
   for(const paneWidth of [null,...(viewport.width>=1280?[480]:[])])for(const enlarged of [false,true])for(const category of ['context_limit','worker_exit']){
    await page.evaluate(({paneWidth,enlarged,category})=>{
     window.setPaneWidth(paneWidth);window.setCategory(category);
     document.querySelector('#amp-one').style.setProperty('--a-text-base',enlarged?'24px':'14px');
    },{paneWidth,enlarged,category});
    await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toHaveCount(category==='context_limit'?1:0);
    const label=[viewport.width,scheme,paneWidth||'full',enlarged?'large':'normal',category].join('-');
    const summary=banner.locator('summary');
    await banner.locator('details').evaluate(node=>{node.open=false});
    metrics.push(await assertConversationErrorGeometry({banner,composer,label:label+'-closed'}));
    await page.keyboard.press('Tab');
    for(const control of [summary,banner.getByRole('button',{name:'Dismiss conversation error'}),...(category==='context_limit'?[banner.getByRole('button',{name:'Create recovery copy',exact:true})]:[])])await assertConversationErrorFocus(control,label+'-closed');
    await summary.focus();await page.keyboard.press('Enter');
    await expect(banner.locator('details')).toHaveAttribute('open','');
    metrics.push(await assertConversationErrorGeometry({banner,composer,label:label+'-open'}));
    for(const control of [banner.getByRole('button',{name:'Technical details',exact:true}),banner.getByRole('button',{name:'Report a problem',exact:true}),banner.getByRole('button',{name:'Dismiss conversation error'})])await assertConversationErrorFocus(control,label+'-open');
    // Traverse the real disclosure controls without activating an external effect.
    await summary.focus();await page.keyboard.press('Tab');await expect(banner.getByRole('button',{name:'Technical details',exact:true})).toBeFocused();
    await page.keyboard.press('Tab');await expect(banner.getByRole('button',{name:'Report a problem',exact:true})).toBeFocused();
    await page.keyboard.press('Tab');await expect(banner.getByRole('button',{name:'Dismiss conversation error'})).toBeFocused();
    await page.keyboard.press('Shift+Tab');await expect(banner.getByRole('button',{name:'Report a problem',exact:true})).toBeFocused();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label+' page overflow');
    assert.deepEqual(await page.evaluate(()=>window.calls),[],label+' no recovery, report, dismiss or other action dispatch');
   }
   await page.screenshot({path:artifacts+'/'+viewport.width+'-'+scheme+'.png',fullPage:true});
  }
  await page.close();page=null;
 }
 assert.deepEqual(errors,[]);
 console.log('PASS: conversation error shares composer column; six viewports, narrow desktop pane, light/dark, enlarged text, open/closed details, wrapping text/actions, focus reach and zero action dispatch.');
}catch(error){
 failures.push(String(error.stack||error));
 if(page&&!page.isClosed())await page.screenshot({path:artifacts+'/failure.png',fullPage:true}).catch(()=>{});
 throw error;
}finally{
 await writeFile(artifacts+'/result.json',JSON.stringify({passed:failures.length===0,scope:'Canonical components/CSS with synthetic props, not actual host or incident qualification.',metrics,errors,failures},null,2)+'\n');
 if(browser)await browser.close();if(vite)await vite.close();
}
