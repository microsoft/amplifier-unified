// Run only in the manager's existing DTU, after building current candidate assets.
// Uses production UI + existing synthetic host fixture; no model/provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--canvas-versions'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:1050},hasTouch:true,permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  const Native=window.EventSource;
  window.blockCopyBaselineReceived=false;
  window.EventSource=class extends Native{
   constructor(...args){super(...args);this.addEventListener('state',()=>{window.blockCopyBaselineReceived=true})}
  };
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const clipboard=()=>page.evaluate(()=>navigator.clipboard.readText());
 const canvas=page.locator('.a-canvas-viewer');
 async function ready(){
  // Current startup is SSE-first. Do not send actions against an empty baseline.
  await page.waitForFunction(()=>window.blockCopyBaselineReceived&&Array.isArray(window.amplifier?.getState()?.sessions));
  await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 }
 async function copy(button,expected){
  await button.focus();await page.keyboard.press('Enter');
  await expect.poll(clipboard).toBe(expected);
  await expect(button.locator('..').getByRole('status')).toHaveText('Copied.');
 }
 await page.goto(url);await ready();
 await action('session.create');
 const raw='\t  α😀\r\n\r\n  tail\t\r\n\r\n';
 const nested='1.\tOuter\r\n\r\n\t- Inner\r\n\r\n\t  ```txt\r\n\t  \tα😀\r\n\t  \r\n\t    tail\t\r\n\t  \r\n\t  ```\r\n';
 const table='| Left | Right |\r\n| :--- | ---: |\r\n| a\\|b | **α😀** |\r\n';
 const listedTable='- Table\r\n\r\n'+table.split('\r\n').filter(Boolean).map(line=>'  '+line+'\r\n').join('');
 const message='Neighbor before\r\n\r\n```js\r\n'+raw+'```\r\n\r\n> **outer**\r\n> > `inner`\r\n\r\n'+nested+'\r\n- Quote\r\n\r\n  > **list quote**\r\n\r\n'+listedTable+'\r\nNeighbor after `inline`\r\n\r\n:::writing{variant="document" id="12345"}\nReusable writing\n:::';
 await action('conversation.send',{text:message});
 const assistant=page.locator('.a-assistant').last();
 await expect(assistant.getByRole('button',{name:'Copy code block',exact:true})).toHaveCount(2);
 await expect(assistant.getByRole('button',{name:'Copy quote block',exact:true})).toHaveCount(3);
 await expect(assistant.getByRole('button',{name:'Copy Markdown source',exact:true})).toHaveCount(1);
 await copy(assistant.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 await copy(assistant.getByRole('button',{name:'Copy code block',exact:true}).nth(1),'\tα😀\r\n\r\n  tail\t\r\n\r\n');
 await copy(assistant.getByRole('button',{name:'Copy quote block',exact:true}).nth(1),'`inner`\r\n');
 await copy(assistant.getByRole('button',{name:'Copy quote block',exact:true}).nth(2),'**list quote**\r\n');
 await copy(assistant.getByRole('button',{name:'Copy Markdown source',exact:true}),table);
 // No block toolbar can include a neighboring paragraph or whole-message text.
 await expect(assistant.locator('[data-copy-block]')).toHaveCount(6);
 // Existing whole-message and writing controls must still use their own payloads.
 await assistant.getByRole('button',{name:'Copy message as Markdown',exact:true}).click();
 await expect.poll(clipboard).toBe(message);
 await assistant.getByRole('button',{name:'Copy writing',exact:true}).click();
 await expect.poll(clipboard).toBe('Reusable writing');
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 const markdown='# Canvas\n\n```txt\r\n'+raw+'```\r\n\r\n```mermaid\nflowchart LR\n A-->B\n```\n\n```dot\ndigraph { a -> b }\n```\n\n> **quoted**\r\n\r\n'+table;
 const artifact=(await action('canvas.show',{kind:'markdown',title:'Block copy fixture',content:markdown})).result;
 await expect(canvas.getByRole('button',{name:'Copy code block',exact:true})).toHaveCount(3);
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 await page.locator('.a-diagram-stage img').nth(1).waitFor({timeout:30000});
 assert.equal(await page.locator('.a-diagram-stage img').count(),2);
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(1),'flowchart LR\n A-->B\n');
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(2),'digraph { a -> b }\n');
 await copy(canvas.getByRole('button',{name:'Copy quote block',exact:true}),'**quoted**\r\n');
 await expect(canvas.getByRole('button',{name:'Copy Markdown source',exact:true})).toHaveCount(1);
 await copy(canvas.getByRole('button',{name:'Copy Markdown source',exact:true}),table);
 // Existing source-span mapping still supports references without toolbar text.
 const span=canvas.locator('blockquote [data-canvas-source-start]').first();
 await span.evaluate(element=>{const range=document.createRange();range.selectNodeContents(element);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'))});
 await expect(page.getByRole('button',{name:'Reference in chat',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Reference in chat',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue(/quoted/);
 await page.reload();await ready();
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 const saved=(await action('canvas.versions.inspect',{id:artifact.id,version:1,includeSource:true})).result;
 assert.equal(saved.source.content,markdown,'Copy and reload must not change saved content');
 await page.getByRole('button',{name:'Source',exact:true}).click();
 await expect(page.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(1);
 await copy(page.getByRole('button',{name:'Copy canvas source',exact:true}),markdown);
 // Code/text reuse the one visible source control, not two identical toolbars.
 for(const kind of ['code','text']){
  await action('canvas.show',{kind,title:kind+' fixture',content:raw});
  await expect(page.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(1);
  await copy(page.getByRole('button',{name:'Copy canvas source',exact:true}),raw);
 }
 for(const [kind,content] of [['mermaid','flowchart LR\r\n A-->B\r\n'],['dot','digraph { a -> b }\r\n']]){
  await action('canvas.show',{kind,content});
  await canvas.locator('.a-diagram-stage img').waitFor({timeout:30000});
  await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),content);
 }
 await action('canvas.show',{kind:'json',content:'not JSON'});
 const parseError=await page.evaluate(()=>{try{JSON.parse('not JSON')}catch(error){return error.message}});
 await copy(canvas.getByRole('button',{name:'Copy preview error',exact:true}),parseError);
 const records=[{name:'α',nested:{tab:'\t',emoji:'😀'}},{name:'neighbor'}];
 await action('canvas.show',{kind:'json',content:JSON.stringify(records)});
 await copy(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true}),JSON.stringify(records[0],null,2));
 await canvas.getByRole('searchbox',{name:'Filter data records'}).fill('neighbor');
 await expect(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true})).toHaveCount(0);
 await copy(canvas.getByRole('button',{name:'Copy record 2 as JSON',exact:true}),JSON.stringify(records[1],null,2));
 await action('canvas.show',{kind:'jsonl',content:records.map(row=>JSON.stringify(row)).join('\r\n')});
 await copy(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true}),JSON.stringify(records[0],null,2));
 // Real browser permission denial, not a mock success.
 await page.evaluate(()=>navigator.clipboard.writeText('retained clipboard'));await context.clearPermissions();
 const cdp=await context.newCDPSession(page);
 const {targetInfo}=await cdp.send('Target.getTargetInfo');
 // Chromium has separate sanitized gesture and unsanitized descriptors.
 // Denying only the latter did not deny the actual write in the retained FAIL.
 for(const allowWithoutSanitization of [false,true])
  await cdp.send('Browser.setPermission',{permission:{name:'clipboard-write',allowWithoutSanitization},setting:'denied',origin:new URL(url).origin,browserContextId:targetInfo.browserContextId});
 const denied=canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true});
 await denied.click();await expect(denied.locator('..').getByRole('status')).toContainText('denied');
 // Context grants alone do not clear an explicit CDP deny. Reset that same
 // owned context before readback; never substitute clipboard bytes or success.
 await cdp.send('Browser.resetPermissions',{browserContextId:targetInfo.browserContextId});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 await expect.poll(clipboard).toBe('retained clipboard');
 // Unsupported API is a separate branch: only this branch is deliberately stubbed.
 await page.evaluate(()=>{window.originalClipboard=Object.getOwnPropertyDescriptor(navigator,'clipboard');Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})});
 await denied.click();await expect(denied.locator('..').getByRole('status')).toContainText('unavailable');
 await page.evaluate(()=>{if(window.originalClipboard)Object.defineProperty(navigator,'clipboard',window.originalClipboard);else delete navigator.clipboard});
 // Stored source is fetched for the selected version before exposing Copy.
 // Use inert HTML only to exercise the host's existing externalization path.
 const savedSource='<p>Saved source α😀</p>\r\n<!--'+'x'.repeat(1000100)+'-->\r\n';
 const fixtureState=await (await page.request.get(url+'/fixture')).json();
 assert.equal(typeof fixtureState.storedCopyPath,'string');
 const storedReceipt=await action('canvas.show',{kind:'html',title:'Stored copy fixture',path:fixtureState.storedCopyPath});
 assert.equal(storedReceipt.accepted,true);
 const storedArtifact=storedReceipt.result;
 await expect.poll(async()=>(await state()).canvas.contentResource?.$resource).toBeTruthy();
 let heldSource;
 const requested=new Promise(resolve=>heldSource=resolve);
 await page.route('**/api/canvas/*/source?version=*',route=>heldSource(route));
 await page.getByRole('button',{name:'Source',exact:true}).click();
 let sourceTimer;
 const sourceRoute=await Promise.race([requested,new Promise((_,reject)=>{sourceTimer=setTimeout(()=>reject(Error('Selected saved-source request did not arrive')),15000)})]).finally(()=>clearTimeout(sourceTimer));
 await expect(canvas.getByText('Loading saved source…',{exact:true})).toBeVisible();
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(0);
 await sourceRoute.continue();
 await page.unroute('**/api/canvas/*/source?version=*');
 await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),savedSource);
 assert.equal((await action('canvas.versions.inspect',{id:storedArtifact.id,version:1,includeSource:true})).result.source.content,savedSource);
 // A failed saved-source read must not enable copying an empty/partial preview.
 await page.evaluate(()=>navigator.clipboard.writeText('retained on source failure'));
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 await page.route('**/api/canvas/*/source?version=*',route=>route.fulfill({status:503,body:'Unavailable'}));
 await page.getByRole('button',{name:'Source',exact:true}).click();
 await expect(canvas.getByRole('alert')).toHaveText('The saved source could not be loaded.');
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(0);
 await expect.poll(clipboard).toBe('retained on source failure');
 await page.unroute('**/api/canvas/*/source?version=*');
 // Hold completion, not invocation, to exercise rapid clicks/version replacement.
 await action('canvas.select',{id:artifact.id,version:1});
 await action('canvas.view',{id:artifact.id,patch:{source:false}});
 await page.evaluate(()=>{
  const write=navigator.clipboard.writeText.bind(navigator.clipboard);window.copyCalls=[];
  Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:text=>{
   window.copyCalls.push(text);const completion=write(text);
   return new Promise((resolve,reject)=>{window.finishCopy=()=>completion.then(resolve,reject)});
  }});
 });
 const oldButton=canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0);
 await oldButton.evaluate(button=>{button.click();button.click()});
 await expect(oldButton.locator('..').getByRole('status')).toHaveText('Copying…');
 assert.deepEqual(await page.evaluate(()=>window.copyCalls),[raw]);
 await action('canvas.versions.revise',{id:artifact.id,expectedRevision:1,content:'```txt\nnew version\n```'});
 await action('canvas.select',{id:artifact.id,version:2});
 await page.waitForFunction(()=>document.querySelector('.a-canvas-preview')?.textContent.includes('new version'));
 await page.evaluate(()=>window.finishCopy());
 await expect.poll(clipboard).toBe(raw);
 assert.deepEqual(await page.evaluate(()=>window.copyCalls),[raw],'No clipboard dispatch after await may retarget a newer version');
 await expect(canvas.locator('.a-block-copy-notice')).toHaveText('');
 await page.reload();await ready();
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}),'new version\n');
 await page.setViewportSize({width:390,height:844});
 await action('view.update',{patch:{canvasFocused:true}});
 const touch=canvas.getByRole('button',{name:'Copy code block',exact:true});
 await expect(touch).toBeVisible();
 const size=await touch.boundingBox();assert.ok(size.width>=44&&size.height>=44);
 await touch.tap();await expect.poll(clipboard).toBe('new version\n');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 console.log('PASS block-copy browser: SSE-first baseline, exact source clipboard, assistant/Canvas list/quote/table blocks, diagrams, code/text/JSON, saved-source readiness/failure, reference spans, reload, permission denial, unsupported API, pending/version race, keyboard/touch, writing/message regression.');
}finally{await browser?.close();fixture.kill()}