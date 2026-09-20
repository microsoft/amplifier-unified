import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=async()=>(await action('canvas.views.inspect')).result.views;
 const view=async id=>(await inspect()).find(v=>v.viewId===id);
 const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
 await action('session.create');const otherSession=(await state()).selectedSessionId;
 await action('session.create');const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasControlsPinned:true,draft:'Keep composer target and draft'}});
 await action('canvas.show',{kind:'markdown',title:'Other artifact',content:'# Other source'});
 const otherArtifact=await view('primary');
 await action('canvas.show',{kind:'markdown',title:'Editable artifact',content:'# Original retained source'});
 const first=await view('primary');
 await action('canvas.views.open',{resourceId:first.resourceId,sessionId:session});
 const manifest={id:'example.dirty-editor',label:'Unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 // The edit deliberately lives only in React state, exercising the public dirty contract.
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");return React.createElement("label",null,"Unsaved renderer edit",React.createElement("input",{value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await page.getByRole('combobox',{name:'Open secondary with'}).selectOption(digest);
 const secondary=page.locator('[data-canvas-view="secondary"]').getByLabel('Unsaved renderer edit');
 await secondary.fill('SECONDARY UNSAVED EDIT');
 await expect.poll(async()=>(await view('secondary')).dirty).toBe(true);
 const secondaryTarget=target(await view('secondary'));
 await page.evaluate(()=>{window.secondaryEditor=document.querySelector('[data-canvas-view="secondary"] input')});
 await page.getByRole('button',{name:'Close secondary view'}).click();
 await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 for(let cycle=0;cycle<2;cycle++){
  await page.getByRole('button',{name:'Saved artifacts (2)'}).click();
  await expect(page.getByRole('region',{name:'Saved canvas artifacts'})).toBeVisible();
  await expect(secondary).toHaveCount(1);await expect(secondary).toBeHidden();
  assert.equal(await secondary.evaluate(el=>el.closest('.a-canvas-workspace').inert),true);
  await page.getByRole('button',{name:'Saved artifacts (2)'}).click();
  await expect(secondary).toBeVisible();await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 }
 await page.getByRole('button',{name:'Close canvas panel'}).click();
 await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 assert.equal((await state()).canvas.open,true);
 // A secondary edit must not prevent safe primary/chat navigation.
 await action('canvas.select',{id:otherArtifact.resourceId});
 await action('canvas.tabClose',{id:otherArtifact.resourceId});
 await action('session.select',{id:otherSession});
 await expect(secondary).toBeVisible();await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 await action('session.select',{id:session});
 assert.deepEqual(target(await view('secondary')),secondaryTarget);
 assert.equal(await page.evaluate(()=>window.secondaryEditor===document.querySelector('[data-canvas-view="secondary"] input')),true);
 // Primary edits block all transitions that would replace the primary mount.
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const primary=page.locator('[data-canvas-view="primary"]').getByLabel('Unsaved renderer edit');
 await primary.fill('PRIMARY UNSAVED EDIT');
 await expect.poll(async()=>(await view('primary')).dirty).toBe(true);
 const before=(await inspect()).map(target);
 await page.getByRole('button',{name:'Saved artifacts (2)'}).click();
 await page.getByRole('button',{name:/Other artifact.*markdown/}).click();
 await expect(primary).toBeVisible();await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
 for(const [name,args] of [
  ['canvas.close',{}],['canvas.select',{id:otherArtifact.resourceId}],
  ['canvas.tabClose',{id:first.resourceId}],['canvas.show',{kind:'text',content:'Do not replace'}],
  ['session.select',{id:otherSession}],['session.create',{}],['session.fork',{id:session}],
  ['workspace.create',{path:root+'output/dirty-guard-must-not-create'}],
 ]){
  await assert.rejects(()=>action(name,args),/viewer edit/);
  assert.equal((await state()).selectedSessionId,session);
  assert.equal((await state()).view.draft,'Keep composer target and draft');
  assert.deepEqual((await inspect()).map(target),before);
  await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
  await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 }
 // Explicit recovery discards just the targeted local edit and retains sources.
 await action('canvas.views.recover',target(await view('primary')));
 await expect(primary).toHaveCount(0);
 assert.equal((await state()).canvas.content,'# Original retained source');
 await expect(secondary).toHaveValue('SECONDARY UNSAVED EDIT');
 await action('canvas.views.recover',target(await view('secondary')));
 await expect(secondary).toHaveCount(0);
 await action('canvas.close');assert.equal((await state()).canvas.open,false);
 await action('canvas.reopen');
 await expect(page.getByRole('combobox',{name:'Open secondary with'})).toHaveValue('builtin.canvas.markdown');
 assert.equal((await state()).view.draft,'Keep composer target and draft');
 assert.equal((await state()).canvasArtifacts.length,2);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['Library hides and retains dirty renderer DOM','panel close preserves dirty secondary','primary and chat navigation retain dirty secondary','dirty primary prevents selection/composer/binding changes','explicit targeted recovery and close/reopen retain sources and draft']}));
} finally {await browser?.close();fixture.kill();}
