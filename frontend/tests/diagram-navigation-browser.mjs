import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;const results=[],errors=[];
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1100},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 const commands=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();commands.push(body)}});
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const saved=()=>page.evaluate(()=>window.amplifier.getState().canvas.view||{});
 const record=(kind,name,passed,measurements={})=>results.push({kind,name,passed,...measurements});
 await page.goto(url);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await act('session.create',{});await act('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:700}});
 for(const kind of ['mermaid','dot']){
  const content=kind==='mermaid'?'flowchart TD\n'+Array.from({length:10},(_,i)=>`N${i}[Step ${i}] --> N${i+1}[Step ${i+1}]`).join('\n'):'digraph { '+Array.from({length:10},(_,i)=>`n${i}->n${i+1};`).join(' ')+' }';
  await act('canvas.show',{kind,content,title:kind+' navigation'});
  const stage=page.locator('.a-diagram-stage'),img=stage.locator('img');
  await expect(img).toBeVisible();await expect.poll(()=>img.evaluate(el=>el.complete&&el.naturalWidth>0)).toBe(true);
  const box=await stage.boundingBox(),picture=await img.boundingBox(),body=await page.locator('.a-canvas-viewer').boundingBox();
  record(kind,'fit uses available pane height',box.height>=body.height*.75,{stageHeight:box.height,paneHeight:body.height});
  record(kind,'fit retains complete tall graph',picture.height<=box.height+1&&picture.width<=box.width+1&&picture.height>=box.height*.8,{imageHeight:picture.height});
  const x=box.x+box.width*.65,y=box.y+box.height*.6;
  const before=await img.boundingBox(),requestStart=commands.length;
  await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+80,y+45,{steps:8});
  const during=await img.boundingBox();
  record(kind,'pan follows pointer before release',Math.abs((during.x-before.x)-80)<2&&Math.abs((during.y-before.y)-45)<2,{deltaX:during.x-before.x,deltaY:during.y-before.y});
  record(kind,'pan does not send per-move commands',commands.length===requestStart,{commandsDuringDrag:commands.length-requestStart});
  await page.mouse.up();await expect.poll(async()=>Math.round((await saved()).panX||0)).toBe(80);
  await page.getByRole('button',{name:'Fit',exact:true}).click();await expect.poll(async()=>Math.round((await saved()).panX||0)).toBe(0);
  const initial=await img.boundingBox();
  await page.mouse.move(x,y);const wheelStart=commands.length;
  await page.mouse.wheel(0,-250);
  // A frame is sufficient for local feedback; durable wheel state is debounced.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const zoomed=await img.boundingBox();const scale=zoomed.width/initial.width;
  record(kind,'wheel zooms around pointer',scale>1.05&&Math.abs((zoomed.x+(x-initial.x)*scale)-x)<3&&Math.abs((zoomed.y+(y-initial.y)*scale)-y)<3,{scale});
  await page.waitForTimeout(300);
  record(kind,'wheel persistence is bounded',commands.length-wheelStart<=2,{commands:commands.length-wheelStart});
  const retained=await saved();await page.reload();await expect(img).toBeVisible();
  await expect.poll(async()=>(await saved()).zoom).toBe(retained.zoom);
  record(kind,'reload retains zoom and pan',(await saved()).panX===retained.panX&&(await saved()).panY===retained.panY);
  const keyBefore=(await saved()).panX||0;await stage.focus();await page.keyboard.press('ArrowRight');
  await expect.poll(async()=>(await saved()).panX).toBe(keyBefore+30);
  const keyboard=await saved();record(kind,'keyboard pan persists',Number.isFinite(keyboard.panX)&&keyboard.panX!==0);
  await page.getByRole('button',{name:'Fit',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await act('view.update',{patch:{canvasFocused:true}});
  await expect.poll(async()=>{const b=await stage.boundingBox(),i=await img.boundingBox();return i.width<=b.width+1&&i.height<=b.height+1}).toBe(true);
  record(kind,'narrow resize refits complete graph',true);
  const touchBox=await stage.boundingBox(),touchBefore=await img.boundingBox(),cdp=await page.context().newCDPSession(page);
  const tx=touchBox.x+touchBox.width/2,ty=touchBox.y+touchBox.height/2;
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx,y:ty}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx+40,y:ty+30}]});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const touchDuring=await img.boundingBox();
  record(kind,'touch pan follows finger before release',Math.abs(touchDuring.x-touchBefore.x-40)<2&&Math.abs(touchDuring.y-touchBefore.y-30)<2);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();
  await expect.poll(async()=>Math.round((await saved()).panX)).toBe(40);
  const cancelledStart=await img.boundingBox(),cancelCommands=commands.length,abort=await page.context().newCDPSession(page);
  await abort.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx,y:ty}]});
  await abort.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx+35,y:ty+25}]});
  await abort.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});await abort.detach();
  await expect.poll(async()=>Math.round((await img.boundingBox()).x-cancelledStart.x)).toBe(0);
  record(kind,'cancelled touch restores saved view without committing partial pan',(await saved()).panX===40&&commands.length===cancelCommands);
  await page.setViewportSize({width:1400,height:1100});await act('view.update',{patch:{canvasFocused:false}});
 }
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({results,errors}));
 assert.ok(results.every(row=>row.passed),JSON.stringify(results.filter(row=>!row.passed)));
}finally{
 if(process.env.DIAGRAM_EVIDENCE)writeFileSync(process.env.DIAGRAM_EVIDENCE,JSON.stringify({results,errors},null,2));
 try{await browser?.close()}finally{fixture.kill('SIGTERM')}
}
