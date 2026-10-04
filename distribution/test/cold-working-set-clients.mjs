/** Optional real browser and installed Ratatui clients; no submitted inputs. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {writeFile} from 'node:fs/promises';

export async function qualifyMixedClients({app,directory,seed}){
 const {chromium,expect}=await import(pathToFileURL(process.env.COLD_PLAYWRIGHT_ENTRY));
 let driver,browser,id=0;const pending=new Map(),errors=[],frames=[];
 const rpc=(op,key,args={})=>new Promise((resolve,reject)=>{const request=++id,timer=setTimeout(()=>{pending.delete(request);reject(Error('PTY fixture timeout: '+op));},35000);pending.set(request,{resolve,reject,timer});driver.stdin.write(JSON.stringify({id:request,op,key,...args})+'\n');});
 try{
  browser=await chromium.launch({headless:true});
  const contexts=await Promise.all([browser.newContext({viewport:{width:1280,height:900}}),browser.newContext({viewport:{width:1280,height:900}})]);
  const pages=await Promise.all(contexts.map(c=>c.newPage()));
  const act=(page,action,args)=>page.evaluate(({action,args})=>window.amplifier.dispatch(action,args),{action,args});
  for(const [index,page]of pages.entries()){
   page.on('pageerror',e=>errors.push(e.message));
   page.on('websocket',socket=>socket.on('framesent',frame=>frames.push({client:index,frame:JSON.parse(frame.payload)})));
   await page.goto(app.url);await page.waitForFunction(()=>window.amplifier?.getState().transport==='ahp/0.9.0');
   await act(page,'session.select',{id:seed.sessions[index].session});
  }
  driver=spawn(process.env.COLD_PTY_PYTHON,['-I','-B','-u',fileURLToPath(new URL('cold-working-set-pty.py',import.meta.url)),directory],{cwd:directory,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
  driver.stderr.on('data',chunk=>process.stderr.write(chunk));
  createInterface({input:driver.stdout}).on('line',line=>{const row=JSON.parse(line),item=pending.get(row.id);if(!item)return;clearTimeout(item.timer);pending.delete(row.id);row.error?item.reject(Error(row.error)):item.resolve(row.result);});
  driver.on('exit',code=>{for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('PTY driver exited '+code));}pending.clear();});
  for(const [index,key]of ['tui-a','tui-b'].entries())await rpc('start',key,{args:[process.env.COLD_TUI_EXECUTABLE,'--server',app.url.replace(/^http/,'ws')+'/ahp','--workspace',seed.workspace,'--state-dir',join(directory,key,'state'),'--client',key,'--session',seed.sessions[index].session]});
  const composer=page=>page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  for(const page of pages)await expect(composer(page)).toBeEnabled();
  for(const [index,page]of pages.entries())await expect(page.getByText('Request '+(index+1)+':'+(seed.sessions[index].turns-1),{exact:true})).toBeVisible();
  await rpc('wait','tui-a',{text:'Answer 1:9999'});await rpc('wait','tui-b',{text:'Answer 2:1999'});
  // Read-only lazy pages may settle first. Once the visible clients are ready,
  // private typing must produce no WebSocket request or shared host action.
  await new Promise(r=>setTimeout(r,350));
  const beforeFrames=frames.length,beforeSequence=app.host.diagnostics().serverSeq;
  await composer(pages[0]).fill('Private browser A');await composer(pages[1]).fill('Private browser B');
  await rpc('send','tui-a',{text:'Private terminal A'});await rpc('send','tui-b',{text:'Private terminal B'});
  await rpc('wait','tui-a',{text:'Private terminal A'});await rpc('wait','tui-b',{text:'Private terminal B'});
  await new Promise(r=>setTimeout(r,400));
  assert.equal(frames.length,beforeFrames,'Private browser drafts must not send wire requests');
  assert.equal(app.host.diagnostics().serverSeq,beforeSequence,'Private browser/terminal drafts must not cause shared actions');
  assert.equal(app.host.diagnostics().activeAgents,0);
  assert.ok(!(await rpc('read','tui-a')).includes('Private terminal B'));
  assert.ok(!(await rpc('read','tui-b')).includes('Private terminal A'));
  await pages[0].reload();await expect(composer(pages[0])).toHaveValue('Private browser A');
  await rpc('restart','tui-a');await rpc('wait','tui-a',{text:['Private terminal A','Answer 1:9999']});
  await expect(composer(pages[1])).toHaveValue('Private browser B');
  await act(pages[0],'session.select',{id:seed.sessions[1].session});await expect(composer(pages[0])).toHaveValue('');
  await expect(composer(pages[1])).toHaveValue('Private browser B');
  await act(pages[0],'session.select',{id:seed.sessions[0].session});await expect(composer(pages[0])).toHaveValue('Private browser A');
  for(const [index,page]of pages.entries())await page.screenshot({path:join(directory,'browser-'+index+'.png'),fullPage:true});
  await rpc('wait','tui-a',{text:['Private terminal A','Answer 1:9999']});
  await rpc('wait','tui-b',{text:['Private terminal B','Answer 2:1999']});
  await rpc('save');assert.deepEqual(errors,[]);
  const snapshots=await Promise.all(pages.map(page=>page.evaluate(()=>{const s=window.amplifier.getState();return {sessionId:s.selectedSessionId,messageCount:s.sessions?.find(row=>row.id===s.selectedSessionId)?.messages?.length};})));
  for(const [index,snapshot]of snapshots.entries()){
   assert.equal(snapshot.sessionId,seed.sessions[index].session);
   assert.ok(snapshot.messageCount>0&&snapshot.messageCount<=150,'Browser projection remains within 50 native turns');
  }
  const workingSet=app.host.diagnostics().workingSet;
  assert.ok(workingSet.selectedNativeWindows<=2);
  assert.ok(workingSet.selectedNativeTurnIds<=100);
  const result={browsers:2,actualInstalledRatatuiPTYs:2,privateDraftRequests:0,privateDraftSharedActions:0,typedOnly:true,inputsSubmitted:0,
   reloadAndTerminalRestartPreserveDraft:true,navigationPreservesPerClientPerSessionDraft:true,errors,snapshots,workingSet,
   terminalModesRestored:(await rpc('close')).terminalModesRestored};
  await writeFile(join(directory,'mixed-clients.json'),JSON.stringify(result,null,2));return result;
 }catch(error){
  if(browser)for(const [index,context]of browser.contexts().entries())for(const page of context.pages()){
   await writeFile(join(directory,'browser-failure-'+index+'.txt'),await page.locator('body').innerText()).catch(()=>{});
   await page.screenshot({path:join(directory,'browser-failure-'+index+'.png'),fullPage:true}).catch(()=>{});
  }
  if(driver)try{await rpc('save');}catch{}
  throw error;
 }finally{
  if(driver){try{await rpc('close');}catch{}driver.stdin.end();}
  await browser?.close();
 }
}
