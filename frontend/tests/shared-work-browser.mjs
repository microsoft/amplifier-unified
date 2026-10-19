// Test-first real-host journey. Run only in the owned isolated guest after build.
// The baseline has no Work tab; no result is claimed until this script executes.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py','--chat-controls','--shared-work'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Shared Work fixture'});
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-shared-work';
const headers={Authorization:'Bearer fixture-browser-control-token'};
const launch={headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]};
let browser,originalError,result;
try {
 const url=await new Promise((resolve,reject)=>{
  let body='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{body+=chunk;for(const line of body.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
 });
 await mkdir(output,{recursive:true});
 browser=await chromium.launch(launch);
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 const errors=[],calls=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const manifest=await (await page.request.get(url+'/fixture/shared-work')).json();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 // The selected draft belongs to a different chat; reading Work must not touch it.
 const created=await action('session.create',{workspace:manifest.workspace.path,title:'Draft owner'});
 const draftOwner=created.result.sessionId;
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Private draft remains unchanged.');
 await action('attachment.add',{sessionId:draftOwner,name:'private-draft.txt',base64:'cHJpdmF0ZQ=='});
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:manifest.workspace.id,workWorkspaceTab:'chats',navExpanded:false}});
 const before=calls.length;
 await page.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Work',exact:true}).click();
 const work=page.getByRole('region',{name:'Shared work',exact:true});
 await expect(work).toBeVisible();
 const filter=work.getByRole('combobox',{name:'Show work'});
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByText(/historical reconciliation is incomplete/i)).toBeVisible();
 const found=new Set();
 for(let pageNumber=0;pageNumber<5;pageNumber++) {
  await expect(work.locator('[data-work-id]').first()).toBeVisible();
  for(const id of await work.locator('[data-work-id]').evaluateAll(rows=>rows.map(row=>row.dataset.workId)))found.add(id);
  const next=work.getByRole('button',{name:'Next work',exact:true});
  if(await next.isDisabled())break;
  const previous=await work.locator('[data-work-id]').first().getAttribute('data-work-id');
  await next.click();
  await expect(work.locator('[data-work-id]').first()).not.toHaveAttribute('data-work-id',previous);
 }
 assert.deepEqual([...found].sort(),[...manifest.acceptedIds].sort(),'Every accepted-open record reachable through bounded pages');
 assert.ok(!found.has(manifest.proposal.id));assert.ok(!found.has(manifest.closed.id));
 await work.getByRole('textbox',{name:'Find work'}).fill('Retain the original promise');
 const row=work.locator(`[data-work-id="${manifest.target.id}"]`);
 await row.focus();await page.keyboard.press('Enter');
 const detail=work.getByRole('article',{name:'Work details'});
 await expect(detail).toBeVisible();
 for(const fact of ['Commitment','Execution','Verification','Human decision','Delivery','Cleanup'])await expect(detail.getByText(fact,{exact:true})).toBeVisible();
 await expect(detail.getByText(/Returned.*unverified/i)).toBeVisible();
 await expect(detail.getByText(/Independent checks passed|Cleanup complete|Shipped/)).toHaveCount(0);
 const source=detail.getByRole('region',{name:'Original source'});
 for(let index=0;index<20&&!((await source.innerText()).includes('ORIGINAL-END'));index++) {
  await detail.getByRole('button',{name:'Read more source',exact:true}).click();
 }
 await expect.poll(()=>source.locator('blockquote').textContent()).toBe(manifest.originalText);
 await expect(source.getByText(manifest.originalSha256,{exact:true})).toBeVisible();
 assert.equal(await source.locator('script').count(),0,'Source rendered as text, never executable HTML');
 await detail.getByRole('button',{name:'Read history',exact:true}).click();
 await expect(detail.getByText('Participant reports success. This is not a retained check receipt.',{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Read correction source',exact:true}).click();
 await expect(detail.getByText(manifest.correctionText,{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Next history',exact:true}).click();
 await expect(detail.getByText('The current cold-reopen outcome is unknown; the old success is retained.',{exact:true})).toBeVisible();
 assert.equal((await state()).selectedSessionId,draftOwner);
 const readCalls=calls.slice(before);
 assert.ok(readCalls.filter(row=>row.action.startsWith('workspace.work.')).every(row=>['workspace.work.list','workspace.work.read','workspace.work.history','workspace.work.source'].includes(row.action)));
 assert.ok(readCalls.every(row=>!['session.select','conversation.send','worker.spawn','task.start','coordination.create'].includes(row.action)));
 assert.ok(readCalls.filter(row=>row.action==='workspace.work.list').every(row=>row.args.limit<=50));
 // New incoming state cannot steal focus; Back restores the explicit item.
 for(const width of [390,320,1440]) {
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);
  if(width<720) {
   const back=detail.getByRole('button',{name:'Back to Work',exact:true});await expect(back).toBeVisible();
   const box=await back.boundingBox();assert.ok(box.height>=44&&box.width>=44,'44px Back target');
   await back.click();await expect(row).toBeFocused();
   const rowBox=await row.boundingBox();assert.ok(rowBox.height>=44&&rowBox.width>=44,'44px item target');
   await page.keyboard.press('Enter');
   await expect(detail.getByRole('heading',{name:manifest.target.title,exact:true})).toBeFocused();
  }
  await page.screenshot({path:path.join(output,`shared-work-${width}.png`)});
 }
 // A second browser client has independent presentation choices.
 const peer=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 peer.on('pageerror',error=>errors.push(error.message));
 await peer.goto(url);await peer.waitForFunction(()=>window.amplifier);
 await peer.evaluate(id=>window.amplifier.dispatch('view.update',{patch:{workSurface:'workspace',workWorkspaceId:id,workWorkspaceTab:'work'}}),manifest.workspace.id);
 const peerWork=peer.getByRole('region',{name:'Shared work',exact:true});
 await peerWork.getByRole('combobox',{name:'Show work'}).selectOption('closed');
 await expect(peerWork.locator(`[data-work-id="${manifest.closed.id}"]`)).toBeVisible();
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByRole('textbox',{name:'Find work'})).toHaveValue('Retain the original promise');
 // Only explicit Open chat traverses the existing guarded navigation path.
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await action('session.select',{id:draftOwner});
 await expect(composer).toHaveDraft('Private draft remains unchanged.');
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 const observation=await (await page.request.get(url+'/fixture/shared-work')).json();
 assert.deepEqual(observation.forbiddenRuntimeCalls,[]);
 const runtime=await (await page.request.get(url+'/fixture')).json();
 assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.sent,[]);assert.equal(runtime.workerCount,0);
 assert.deepEqual(errors,[]);
 result={status:'passed',sourceFixture:true,installedWheel:false,launchOptions:launch,
  acceptedItems:found.size,sourcePagesComplete:true,priorReturnRetained:true,currentUnknown:true,
  widths:[1440,390,320],clients:2,forbiddenRuntimeCalls:observation.forbiddenRuntimeCalls,
  limits:['No live provider or private history','Dirty Canvas/Settings and cold service reopen still require separate qualification']};
} catch(error) {originalError=error;throw error}
finally {await finishFixture({label:'Shared Work fixture',close:[['browser',()=>browser?.close()]],stop,originalError})}
await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
