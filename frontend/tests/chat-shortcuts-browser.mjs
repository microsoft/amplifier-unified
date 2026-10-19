// Real sidebar and guarded selection; browser-reserved keys remain untouched.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const source=fileURLToPath(new URL('../',import.meta.url));
const started=performance.now();
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',vite,browser,page,primary;
fixture.stderr.on('data',value=>logs+=value);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/chat-shortcuts';
const ready=new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)))});
try{
 await mkdir(out,{recursive:true});const port=await ready,target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root:source,cacheDir:out+'/vite',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const rail=page.locator('#workspace-navigation'),rows=rail.locator('.a-nav-chat[data-session-id]');
 const badges=rail.locator('.a-chat-shortcut:visible');
 const blur=()=>page.evaluate(()=>document.activeElement?.blur());
 const hold=async()=>{await page.keyboard.down('Alt');await page.keyboard.down('Shift')};
 const release=async()=>{await page.keyboard.up('Shift');await page.keyboard.up('Alt');await expect(badges).toHaveCount(0)};
 const geometry=()=>rows.evaluateAll(nodes=>nodes.slice(0,9).map(n=>{const r=n.getBoundingClientRect();return {id:n.dataset.sessionId,x:r.x,y:r.y,width:r.width,height:r.height}}));
 const selected=()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const targetMap=()=>rows.filter({has:page.locator('.a-chat-shortcut')}).evaluateAll(nodes=>nodes.map(n=>({id:n.dataset.sessionId,number:Number(n.querySelector('kbd').textContent)})));
 const reveal=async()=>{await blur();await hold();await expect.poll(()=>badges.count()).toBeGreaterThan(0);return targetMap()};
 const shortcut=async id=>{const targets=await reveal(),target=targets.find(row=>row.id===id);assert.ok(target,`Chat ${id} must be in the bounded shortcut list`);await page.keyboard.press('Digit'+target.number);await release()};
 await page.goto(vite.resolvedUrls.local[0]);await expect(rows).toHaveCount(20);await blur();
 const before=await geometry();await hold();await expect(badges).toHaveCount(0);await expect(badges).toHaveCount(9);assert.deepEqual(await geometry(),before);
 await page.screenshot({path:out+'/revealed.png'});
 const frozen=await rows.filter({has:page.locator('.a-chat-shortcut')}).evaluateAll(nodes=>nodes.map(n=>({id:n.dataset.sessionId,number:Number(n.querySelector('kbd').textContent)})));
 // A real pin moves the ninth row above the others while the gesture is held.
 const ninth=frozen.find(row=>row.number===9);await act('session.pin',{id:ninth.id,pinned:true});
 await expect.poll(()=>rows.first().getAttribute('data-session-id')).toBe(ninth.id);
 await expect(rows.first().locator('kbd')).toHaveText('9');
 await page.keyboard.press('Digit2');await release();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(frozen.find(row=>row.number===2).id);
 const chosen=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 // Typing, composition, dialogs and browser tab shortcuts never navigate.
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await composer.focus();await hold();await page.waitForTimeout(1000);await expect(badges).toHaveCount(0);await release();
 await blur();await page.evaluate(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'Shift',code:'ShiftLeft',altKey:true,shiftKey:true,isComposing:true,bubbles:true})));await page.waitForTimeout(1000);await expect(badges).toHaveCount(0);
 const untouched=await page.evaluate(()=>['Control','Meta'].map(modifier=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'1',code:'Digit1',ctrlKey:modifier==='Control',metaKey:modifier==='Meta',cancelable:true,bubbles:true}))));assert.deepEqual(untouched,[true,true]);
 await act('view.update',{patch:{panel:'settings'}});await blur();await hold();await page.waitForTimeout(1000);await expect(badges).toHaveCount(0);await release();await act('view.update',{patch:{panel:null}});
 await blur();await hold();await expect(badges).toHaveCount(10);await page.keyboard.press('Escape');await expect(badges).toHaveCount(0);await release();
 await hold();await expect(badges).toHaveCount(10);await page.evaluate(()=>window.dispatchEvent(new Event('blur')));await expect(badges).toHaveCount(0);await release();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),chosen);
 // A real unsent composer draft follows its chat through both keyboard switches.
 const draft='Unsent shortcut proof: keep this with its original chat.';
 await composer.fill(draft);
 const other=frozen.find(row=>row.id!==chosen).id;
 await shortcut(other);await expect.poll(selected).toBe(other);await expect(composer).not.toHaveText(draft);
 await shortcut(chosen);await expect.poll(selected).toBe(chosen);await expect(composer).toHaveText(draft);

 // The guarded row action must reject navigation before losing renderer-local edits.
 await act('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await act('canvas.show',{kind:'markdown',title:'Shortcut dirty guard',content:'# Retained shortcut source'});
 const manifest={id:'example.shortcut-dirty-editor',label:'Shortcut unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const editorSource='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Shortcut unsaved edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await act('shell.packages.stage',{manifest,source:editorSource})).result.digest;
 const validation=(await act('shell.packages.validate',{digest})).result;
 assert.equal(validation.status,'passed',JSON.stringify(validation));
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const editor=page.locator('[data-canvas-view="primary"]').getByLabel('Shortcut unsaved edit');
 await editor.fill('LOCAL UNSAVED CANVAS EDIT');
 const canvasView=async()=>(await act('canvas.views.inspect',{})).result.views.find(view=>view.viewId==='primary');
 await expect.poll(async()=>(await canvasView()).dirty).toBe(true);
 const dirtyBefore=await canvasView(),mount=await editor.getAttribute('data-mount');assert.ok(mount);
 const refused=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
  const body=request.postDataJSON();return body.action==='session.select'||body.action==='shell.command'&&body.args?.action==='session.select';
 });
 await shortcut(other);const refusal=await refused;
 assert.equal(refusal.ok(),false);assert.match(JSON.stringify(await refusal.json()),/viewer edit/);
 assert.equal(await selected(),chosen);await expect(composer).toHaveText(draft);
 await expect(editor).toHaveValue('LOCAL UNSAVED CANVAS EDIT');assert.equal(await editor.getAttribute('data-mount'),mount);
 assert.deepEqual(await canvasView(),dirtyBefore);await page.screenshot({path:out+'/dirty-canvas-retained.png'});
 await act('canvas.views.recover',Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,dirtyBefore[key]])));
 await expect(editor).toHaveCount(0);assert.equal((await page.evaluate(()=>window.amplifier.getState())).canvas.content,'# Retained shortcut source');await act('canvas.close',{});

 // A supported workspace-pinned shell scope narrows the actual sidebar. The
 // quiet sidebar has no text-filter control; legacy list filters do not apply.
 const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
 const inspected=(await act('shell.inspect',{clientId})).result,originalComposition=inspected.composition;
 const info=await page.evaluate(async()=>{const response=await fetch('/api/fixture/info');if(!response.ok)throw Error(await response.text());return response.json()});
 const beta=info.state.workspaces.find(row=>row.path===info.paths.two);assert.ok(beta);
 const scopedComposition=structuredClone(originalComposition);scopedComposition.instances.find(row=>row.id==='chats').scope={mode:'pinned',workspaceId:beta.id};
 const setComposition=async composition=>{
  const {revision}=(await act('shell.inspect',{clientId})).result;
  const changeId=(await act('shell.changes.prepare',{clientId,expectedRevision:revision,composition})).result.id;
  await act('shell.changes.apply',{clientId,changeId,expectedRevision:revision});
 };
 await setComposition(scopedComposition);await expect(rows).toHaveCount(3);
 const scoped=await reveal();assert.equal(new Set(scoped.map(row=>row.id)).size,3);assert.deepEqual(scoped.map(row=>row.number),[1,2,3]);
 assert.ok(await rows.locator('.a-nav-chat-select').evaluateAll(nodes=>nodes.every(node=>/^Beta 00[012]$/.test(node.getAttribute('aria-label')))));
 await page.keyboard.press('Digit9');assert.equal(await selected(),chosen);await expect(badges).toHaveCount(3);
 const scopedTarget=scoped.find(row=>row.id!==chosen);assert.ok(scopedTarget);
 await page.keyboard.press('Digit'+scopedTarget.number);await release();await expect.poll(selected).toBe(scopedTarget.id);
 await setComposition(originalComposition);await expect(rail.locator('[data-sidebar-section="recent"] .a-nav-chat')).toHaveCount(20);

 // Narrow navigation retains row geometry; the overlaid badges stay within each row.
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Open navigation',exact:true}).click();await expect(rail).toBeVisible();
 await blur();const narrowBefore=await geometry();await reveal();assert.deepEqual(await geometry(),narrowBefore);
 const narrow=await badges.evaluateAll(nodes=>nodes.map(node=>{const badge=node.getBoundingClientRect(),row=node.closest('[data-session-id]').getBoundingClientRect();return {number:node.textContent,badge:{x:badge.x,y:badge.y,width:badge.width,height:badge.height},row:{x:row.x,y:row.y,width:row.width,height:row.height}}}));
 assert.ok(narrow.length>=9);for(const {badge,row} of narrow){assert.ok(badge.x>=row.x&&badge.x+badge.width<=row.x+row.width+1);assert.ok(badge.y>=row.y&&badge.y+badge.height<=row.y+row.height+1);assert.ok(badge.x>=0&&badge.x+badge.width<=390)}
 await page.screenshot({path:out+'/revealed-mobile.png'});await release();await page.setViewportSize({width:1280,height:900});
 // A collapsed section contributes no targets; focus/reading layout stays intact.
 await rail.locator('[data-sidebar-section="recent"] .a-sidebar-section-heading>button').first().click();await expect(rail.locator('[data-sidebar-section="recent"] .a-sidebar-section-heading>button').first()).toHaveAttribute('aria-expanded','false');await blur();await hold();await expect(badges).toHaveCount(1);await release();
 assert.deepEqual(errors,[]);await writeFile(out+'/result.json',JSON.stringify({passed:true,durationMs:Math.round(performance.now()-started),browser:browser.version(),geometry:before,frozen,reorderedTarget:ninth.id,selected:chosen,draftRoundtrip:true,dirtyCanvas:{navigationRefused:true,mountRetained:true,editRetained:true,composerRetained:true},scoped,scopedSelected:scopedTarget.id,narrow,limits:['Synthetic provider and Chromium; native OS shortcut assignments not exercised.','Workspace-scoped sidebar uses the canonical shell composition action; quiet sidebar has no text-filter control.']},null,2)+'\n');
 console.log('PASS: frozen targets, unchanged desktop/narrow geometry, unsent draft roundtrip, dirty Canvas refusal, workspace-scoped/fewer targets, typing/IME/dialog/tab-key/blur/Escape/collapse guards.');
}catch(error){primary=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 let failure;for(const cleanup of [()=>browser?.close(),()=>vite?.close(),async()=>{if(fixture.exitCode!==null)return;const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await done;if(code!==0)throw Error(`Fixture cleanup ${code}/${signal}`)}finally{clearTimeout(timer)}}])try{await cleanup()}catch(error){failure??=error}
 if(logs)await writeFile(out+'/fixture.log',logs).catch(()=>{});if(!primary&&failure)throw failure;
}
