// The browser operates one installed release through update and recovery.
// Explicit synthetic inputs only; all inference uses the audited offline provider.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
export async function openBrowserJourney({root,url,session,playwright}) {
 const {chromium,expect}=await import(pathToFileURL(playwright));
 const migration=JSON.parse(await readFile(join(root,'journey-migration.json'),'utf8'));
 const legacyTask=JSON.parse(await readFile(join(root,'legacy-task.json'),'utf8'));
 const browser=await chromium.launch({headless:true}),a=await browser.newPage({viewport:{width:1440,height:1100}}),b=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[],checks=[];let first,second,artifact,memory,notification,drop=true,answerSubmissions=0;
 const act=(page,action,args={})=>page.evaluate(({action,args})=>window.amplifier.dispatch(action,args),{action,args});
 const ready=page=>page.waitForFunction(()=>window.amplifier?.getState().connection?.connected,{},{timeout:60000});
 const select=async page=>{await page.goto(url);await ready(page);await act(page,'session.select',{id:session});};
 const idle=page=>page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.status==='idle',{},{timeout:60000});
 const audit=()=>readFile(join(root,'provider-requests.jsonl'),'utf8');
 const count=async()=>(await audit()).trim().split('\n').length;
 const request=(action,args={})=>act(a,action,action.startsWith('notifications.')?args:{sessionId:session,...args}).then(r=>r.result);
 const pending=page=>page.getByRole('form',{name:'Answer: '+(second||first).prompt});
 for(const page of [a,b])page.on('pageerror',e=>errors.push(e.message));
 await a.routeWebSocket('**/ahp',route=>{
  const server=route.connectToServer(),ids=new Set();
  route.onMessage(message=>{const r=JSON.parse(String(message));if(r.params?.operation==='question.answer'){answerSubmissions++;ids.add(r.id)}server.send(message)});
  server.onMessage(message=>{const r=JSON.parse(String(message));if(drop&&ids.has(r.id)&&r.result?.accepted){drop=false;route.send(JSON.stringify({jsonrpc:'2.0',id:r.id,error:{code:-32000,message:'Rehearsal discarded the committed answer acknowledgement'}}));return}route.send(message)});
 });
 const inspectSaved=async()=>{
  if(migration.observations){
   const source=migration.observations.source;
   const page=await request('observation.list');assert.equal(page.watches.length,source.watchIds.length);
   for(const id of source.watchIds){
    const report=await request('observation.report',{id}),original=source.expected[id];
    assert.equal(report.watch.status,original.watch.status==='active'?'needs_review':original.watch.status);
    assert.equal(report.watch.nextDue,null);assert.equal(report.runs.length,original.runs.length);
    assert.equal(report.runs[0].phase,'abandoned_read');assert.equal(report.handoffs.length,original.outboxes.length);
    if(original.outboxes.length){
     const old=original.outboxes[0];assert.equal(report.handoffs[0].phase,['accepted','submitting'].includes(old.phase)?'unknown':old.phase==='pending'?'suppressed':old.phase);
     assert.deepEqual(report.handoffs[0].outcome,old.outcome);assert.equal(report.handoffs[0].inputId,old.inputId);
    }
   }
   for(const command of source.commands){
    const result=await request('observation.request',{requestId:command.requestId});
    assert.equal(result.receipt.status,'legacy_observed');assert.equal(result.receipt.originalCommandId,command.id);
   }
   assert.deepEqual((await request('observation.observers')).observers,[],'Old source qualification is not new executable authority');
  }
  const task=await request('runtime.control',{operation:'task.get',args:{}});
  assert.deepEqual(task.task,legacyTask.task,'Saved task, corrections, dependencies and pause must survive');
  assert.equal(task.goal,null);assert.equal(task.historyCount,1);
  const history=await request('runtime.control',{operation:'task.history',args:{limit:10}});
  assert.deepEqual(history.items,legacyTask.history);
  for(const [commandId,original] of Object.entries(legacyTask.receipts)){
   const receipt=await request('runtime.control',{operation:'task.receipt',args:{commandId}});
   assert.equal(receipt.available,true);assert.equal(receipt.replayed,false);
   for(const [key,value] of Object.entries(original))assert.deepEqual(receipt.receipt[key],value);
  }
  const old=await request('question.read',{id:migration.questions.source.answeredId});
  assert.equal(old.status,'answered');assert.equal(old.answer.text,'PDF');assert.equal(old.delivery.status,'unknown');
  const schedule=await request('schedule.read',{id:migration.schedules.source.scheduleId});
  assert.equal(schedule.schedule.status,'needs_review');assert.equal(schedule.runs.length,2);
  assert.equal(schedule.runs.find(r=>r.id===migration.schedules.source.runs.find(v=>v.phase==='submitting').id).phase,'unknown');
  return schedule;
 };
 try{
  await select(a);await select(b);await idle(a);
  await expect(a.locator('strong').filter({hasText:'violet compass'})).toBeVisible();
  first=await request('question.read',{id:migration.questions.source.pendingId});assert.equal(first.status,'pending');await inspectSaved();
  await expect(pending(a)).toBeVisible();await expect(pending(b)).toBeVisible();
  await pending(a).getByRole('textbox').fill('JOURNEY-COPPER-941');await expect(pending(b).getByRole('textbox')).toHaveValue('');
  await act(a,'view.update',{patch:{draft:'Private unsent draft survives the switch'}});
  await act(b,'view.update',{patch:{draft:'Other browser keeps its own draft'}});
  artifact=(await request('canvas.show',{kind:'markdown',title:'Retained journey output',content:'**Violet compass** before the update.'})).artifact;
  memory=await request('memory.create',{scope:'workspace',text:'The release rehearsal keeps violet compass.'});
  const settings=await request('notifications.get');
  await request('notifications.save',{expectedRevision:settings.revision,patch:{enabled:false,preview:!settings.preview}});
  notification=await request('notifications.get');
  assert.equal(await count(),2,'Passive migration, drafts and explicit non-model actions start no inference');
  checks.push('old history renders as Markdown','migrated questions and complete schedule history visible','original saved task, completed history, corrections, dependencies and command receipts retained','unknown past work remains unknown','independent private browser drafts');
  if(migration.observations)checks.push('five original watches, read histories, result handoffs and command receipts survive without reactivation');
 }catch(e){await browser.close();throw e;}
 return {
  async disconnect(){for(const page of [a,b])await page.goto('about:blank');},
  async afterUpdate(){
   const before=await count();for(const page of [a,b])await select(page);
   assert.equal(await a.evaluate(()=>window.amplifier.getState().view.draft),'Private unsent draft survives the switch');
   assert.equal(await b.evaluate(()=>window.amplifier.getState().view.draft),'Other browser keeps its own draft');
   await expect(pending(a).getByRole('textbox')).toHaveValue('JOURNEY-COPPER-941');await expect(pending(b).getByRole('textbox')).toHaveValue('');
   assert.equal(await count(),before);await inspectSaved();
   await pending(a).getByRole('button',{name:'Submit answer',exact:true}).click();
   const uncertain=a.getByLabel('Unconfirmed question answer');await expect(uncertain).toBeVisible();
   await expect.poll(async()=>((await request('question.read',{id:first.id})).delivery||{}).status,{timeout:60000}).toBe('accepted');
   await idle(a);await a.reload();await ready(a);await expect(uncertain).toContainText('JOURNEY-COPPER-941');
   await uncertain.getByRole('button',{name:'Check saved question'}).click();await expect(uncertain).toHaveCount(0);
   assert.equal(answerSubmissions,1);assert.equal(await count(),before+1);await expect(pending(b)).toHaveCount(0);
   second=await request('question.create',{prompt:'Which follow-up survives full recovery?',dependency:'Pending future answer',required:true,options:[],allowFreeText:true});
   await pending(a).getByRole('textbox').fill('Unsent answer after update');
   artifact=(await request('canvas.versions.revise',{id:artifact.id,expectedRevision:artifact.revision,title:artifact.title,content:'**Violet compass** after the update.'})).artifact;
   const original=await request('canvas.versions.inspect',{id:artifact.id,version:1,includeSource:true});assert.equal(original.body.content,'**Violet compass** before the update.');
   await inspectSaved();assert.deepEqual(await request('notifications.get'),notification);
   checks.push('signed update preserves both browser drafts and pending answer','lost question acknowledgement recovered without resubmission','one explicit migrated answer reaches the actual provider once','post-update artifact revision and original version retained');
  },
  async afterRecovery(){
   const before=await audit();for(const page of [a,b])await select(page);await idle(a);
   const requests=before.trim().split('\n').map(JSON.parse);
   assert.ok(requests.length>0);
   for(const messages of requests){
    const text=JSON.stringify(messages);
    assert.ok(text.includes('Saved task state')&&text.includes('Use the revised copper totals')&&text.includes('paused'),'Original paused task must reach the actual provider boundary');
   }
   assert.equal(await a.evaluate(()=>window.amplifier.getState().view.draft),'Private unsent draft survives the switch');
   assert.equal(await b.evaluate(()=>window.amplifier.getState().view.draft),'Other browser keeps its own draft');
   await expect(pending(a).getByRole('textbox')).toHaveValue('Unsent answer after update');await expect(pending(b).getByRole('textbox')).toHaveValue('');
   const answer=await request('question.read',{id:first.id});assert.equal(answer.answer.text,'JOURNEY-COPPER-941');assert.equal(answer.delivery.status,'accepted');
   await inspectSaved();
   const saved=await request('canvas.versions.inspect',{id:artifact.id,includeSource:true});assert.equal(saved.artifact.revision,2);assert.equal(saved.body.content,'**Violet compass** after the update.');
   assert.equal((await request('memory.read',{id:memory.id})).text,'The release rehearsal keeps violet compass.');
   assert.deepEqual(await request('notifications.get'),notification);assert.equal(await audit(),before,'Recovery readback must not replay any provider call');assert.equal(answerSubmissions,1);
   await act(a,'view.update',{patch:{panel:null,canvasOpen:false}});await a.setViewportSize({width:390,height:844});
   assert.ok(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await a.screenshot({path:join(root,'journey-restored-mobile.png'),fullPage:true});assert.deepEqual(errors,[]);
   checks.push('full recovery retains new answer, pending question, artifact, memory and settings','both private drafts survive update and recovery','restored unknown scheduled input never resent','mobile layout has no horizontal overflow');
   const receipt={passed:true,checks,answerSubmissions,errors,artifactId:artifact.id,memoryId:memory.id,migration,paidInference:false};
   await writeFile(join(root,'browser-journey.json'),JSON.stringify(receipt,null,2));return receipt;
  },
  async close(){await browser.close();}
 };
}
