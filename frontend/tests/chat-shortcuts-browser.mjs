// Real sidebar and guarded selection; browser-reserved keys remain untouched.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const source=fileURLToPath(new URL('../',import.meta.url));
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
 // A collapsed section contributes no targets; focus/reading layout stays intact.
 await rail.locator('[data-sidebar-section="recent"] .a-sidebar-section-heading>button').first().click();await expect(rail.locator('[data-sidebar-section="recent"] .a-sidebar-section-heading>button').first()).toHaveAttribute('aria-expanded','false');await blur();await hold();await expect(badges).toHaveCount(1);await release();
 assert.deepEqual(errors,[]);await writeFile(out+'/result.json',JSON.stringify({passed:true,geometry:before,frozen,reorderedTarget:ninth.id,selected:chosen,limits:['Synthetic provider and Chromium; native OS shortcut assignments not exercised.']},null,2)+'\n');
 console.log('PASS: numbered chat targets freeze across live reorder; geometry unchanged; guarded keyboard navigation; typing/IME/dialog/tab-key/blur/Escape/collapse guards.');
}catch(error){primary=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 let failure;for(const cleanup of [()=>browser?.close(),()=>vite?.close(),async()=>{if(fixture.exitCode!==null)return;const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await done;if(code!==0)throw Error(`Fixture cleanup ${code}/${signal}`)}finally{clearTimeout(timer)}}])try{await cleanup()}catch(error){failure??=error}
 if(logs)await writeFile(out+'/fixture.log',logs).catch(()=>{});if(!primary&&failure)throw failure;
}
