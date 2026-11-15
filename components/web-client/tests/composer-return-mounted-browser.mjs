// Real MarkdownComposer + settings, synthetic component props/storage/platform
// signals. Not an installed-host, physical mobile keyboard or provider check.
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/composer-return-mounted';
const fixturePath='tests/fixtures/composer-return-mounted.jsx';
const key='amplifier.composer.returnKey.v1';
const safeHelp='Return adds a new line. Use Send to send.';
const desktopHelp='Enter to send. Shift+Enter or Ctrl+Enter adds a new line.';
const report={scope:'actual mounted production composer; synthetic props/storage/platform; not physical mobile or installed-host acceptance',checks:[],errors:[],blockedRequests:[]};
let vite,browser;
const snapshot=page=>page.evaluate(()=>window.composerFixture.snapshot());
const composer=page=>page.getByRole('textbox',{name:'Message Amplifier',exact:true});
const pollSnapshot=(page,pick)=>expect.poll(async()=>pick(await snapshot(page)));
const textContent=node=>(node.content||[]).map(child=>child.text??textContent(child)).join('');
const settle=page=>page.waitForTimeout(350); // Past PM's existing 200 ms iOS fallback; not application dedup.
async function setRange(page,selector,{start,end=start}={}){
 await composer(page).evaluate((editor,{selector,start,end})=>{
  const element=editor.querySelector(selector);
  if(!element)throw Error('Missing fixture selection target: '+selector);
  const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT),text=walker.nextNode();
  if(!text)throw Error('Missing fixture text: '+selector);
  const from=start==='end'?text.length:start,to=end==='end'?text.length:end;
  editor.focus();
  const range=document.createRange();range.setStart(text,from);range.setEnd(text,to);
  const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  document.dispatchEvent(new Event('selectionchange'));
 },{selector,start,end});
 // Let the native selection observer publish it before capturing the PM caret.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
function assertUnchanged(before,after,label){
 assert.equal(after.draft,before.draft,label+' retains serialized draft');
 assert.deepEqual(after.document,before.document,label+' retains the complete rich document');
 assert.equal(after.caret,before.caret,label+' retains editor selection position');
 assert.deepEqual(after.sends,before.sends,label+' never invokes onSend');
 assert.equal(after.changes,before.changes,label+' creates no edit transaction');
 assert.equal(after.sameEditor,true,label+' never remounts the editor');
}
async function newMount({text,platform='desktop',storage='normal'}={}){
 const context=await browser.newContext({viewport:{width:1100,height:900}});
 const page=await context.newPage();
 page.on('pageerror',error=>report.errors.push(error.message));
 // This isolated component has no legitimate host/network actions. Observe and
 // block all API requests, not only the expected appearance/preferences names.
 await page.route('**/api/**',route=>{
  report.blockedRequests.push({method:route.request().method(),url:new URL(route.request().url()).pathname,body:route.request().postData()});
  return route.abort('blockedbyclient');
 });
 await page.addInitScript(({text,platform,storage,key})=>{
  window.composerFixtureInitial={text};
  window.returnStorageCalls=[];
  const get=Storage.prototype.getItem,set=Storage.prototype.setItem;
  window.readStoredReturnPreference=()=>get.call(localStorage,key);
  Storage.prototype.getItem=function(name){
   if(name===key){
    window.returnStorageCalls.push({operation:'read'});
    if(storage==='read-fails')throw new DOMException('Fixture preference read blocked','SecurityError');
   }
   return get.call(this,name);
  };
  Storage.prototype.setItem=function(name,value){
   if(name===key){
    window.returnStorageCalls.push({operation:'write',value});
    if(storage==='write-fails'||storage==='read-fails')throw new DOMException('Fixture preference write blocked','QuotaExceededError');
    if(storage==='unconfirmed')return;
   }
   return set.call(this,name,value);
  };
  // Test the editor's platform-deferred paths in Chromium, not actual Safari
  // or Android keyboards. Do not replace any production Return handler.
  Object.defineProperty(navigator,'maxTouchPoints',{get:()=>platform==='desktop'?0:5});
  if(platform==='ios'){
   Object.defineProperty(navigator,'vendor',{get:()=>'Apple Computer, Inc.'});
   Object.defineProperty(navigator,'platform',{get:()=>'MacIntel'});
   Object.defineProperty(navigator,'userAgent',{get:()=>'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'});
  }else if(platform==='android'){
   Object.defineProperty(navigator,'userAgent',{get:()=>'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36'});
  }
 },{text,platform,storage,key});
 try{
  await page.goto(vite.resolvedUrls.local[0]+'composer-return-mounted');
  await expect(composer(page)).toBeVisible();
  await page.waitForFunction(()=>!!window.composerFixture);
  await expect(composer(page)).toHaveAttribute('enterkeyhint','enter');
  await expect(composer(page)).toHaveAccessibleDescription(platform==='desktop'?desktopHelp:safeHelp);
  return {context,page};
 }catch(error){await context.close();throw error;}
}

async function failedPreference(storage){
 const original='Keep **bold** and *italic*.\n\n- First\n- Second\n\n```js\n  code\n```';
 const {context,page}=await newMount({text:original,storage});
 try{
  const editor=composer(page),choice=page.getByLabel('Return key',{exact:true});
  await setRange(page,'p',{start:4});
  const initial=await snapshot(page);
  await page.keyboard.type('new ');
  await pollSnapshot(page,row=>row.draft).toBe('Keepnew  **bold** and *italic*.\n\n* First\n* Second\n\n```js\n  code\n```');
  await setRange(page,'strong',{start:0,end:4});
  const before=await snapshot(page);
  assert.deepEqual(before.selection.anchorPath,before.selection.focusPath);
  assert.equal(before.selection.focusOffset-before.selection.anchorOffset,4,'A real rich-text range is selected');
  assert.ok(before.document.content.some(node=>node.type==='bullet_list'));
  assert.ok(before.document.content.some(node=>node.type==='code_block'));
  if(storage==='read-fails')await expect(page.getByRole('alert')).toContainText('could not read');
  await choice.focus();await expect(choice).toBeFocused();
  await choice.selectOption('newline');
  await expect(choice).toHaveValue('newline');
  await expect(page.getByRole('alert')).toHaveText(/applies in this tab only.*could not confirm saving/);
  await expect(editor).toHaveAccessibleDescription(safeHelp);
  await expect(choice).toHaveAccessibleDescription(/This browser only.*Return adds a new line/);
  const after=await snapshot(page);
  const proof={case:'failed-preference-'+storage,before,after,storageCalls:await page.evaluate(()=>window.returnStorageCalls)};
  report.checks.push(proof);
  assertUnchanged(before,after,storage+' preference');
  // The preference owns focus while selected. Refocusing without a click must
  // restore the editor's retained range, not place its caret at an arbitrary end.
  await editor.focus();
  assert.deepEqual((await snapshot(page)).selection,before.selection,storage+' preserves the actual DOM range');
  assert.equal(await page.evaluate(()=>window.readStoredReturnPreference()),null,'Failed/unconfirmed choice is not falsely persisted');
  const calls=await page.evaluate(()=>window.returnStorageCalls);
  assert.ok(calls.some(call=>call.operation==='write'&&call.value==='newline'),'Failure path actually attempted browser-local storage');
  // Undo/redo after the preference rerender proves the mounted editor retained
  // its history, not merely a DOM/text copy. Exactly one typed edit precedes it.
  await editor.press('ControlOrMeta+z');
  await pollSnapshot(page,row=>row.document).toEqual(initial.document);
  await editor.press('ControlOrMeta+Shift+z');
  await pollSnapshot(page,row=>row.document).toEqual(before.document);
  assert.equal((await snapshot(page)).sameEditor,true);
  await setRange(page,'li:last-child p',{start:'end'});
  await editor.press('Enter');await settle(page);
  const continued=await snapshot(page),list=continued.document.content.find(node=>node.type==='bullet_list');
  assert.equal(list.content.length,3,'Failed-storage preference still applies newline to actual desktop editor');
  assert.deepEqual(continued.sends,[]);
  Object.assign(proof,{passed:true,documentRetained:true,selectionRetained:true,undoRedoRetained:true,editorIdentityRetained:true,warning:true,effectiveNewline:true});
 }finally{await context.close();}
}

async function guardedProps(){
 const {context,page}=await newMount({text:'A **rich** draft.\n\n- Keep this item'});
 try{
  const editor=composer(page);
  await setRange(page,'strong',{start:1,end:3});
  for(const prop of ['disabled','readOnly']){
   const before=await snapshot(page);
   // Real component props, NOT DOM aria/contenteditable mutation.
   await page.evaluate(prop=>window.composerFixture.setProps({disabled:prop==='disabled',readOnly:prop==='readOnly'}),prop);
   await expect(editor).toHaveAttribute(prop==='disabled'?'aria-disabled':'aria-readonly','true');
   await expect(editor).toHaveAttribute('contenteditable','false');
   const receipts=await editor.evaluate(node=>{
    const results=[];
    for(const input of [
     new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true,cancelable:true}),
     new KeyboardEvent('keydown',{key:'Enter',keyCode:13,ctrlKey:true,bubbles:true,cancelable:true}),
     new InputEvent('beforeinput',{inputType:'insertParagraph',bubbles:true,cancelable:true}),
     new InputEvent('beforeinput',{inputType:'insertLineBreak',bubbles:true,cancelable:true}),
     new InputEvent('beforeinput',{inputType:'insertParagraph',bubbles:true,cancelable:false}),
    ]){
     node.dispatchEvent(input);results.push({type:input.type,inputType:input.inputType,prevented:input.defaultPrevented});
    }
    return results;
   });
   await settle(page);
   assertUnchanged(before,await snapshot(page),prop+' props');
   report.checks.push({case:'component-prop-'+prop,unchanged:true,events:receipts});
   await page.evaluate(()=>window.composerFixture.setProps({disabled:false,readOnly:false}));
   await expect(editor).toHaveAttribute('contenteditable','true');
  }
  // Positive control: removing the real lock restores actual editing and Send.
  await setRange(page,'li p',{start:'end'});
  await editor.press('Control+Enter');
  await pollSnapshot(page,row=>row.document.content[1].content.length).toBe(2);
  const editable=await snapshot(page);
  await editor.press('Enter');
  await pollSnapshot(page,row=>row.sends).toEqual([editable.draft]);
  report.checks.push({case:'component-props-unlocked-positive-control',editable:true,onSend:1});
 }finally{await context.close();}
}

async function richReturn(platform,kind){
 const sources={
  list:'- **First**\n- Second',
  code:'```js\n  code\n```',
  table:'| A | B |\n| --- | --- |\n| left | right |',
 };
 const selectors={list:'li:last-child p',code:'pre code',table:'td:first-child'};
 const {context,page}=await newMount({platform,text:sources[kind]});
 try{
  const editor=composer(page);
  await setRange(page,selectors[kind],{start:'end'});
  const before=await snapshot(page);
  // The listener runs after the production handler. Its preventDefault and
  // beforeinput records prove which native/platform route this stimulus took.
  await editor.evaluate(node=>{
   window.returnEventLog=[];
   for(const type of ['keydown','beforeinput','input'])node.addEventListener(type,event=>{
    window.returnEventLog.push({type,key:event.key,keyCode:event.keyCode,inputType:event.inputType,prevented:event.defaultPrevented,cancelable:event.cancelable,trusted:event.isTrusted});
   });
  });
  await editor.press('Enter');await settle(page);
  const after=await snapshot(page),events=await page.evaluate(()=>window.returnEventLog);
  const proof={case:platform+'-'+kind,before,after,events};report.checks.push(proof);
  assert.equal(after.sameEditor,true);assert.deepEqual(after.sends,[]);
  assert.ok(events.some(event=>event.type==='keydown'&&event.key==='Enter'&&event.trusted),'Actual Chromium keydown reaches the mounted editor');
  // The compatibility adapter keeps real safe-mode keydown native, rather than
  // arming PM's legacy iOS/Android deferred Enter. The real cancelable editing
  // event owns the rich transaction; its native DOM mutation must be canceled.
  assert.ok(events.some(event=>event.type==='keydown'&&event.key==='Enter'&&event.trusted&&!event.prevented),'Safe Return does not cancel mobile keydown');
  assert.ok(events.some(event=>event.type==='keydown'&&event.key==='Enter'&&!event.trusted&&event.keyCode===0),'Exact relay traverses the pinned PM guard/flush');
  assert.equal(events.filter(event=>event.type==='beforeinput'&&['insertParagraph','insertLineBreak'].includes(event.inputType)).length,1,'One native editing intent');
  assert.ok(events.some(event=>event.type==='beforeinput'&&event.cancelable&&event.prevented),'Rich edit cancels the native beforeinput default');
  if(kind==='list'){
   assert.equal(after.document.content.length,1);
   const list=after.document.content[0];assert.equal(list.type,'bullet_list');
   assert.equal(list.content.length,3,'One Return creates exactly one list item');
   assert.deepEqual(list.content.slice(0,2),before.document.content[0].content);
   assert.equal(textContent(list.content[2]),'');
   assert.equal(before.caret,18);assert.equal(after.caret,22,'Caret reaches the one new list item');
  }else if(kind==='code'){
   assert.equal(after.document.content.length,1);assert.equal(after.document.content[0].type,'code_block');
   assert.deepEqual(after.document.content[0].attrs,before.document.content[0].attrs,'Code language is retained');
   assert.equal(textContent(after.document.content[0]),'  code\n  ','One code continuation retains indentation with no extra fallback newline');
   assert.equal(before.caret,7);assert.equal(after.caret,10,'Caret follows the inserted code indentation');
  }else{
   assert.deepEqual(after.document.content[0],before.document.content[0],'Table columns/content stay exact');
   assert.equal(after.document.content.length,before.document.content.length+1,'One table exit creates exactly one trailing paragraph');
   assert.deepEqual(after.document.content.at(-1),{type:'paragraph'});
   assert.equal(before.caret,15);assert.equal(after.caret,26,'Caret exits all table cells into the following paragraph');
  }
  // No onChange count assumption: DOM reconciliation may use multiple
  // transactions for one edit. Semantic document+undo is the regression oracle.
  const undo=platform==='ios'?'Meta+z':'ControlOrMeta+z',redo=platform==='ios'?'Meta+Shift+z':'ControlOrMeta+Shift+z';
  await editor.press(undo);
  await pollSnapshot(page,row=>row.document).toEqual(before.document);
  await editor.press(redo);
  await pollSnapshot(page,row=>row.document).toEqual(after.document);
  const redone=await snapshot(page);assert.deepEqual(redone.sends,[]);assert.equal(redone.sameEditor,true);
  Object.assign(proof,{passed:true,oneSemanticEdit:true,onSend:0,undoRedo:true});
 }finally{await context.close();}
}

// Cheap authoring check for the sole runtime owner; never starts Vite/browser.
if(process.argv.includes('--preflight')){
 await transformWithEsbuild(await readFile(root+fixturePath,'utf8'),fixturePath,{loader:'jsx',jsx:'automatic'});
 const registry=JSON.parse(await readFile(root+'tests/browser-checks.json','utf8'));
 assert.equal(registry.scenarios.filter(row=>row.id==='composer-return-mounted').length,1);
 console.log('Mounted Return fixture JSX and registry preflight passed; browser not run.');
}else{
 await mkdir(output,{recursive:true});
 try{
  vite=await createServer({configFile:false,root,
   server:{host:'127.0.0.1',port:0,hmr:false},
   optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']},
   plugins:[{name:'composer-return-mounted',
    configureServer(server){server.middlewares.use((req,res,next)=>{
     if(req.url!=='/composer-return-mounted')return next();
     res.setHeader('Content-Type','text/html');
     res.end('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><main id="amp-one"><div id="fixture"></div></main><script type="module" src="/tests/fixtures/composer-return-mounted.jsx"></script>');
    });},
   }],
  });
  await vite.listen();
  browser=await chromium.launch({headless:true,
   ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
   ...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--single-process','--no-zygote','--no-sandbox']}:{}),
  });
  for(const storage of ['write-fails','read-fails','unconfirmed'])await failedPreference(storage);
  await guardedProps();
  for(const platform of ['touch','ios','android'])for(const kind of ['list','code','table'])await richReturn(platform,kind);
  assert.deepEqual(report.errors,[]);
  assert.deepEqual(report.blockedRequests,[],'No host preference or other API request is made');
  report.status='PASS';
 }catch(error){report.status='FAIL';report.failure=error.stack||String(error);throw error;}
 finally{
  await writeFile(output+'/result.json',JSON.stringify(report,null,2)+'\n');
  await browser?.close();await vite?.close();
 }
 console.log(JSON.stringify(report));
}