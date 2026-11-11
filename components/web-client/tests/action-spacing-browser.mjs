// Focused production components and skin, synthetic props only. No actual host.
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/action-spacing';
await mkdir(artifacts,{recursive:true});
const fixture=`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {UpdateRestart} from '/src/update-restart.jsx';
import {MessageDelivery} from '/src/message-delivery.jsx';
import {FeedbackReconcile} from '/src/feedback-lifecycle.jsx';
import '/src/unified.css';
import '/src/updates.css';
import '/src/message-interactions.css';
import '/src/chat-presentation.css';
import '/src/main-refresh.css';
const target={hostInstanceId:'synthetic',revision:'a'.repeat(40),generation:'b'.repeat(32),attemptId:'synthetic'};
window.calls=[];
const dispatch=async(action,args)=>{
 window.calls.push({action,args});
 if(action==='conversation.delivery')return {accepted:true,result:{...args,delivery:'unknown',message:'Still uncertain. Nothing was resent.'}};
 throw Error('This spacing fixture refuses effects: '+action);
};
const updates={forceRestartTarget:target,pendingApp:{latest:'v99.0.0'}};
const message={id:'local',inputId:'original',role:'user',delivery:{status:'unknown'}};
const session={id:'synthetic',status:'idle',messages:[]};
function Delivery({saved=false,failed=false,steering=false}){
 const row={...message,inputId:saved?'saved':failed?'failed':steering?'steering':'original',
  ...(steering?{steering:{disposition:'unknown'}}:{})};
 return <div className="a-message-actions"><MessageDelivery message={row}
  session={{...session,messages:saved?[row]:[]}}
  delivery={{status:failed?'failed':'unknown',error:'Transport interrupted. Delivery is uncertain.'}}
  localDelivery={saved||steering?undefined:{commandId:row.inputId,status:failed?'failed':'unknown'}}
  dispatch={dispatch} retry={()=>dispatch('conversation.send',{})} discard={()=>dispatch('discard',{})}/></div>;
}
createRoot(document.getElementById('fixture')).render(<>
 <section aria-label="Update restart" className="a-dialog a-updates"><h2>Updates</h2><UpdateRestart updates={updates} act={dispatch}/></section>
 <section aria-label="Original submission"><h2>Delivery recovery</h2><Delivery/></section>
 <section aria-label="Saved submission"><h2>Saved input</h2><Delivery saved/></section>
 <section aria-label="Failed submission"><h2>Unsent input</h2><Delivery failed/></section>
 <section aria-label="Steering submission"><h2>Live direction</h2><Delivery steering/></section>
 <section aria-label="Feedback"><FeedbackReconcile state={{feedback:{requests:[{requestId:'report',title:'Synthetic report',status:'unknown'}]}}} act={dispatch}/></section>
</>);
`;
let vite,browser,page;
const metrics=[],errors=[],failures=[];
async function separated(first,second,label){
 const a=await first.boundingBox(),b=await second.boundingBox();
 assert.ok(a&&b,`${label}: both controls visible`);
 const horizontal=Math.max(b.x-a.x-a.width,a.x-b.x-b.width);
 const vertical=Math.max(b.y-a.y-a.height,a.y-b.y-b.height);
 metrics.push({label,horizontal,vertical,first:a,second:b});
 // A real gap on either a shared line or a wrapped line; never label whitespace.
 if(Math.max(horizontal,vertical)<6)failures.push(`${label}: controls touch (${horizontal}, ${vertical})`);
}
async function statusGap(section,label){
 const gap=await section.locator('.a-message-delivery').evaluate(node=>{
  const button=node.querySelector('button'),range=document.createRange();
  const text=[...node.childNodes].find(child=>child.nodeType===Node.TEXT_NODE&&child.textContent.trim());
  if(!button||!text)return null;
  range.selectNodeContents(text);
  const a=range.getBoundingClientRect(),b=button.getBoundingClientRect();
  return Math.max(b.left-a.right,b.top-a.bottom);
 });
 metrics.push({label,statusGap:gap});
 if(gap===null||gap<6)failures.push(`${label}: status/action gap ${gap}`);
}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},
  plugins:[{
   name:'action-spacing-fixture',
   resolveId(id){if(id==='/action-spacing.jsx')return id},
   async load(id){if(id==='/action-spacing.jsx')return (await transformWithEsbuild(fixture,'action-spacing.jsx',{loader:'jsx',jsx:'automatic'})).code},
   configureServer(server){server.middlewares.use((req,res,next)=>{
    if(req.url!=='/action-spacing')return next();
    res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0}#amp-one{height:auto;min-height:100vh;padding:16px;box-sizing:border-box}#fixture{display:grid;gap:24px;max-width:800px;margin:auto}#fixture>section{min-width:0;width:100%;box-sizing:border-box}h2{margin-top:0}</style><main id="amp-one" class="a-chat-shell a-approachable-shell"><div id="fixture"></div></main><script type="module" src="/action-spacing.jsx"></script>');
   })}
  }]});
 await vite.listen();browser=await chromium.launch({headless:true});
 for(const viewport of [{width:3480,height:2238},{width:1280,height:900},{width:390,height:844},{width:320,height:740}]){
  page=await browser.newPage({viewport,hasTouch:viewport.width<=390,isMobile:viewport.width<=390});
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>sessionStorage.clear());
  for(const scheme of ['light','dark']){
   for(const enlarged of [false,...(viewport.width===320?[true]:[])]){
   await page.emulateMedia({colorScheme:scheme});
   await page.goto(vite.resolvedUrls.local[0]+'action-spacing');
   // The fixture stacks independent production surfaces instead of mounting the
   // viewport-locked app shell; component rules and control geometry stay real.
   await page.locator('#amp-one').evaluate((node,value)=>Object.assign(node.style,{
    colorScheme:value,height:'auto',minHeight:'100vh',overflow:'visible',display:'block',padding:'16px',
   }),scheme);
   await page.evaluate(()=>{
    // Undo only the document viewport lock for this stacked fixture, so a
    // full-page screenshot shows every surface rather than the last scroll.
    for(const node of [document.documentElement,document.body])Object.assign(node.style,{height:'auto',overflow:'visible'});
    document.querySelector('#fixture>.a-dialog').style.maxHeight='none';
   });
   if(enlarged)await page.locator('#amp-one').evaluate(node=>node.style.setProperty('--a-text-base','24px'));
   const label=`${viewport.width}-${scheme}${enlarged?'-large-text':''}`;
   const updates=page.getByRole('region',{name:'Update restart',exact:true});
   await updates.getByRole('button',{name:'Force restart…',exact:true}).click();
   const dialog=updates.getByRole('alertdialog'),cancel=dialog.getByRole('button',{name:'Cancel',exact:true});
   await expect(cancel).toBeFocused();
   await separated(dialog.getByRole('button',{name:'Interrupt work and restart',exact:true}),cancel,label+' restart');
   await cancel.press('Escape');await expect(updates.getByRole('button',{name:'Force restart…',exact:true})).toBeFocused();
   await updates.getByRole('button',{name:'Force restart…',exact:true}).click();
   for(const name of ['Original submission','Saved submission']){
    const section=page.getByRole('region',{name,exact:true});
    await section.getByRole('button',{name:'Check delivery',exact:true}).click();
    const retry=section.getByRole('button',{name:name==='Original submission'?'Retry original submission':'Send again',exact:true});
    const dismiss=section.getByRole('button',{name:'Dismiss warning',exact:true});
    await separated(retry,dismiss,label+' '+name);
    await statusGap(section,label+' '+name+' status');
    await retry.click();
    const confirmation=section.locator('.a-delivery-confirm');
    await separated(confirmation.getByRole('button',{name:'Cancel',exact:true}),confirmation.getByRole('button',{name:'Send this message again',exact:true}),label+' retry confirmation');
    await confirmation.getByRole('button',{name:'Cancel',exact:true}).click();
    await dismiss.click();
    await expect(section.locator('summary')).toBeFocused();
    await expect(section.getByRole('button',{name:'Check delivery',exact:true})).not.toBeVisible();
    await section.locator('summary').click();
    await expect(section.getByRole('button',{name:'Check delivery',exact:true})).toBeVisible();
   }
   const failed=page.getByRole('region',{name:'Failed submission',exact:true});
   await separated(failed.getByRole('button',{name:'Discard unsent message',exact:true}),failed.getByRole('button',{name:'Dismiss warning',exact:true}),label+' discard');
   const feedback=page.getByRole('region',{name:'Feedback',exact:true});
   await separated(feedback.getByText('Synthetic report',{exact:true}),feedback.getByRole('button',{name:'Check delivery',exact:true}),label+' feedback');
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
   if(overflow)failures.push(label+' horizontal overflow');
   const clippedButtons=await page.locator('#fixture button:visible').evaluateAll(nodes=>nodes.filter(node=>node.scrollWidth>node.clientWidth+1).map(node=>node.textContent));
   if(clippedButtons.length)failures.push(label+' clipped buttons: '+clippedButtons.join(', '));
   if(viewport.width<=390){
    const smallTargets=await page.locator('#fixture button:visible').evaluateAll(nodes=>nodes.filter(node=>node.getBoundingClientRect().height<44).map(node=>node.textContent));
    if(smallTargets.length)failures.push(label+' touch targets below 44px: '+smallTargets.join(', '));
   }
   await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:`${artifacts}/${label}.png`,fullPage:true});
   assert.ok((await page.evaluate(()=>window.calls)).every(call=>call.action==='conversation.delivery'),'No send, retry, discard or restart effect');
   }
  }
  await page.close();
 }
 assert.deepEqual(errors,[]);assert.deepEqual(failures,[]);
 console.log('PASS: production action rows, 4 widths × light/dark plus enlarged mobile text, gaps, wrap, focus, details, touch targets, and no effect dispatch.');
}catch(error){
 if(page&&!page.isClosed())await page.screenshot({path:`${artifacts}/failure.png`,fullPage:true}).catch(()=>{});
 failures.push(String(error.stack||error));throw error;
}finally{
 await writeFile(`${artifacts}/result.json`,JSON.stringify({passed:failures.length===0,scope:'Production components and skin; synthetic props, not actual host/release acceptance.',metrics,errors,failures},null,2)+'\n');
 if(browser)await browser.close();if(vite)await vite.close();
}