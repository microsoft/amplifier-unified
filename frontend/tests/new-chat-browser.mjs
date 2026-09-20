// Real packaged UI, isolated storage, and synthetic runtime. No model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
const out='/tmp/amplifier-chat-controls';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=async()=>await(await page.request.get(url+'/fixture')).json();
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await action('view.update',{patch:{navPinned:true}});
 const launcher=page.getByRole('button',{name:'New chat',exact:true});await expect(launcher).toHaveCount(1);
 assert.equal(await page.locator('.a-top [data-action="session.create"],.a-top [data-action="session.draft"]').count(),0);
 await expect(page.locator('.a-canvas-toggle')).toHaveText('');await expect(page.locator('.a-canvas-toggle')).toHaveAttribute('title','Open canvas');
 await expect(page.getByRole('button',{name:'Chat details',exact:true})).toBeDisabled();
 const originalWorkspace=(await state()).settings.workspace;
 await launcher.click();await launcher.click();assert.equal((await state()).sessions.length,0);
 await page.getByText('Chat settings',{exact:true}).click();
 await page.locator('#new-chat-name').pressSequentially('Workspace research');
 await page.getByLabel('Use the bundle’s model').uncheck();
 await page.locator('#new-chat-provider').fill('test-provider');await page.locator('#new-chat-model').fill('chosen-model');await page.locator('#new-chat-effort').fill('high');
 await page.locator('#new-chat-workspace').fill(originalWorkspace+'/../');
 const draftSaved=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.args?.patch?.draft==='Do the planned work');
 await composer.fill('Do the planned work');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Use these notes')});await page.getByRole('button',{name:'Remove notes.txt'}).waitFor();
 assert.equal((await state()).sessions.length,0);assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).started,[]);
 await draftSaved;await page.reload();await expect(composer).toHaveValue('Do the planned work');await page.getByText('Chat settings',{exact:true}).click();await expect(page.locator('#new-chat-name')).toHaveValue('Workspace research');await expect(page.locator('#new-chat-model')).toHaveValue('chosen-model');await expect(page.getByRole('button',{name:'Remove notes.txt'})).toBeVisible();
 await page.screenshot({path:out+'/new-chat-desktop.png'});
 for(const width of [390,320]){await page.setViewportSize({width,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+`/new-chat-${width}.png`})}
 await page.setViewportSize({width:1280,height:900});
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 let current=await state();assert.equal(current.sessions.length,1);const sid=current.selectedSessionId,chat=current.sessions.find(row=>row.id===sid);assert.equal(chat.title,'Workspace research');assert.equal(chat.selection.model,'chosen-model');assert.notEqual(chat.workspace,originalWorkspace);
 const submitted=(await inspect()).sent;assert.equal(submitted.length,1);assert.equal(submitted[0].selection.model,'chosen-model');assert.equal(submitted[0].attachments[0].name,'notes.txt');
 await expect(page.getByRole('button',{name:'Chat details',exact:true})).toBeEnabled();await page.locator('.a-composer').getByRole('button',{name:'Chat controls',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Chat controls',exact:true})).toBeVisible();await page.getByRole('button',{name:'Goals & modes',exact:true}).click();await expect(page.getByLabel('Goal condition')).toBeVisible();await expect(page.getByLabel('Mode',{exact:true})).toBeVisible();await page.screenshot({path:out+'/composer-chat-controls.png'});await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.getByRole('button',{name:'Chat details',exact:true}).click();const details=page.getByRole('dialog',{name:'Chat details',exact:true});await expect(details.getByRole('button',{name:'Copy session ID',exact:true})).toBeVisible();await details.getByRole('button',{name:'Copy session ID',exact:true}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),sid);await expect(details.getByRole('button',{name:'Download Markdown',exact:true})).toBeVisible();await expect(details.getByLabel('Goal condition')).toHaveCount(0);
 const downloaded=page.waitForEvent('download');await details.getByRole('button',{name:'Download Markdown',exact:true}).click();const file=await downloaded;assert.match(file.suggestedFilename(),/\.md$/);await page.screenshot({path:out+'/header-chat-details.png'});await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const activity=async(status,workers=[])=>{assert.equal((await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status,workers}})).status(),200)};
 await activity('working');const stop=page.getByRole('button',{name:'Stop response',exact:true});await expect(stop).toHaveCount(1);const box=await stop.boundingBox();await composer.fill('A correction');const send=page.getByRole('button',{name:'Send a correction',exact:true});await expect(send).toBeEnabled();assert.deepEqual(await send.boundingBox(),box);await composer.fill('');await expect(stop).toBeEnabled();assert.deepEqual(await stop.boundingBox(),box);
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'correction.txt',mimeType:'text/plain',buffer:Buffer.from('Next attachment')});await page.getByRole('button',{name:'Remove correction.txt'}).waitFor();await expect(send).toBeEnabled();await page.getByRole('button',{name:'Remove correction.txt'}).click();await expect(stop).toBeEnabled();await stop.click();await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();assert.deepEqual((await inspect()).stopped,[sid]);
 await activity('idle',[{id:'worker',status:'running',title:'Background work'}]);await expect(stop).toBeEnabled();await activity('idle');
 // The damaged source stays referenced and the drawer still opens.
 await action('canvas.show',{kind:'markdown',title:'Recoverable artifact',content:'# Saved artifact'});const aid=(await state()).canvas.id;await action('canvas.close');assert.equal((await page.request.post(url+'/fixture/damage',{data:{artifactId:aid}})).status(),200);await page.getByRole('button',{name:'Open canvas',exact:true}).click();await expect(page.getByRole('complementary',{name:'Agent canvas'})).toBeVisible();await expect(page.getByText('The saved artifact source is unavailable.',{exact:true}).first()).toBeVisible();assert.equal((await state()).canvas.id,aid);assert.equal((await state()).selectedSessionId,sid);await page.screenshot({path:out+'/unavailable-artifact.png'});await action('canvas.close');
 await composer.fill('Keep this existing chat draft');await launcher.click();await expect(page.getByRole('heading',{name:'New chat',exact:true})).toBeVisible();await expect(composer).toHaveValue('');assert.equal((await state()).sessions.length,1);await composer.fill('A different new draft');await action('session.select',{id:sid});await expect(composer).toHaveValue('Keep this existing chat draft');await launcher.click();await expect(composer).toHaveValue('A different new draft');
 assert.deepEqual(errors,[]);assert.equal(calls.filter(row=>row.action==='session.create').length,1);
 console.log('New chat browser passed: one launcher, uncommitted configurable draft, reload and attachments, exact first submission, composer controls, header identity/export, send/stop transitions, damaged canvas, draft isolation, desktop/mobile.');
}finally{await browser?.close();fixture.kill()}
