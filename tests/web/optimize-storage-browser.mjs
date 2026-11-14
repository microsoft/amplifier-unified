// Real built entry + production host. No isolated React render or action mock.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

const root=fileURLToPath(new URL('../../',import.meta.url));
const prefix='maintenance.optimize.';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const mutations=new Set(['preview','policy.save','apply','cancel','resume'].map(name=>prefix+name));

// Use the control's computed accessible name rather than the wrapping label's
// nested option text. Settings can occupy the shared panel dialog.
const scopeControl=section=>section.getByRole('combobox',{name:'Saved sessions to review',exact:true});
const settingsCloseControl=page=>page.locator('[data-part="dialog"]:visible')
 .getByRole('button',{name:/^Close (panel|settings)$/i});

// Reconciliation means a different, passive action with the original identity.
// In particular an exact duplicate apply is still a mutation replay here.
function assertReadOnlyReconcile(requests,command){
 assert.ok([prefix+'apply',prefix+'resume'].includes(command.action));
 assert.equal(typeof command.id,'string');assert.ok(command.id);
 const submissions=requests.filter(row=>row.action===command.action&&row.id===command.id);
 assert.equal(submissions.length,1,'An unknown apply must never be resubmitted');
 assert.ok(requests.some(row=>row.action===prefix+'read'&&row.args?.commandId===command.id),
  'The exact command identity must be read, not guessed from the latest operation');
}

function assertSafeUsageExport(content){
 assert.doesNotMatch(content,/OPTIMIZE-SYNTHETIC-(REQUEST-ONLY|RESPONSE-KEEP)/,
  'Raw diagnostic secrets must remain excluded');
 const lines=content.trim().split('\n');
 assert.ok(content.trim()&&lines.length<=30,'The inspected export must be a bounded, nonempty page');
 const records=lines.map(line=>JSON.parse(line));
 const calls=records.filter(row=>['llm:request','llm:response'].includes(row.event)
  &&row.data?.model==='optimize-fixture');
 assert.deepEqual(calls.map(row=>row.event).sort(),['llm:request','llm:response'],
  'Both real diagnostic directions must remain represented by safe facts');
 for(const row of calls){
  assert.deepEqual(Object.keys(row).sort(),['data','event','timestamp','workspace']);
  assert.equal(typeof row.workspace,'string');assert.ok(row.workspace);
  assert.ok(typeof row.timestamp==='string'&&Number.isFinite(Date.parse(row.timestamp)),
   'The export envelope must retain a usable timestamp');
  assert.deepEqual(row.data,{provider:'fixture',model:'optimize-fixture',
   usage:{input_tokens:7,output_tokens:3,cost:0.01}},
  'Only the known safe provider/model/usage projection belongs in this fixture export');
 }
 return calls;
}

function assertPendingOutbox(before,after){
 assert.equal(before.deliveries.length,2,'Nonempty outbox proof requires both owner-staged pending rows');
 assert.deepEqual(before.outboxTransport,{schedulerStopped:true,clientCalls:0});
 assert.equal(new Set(before.deliveries.map(row=>row.record_id)).size,2);
 const events=[];
 for(const row of before.deliveries){
  assert.equal(row.destination,'optimize-offline-pending');
  assert.equal(row.status,'pending');assert.equal(row.attempts,0);assert.equal(row.error,null);
  assert.ok(before.eventIds.includes(row.record_id));
  assert.match(row.revision,/^[a-f0-9]{64}$/);
  assert.doesNotMatch(row.payload,/OPTIMIZE-SYNTHETIC-(REQUEST-ONLY|RESPONSE-KEEP)/);
  const payload=JSON.parse(row.payload);
  assert.equal(payload.workspace,'optimize-offline-pending');
  assert.equal(payload.data.provider,'fixture');assert.equal(payload.data.model,'outbox-fixture');
  assert.equal(payload.data.event_id,row.record_id);
  assert.equal('raw' in payload.data,false);assert.equal('provider_payloads' in payload.data,false);
  assert.match(payload.idempotency_key,/^aci-event-v1:[a-f0-9]{64}$/);
  events.push(payload.event);
 }
 assert.deepEqual(events.sort(),['llm:request','llm:response']);
 if(after){
  assert.deepEqual(after.deliveries,before.deliveries,
   'Outbox identities/status/payloads changed or extra work was enqueued');
  assert.deepEqual(after.outboxTransport,before.outboxTransport,'Offline transport was contacted');
 }
}

function sourceCheck(){
 const locatorCalls=[];
 const fakeScope={getByRole:(role,options)=>{locatorCalls.push({role,options});return 'scope-control'}};
 assert.equal(scopeControl(fakeScope),'scope-control');
 assert.deepEqual(locatorCalls,[{role:'combobox',options:{name:'Saved sessions to review',exact:true}}]);
 const closeNames=['Close panel','Close settings'];
 const fakePage={locator:selector=>{
  assert.equal(selector,'[data-part="dialog"]:visible');
  return {getByRole:(role,{name})=>{
   assert.equal(role,'button');assert.ok(closeNames.every(value=>name.test(value)));
   assert.equal(name.test('Close unrelated panel'),false);
   return 'visible-panel-close';
  }};
 }};
 assert.equal(settingsCloseControl(fakePage),'visible-panel-close');
 const command={action:prefix+'apply',id:'fixed-command',args:{confirmExpiry:true}};
 const read={action:prefix+'read',args:{commandId:'fixed-command'}};
 assertReadOnlyReconcile([command,read],command);
 assert.throws(()=>assertReadOnlyReconcile([command,command,read],command),/never be resubmitted/);
 assert.throws(()=>assertReadOnlyReconcile([command,{...read,args:{commandId:'another'}}],command),
  /exact command identity/);
 assert.throws(()=>assertReadOnlyReconcile([command,{action:prefix+'read',args:{}}],command),
  /exact command identity/);
 const usage=['llm:request','llm:response'].map(event=>({event,workspace:'/synthetic-workspace',
  timestamp:'2026-11-12T00:00:00Z',data:{provider:'fixture',model:'optimize-fixture',
   usage:{input_tokens:7,output_tokens:3,cost:0.01}}}));
 const jsonl=rows=>rows.map(row=>JSON.stringify(row)).join('\n')+'\n';
 assertSafeUsageExport(jsonl(usage));
 assert.throws(()=>assertSafeUsageExport(''),/nonempty/);
 assert.throws(()=>assertSafeUsageExport(jsonl(usage.slice(1))),/Both real diagnostic directions/);
 assert.throws(()=>assertSafeUsageExport(jsonl(usage.map(row=>({...row,
  data:{...row.data,provider:'wrong-provider'}})))),/known safe/);
 assert.throws(()=>assertSafeUsageExport(jsonl(usage.map(row=>({...row,
  data:{...row.data,usage:{input_tokens:0,output_tokens:3,cost:0.01}}})))),/known safe/);
 assert.throws(()=>assertSafeUsageExport(jsonl(usage.map(row=>({...row,
  data:{...row.data,event_id:'optimize-request'}})))),/known safe/);
 assert.throws(()=>assertSafeUsageExport(jsonl(usage.map(row=>({...row,
  data:{...row.data,raw:'OPTIMIZE-SYNTHETIC-RESPONSE-KEEP'}})))),/Raw diagnostic secrets/);
 const outbox={eventIds:['one','two'],outboxTransport:{schedulerStopped:true,clientCalls:0},
  deliveries:['one','two'].map((record_id,index)=>({record_id,destination:'optimize-offline-pending',
   status:'pending',attempts:0,error:null,revision:'a'.repeat(64),
   payload:JSON.stringify({event:index?'llm:response':'llm:request',workspace:'optimize-offline-pending',
    data:{event_id:record_id,provider:'fixture',model:'outbox-fixture'},
    idempotency_key:'aci-event-v1:'+'b'.repeat(64)})}))};
 assertPendingOutbox(outbox,structuredClone(outbox));
 assert.throws(()=>assertPendingOutbox({...outbox,deliveries:[]}),/Nonempty outbox/);
 assert.throws(()=>assertPendingOutbox(outbox,{...outbox,deliveries:[]}),/changed or extra work/);
 const extra=structuredClone(outbox);extra.deliveries.push(extra.deliveries[0]);
 assert.throws(()=>assertPendingOutbox(outbox,extra),/changed or extra work/);
 const accepted=structuredClone(outbox);accepted.deliveries[0].status='accepted';
 assert.throws(()=>assertPendingOutbox(outbox,accepted),/changed or extra work/);
 const changed=structuredClone(outbox);changed.deliveries[0].payload='{}';
 assert.throws(()=>assertPendingOutbox(outbox,changed),/changed or extra work/);
 console.log(JSON.stringify({passed:true,browserExecuted:false,checks:[
  'exact command read accepted','duplicate mutation rejected',
  'wrong command identity rejected','latest operation is not exact reconciliation',
  'sanitized request and response JSONL accepted without event IDs',
  'empty and missing-direction exports rejected','incorrect provider or usage rejected',
  'restored private event ID and raw diagnostic secret rejected',
  'nonempty pending outbox equality accepted; empty baseline or result rejected',
  'extra enqueue, changed status or changed payload rejected',
  'scope uses exact named combobox; settings dismissal is scoped to visible panel']}));
}

async function serverIdentity(){
 // Managed guest snapshots intentionally omit .git. The manager can provide an
 // immutable provenance packet and its independently recorded byte digest.
 const path=process.env.AMPLIFIER_TEST_SERVER_PROVENANCE;
 if(path){
  const bytes=await readFile(path),expected=process.env.AMPLIFIER_TEST_SERVER_PROVENANCE_SHA256;
  assert.match(expected||'',/^[a-f0-9]{64}$/,'A provenance packet needs its verified SHA-256');
  assert.equal(sha(bytes),expected);
  const packet=JSON.parse(bytes);
  assert.match(packet.serverCommit||'',/^[a-f0-9]{40}$/);
  assert.equal(packet.fixtureSha256,sha(await readFile(new URL('../fixtures/optimize_storage_ui_server.py',import.meta.url))));
  assert.equal(packet.journeySha256,sha(await readFile(fileURLToPath(import.meta.url))));
  return {serverCommit:packet.serverCommit,serverIdentitySource:'manager-provenance-packet',
   provenancePacketSha256:expected};
 }
 return {serverCommit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),
  serverIdentitySource:'source-checkout-git'};
}

async function run(){
 const [{chromium,expect},{startFixture},{finishFixture},{openSettingsPage}]=await Promise.all([
  import('@playwright/test'),import('./fixture-readiness.mjs'),import('./fixture-lifecycle.mjs'),
  import('../../.build/web-client/tests/browser-settings.mjs')]);
 const outputBase=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'working-files'));
 await mkdir(outputBase,{recursive:true});
 const outputDir=await mkdtemp(join(outputBase,'optimize-storage-browser-'));
 const selected=JSON.parse(await readFile(join(root,'components/web-client.source.json'),'utf8'));
 // The commissioning owner supplies the actually accepted selection; this file
 // intentionally does not turn a reviewed-but-unaccepted client into a build.
 const expected=process.env.AMPLIFIER_OPTIMIZE_EXPECTED_CLIENT_COMMIT;
 assert.match(expected||'',/^[a-f0-9]{40}$/,'Run only with an explicit accepted client commit');
 assert.equal(selected.source.revision,expected,'Selected delivery must be the accepted client');
 const componentBytes=await readFile(join(root,'amplifier_web/static/client-component.json'));
 const component=JSON.parse(componentBytes);
 assert.equal(component.source.revision,expected,'Built artifact must name the same selected client');
 const provenance={...await serverIdentity(),
  clientCommit:selected.source.revision,clientInputsSha256:selected.inputsSha256,
  build:component.build,pairedServer:component.pairedServer,componentSha256:sha(componentBytes),
  fixtureSha256:sha(await readFile(new URL('../fixtures/optimize_storage_ui_server.py',import.meta.url))),
  journeySha256:sha(await readFile(fileURLToPath(import.meta.url))),
  productionHost:true,syntheticRuntime:true,syntheticSource:true,providerCalls:false};
 let owned,browser,failure,cleanupFailure;
 const evidence={provenance,checks:[],passed:false,acceptanceComplete:false,browserExecuted:false};
 try{
  owned=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
   args:['-u',join(root,'tests/fixtures/optimize_storage_ui_server.py')],
   env:process.env,tag:'optimize-storage-ui',name:'Optimize storage'});
  const {base}=owned;
  browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'
   ?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
  const context=await browser.newContext({viewport:{width:1100,height:900},
   extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const page=await context.newPage();page.setDefaultTimeout(15000);
  const errors=[],offOrigin=[],requests=[],served=[];
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(!['data:','blob:'].includes(url.protocol)&&url.origin!==base){
    offOrigin.push(url.origin);return route.abort('blockedbyclient');
   }
   return route.fallback();
  });
  page.on('request',request=>{
   if(request.method()==='POST'&&request.url()===base+'/api/actions'){
    const body=request.postDataJSON();
    if(body?.action?.startsWith(prefix))requests.push(body);
   }
  });
  const pendingAssets=[];
  page.on('response',response=>{
   const url=new URL(response.url());
   if(url.origin===base&&url.pathname.endsWith('.js')){
    pendingAssets.push((async()=>served.push({path:url.pathname,status:response.status(),
     sha256:sha(await response.body())}))());
   }
  });
  const observe=async()=>{
   const response=await page.request.get(base+'/fixture/optimize');
   assert.equal(response.ok(),true);return response.json();
  };
  await page.request.post(base+'/fixture/browser-session');
  await page.goto(base);await page.waitForSelector('#amp-one');
  evidence.browserExecuted=true;
  const before=await observe();
  assertPendingOutbox(before);
  assert.ok(before.eventIds.length>=2,'Preservation assertions require real indexed fixture records');
  // Public new-chat navigation gives a genuine no-selection state, not an
  // injected app-state mutation or widening the Optimize scope by fixture fiat.
  await page.getByRole('button',{name:'New chat',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBeFalsy();
  await openSettingsPage(page,'repair');
  const noSelection=page.getByRole('region',{name:'Optimize saved session storage',exact:true});
  await expect(noSelection).toContainText('scope is not widened automatically');
  await expect(scopeControl(noSelection)).toHaveValue('current');
  await expect(noSelection.getByRole('button',{name:'Preview saved storage optimization',exact:true})).toBeDisabled();
  await scopeControl(noSelection).selectOption('all');
  await expect(noSelection.getByRole('button',{name:'Preview saved storage optimization',exact:true})).toBeEnabled();
  assert.equal(requests.filter(row=>mutations.has(row.action)).length,0);
  await settingsCloseControl(page).click();
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),before.sessionId);
  await openSettingsPage(page,'repair');
  const section=page.getByRole('region',{name:'Optimize saved session storage',exact:true});
  await expect(section).toBeVisible();
  await expect(section).toContainText('does not establish that those timers are configured');
  const requestChoice=section.getByLabel('Expire raw request diagnostics during this manual optimization',{exact:true});
  const responseChoice=section.getByLabel('Expire raw response diagnostics during this manual optimization',{exact:true});
  await expect(requestChoice).not.toBeChecked();await expect(responseChoice).not.toBeChecked();
  assert.equal(requests.filter(row=>mutations.has(row.action)).length,0,'Opening Settings is passive');
  await requestChoice.check();
  const hours=section.getByLabel('Keep raw request diagnostics for hours',{exact:true});
  const save=section.getByRole('button',{name:'Save manual optimization choices',exact:true});
  await hours.fill('');await expect(save).toBeDisabled();
  await hours.fill('0');await expect(save).toBeDisabled();
  await hours.fill('12');await save.click();
  await expect(section).toContainText('No optimization ran and no schedule changed.');
  const policy=requests.find(row=>row.action===prefix+'policy.save');
  assert.equal(policy.args.requestKeepHours,12);assert.equal(policy.args.responseKeepHours,null);
  evidence.checks.push('ordinary Settings entry is passive','no-selected-session scope never widens automatically',
   'manual null versus invalid zero and enabled age');
  const scope=scopeControl(section);
  await scope.selectOption('current');
  const previewButton=section.getByRole('button',{name:'Preview saved storage optimization',exact:true});
  await previewButton.click();
  await expect(section.getByRole('button',{name:'Refresh saved optimization status',exact:true})).toBeEnabled();
  await expect.poll(async()=>{
   const last=requests.filter(row=>row.action===prefix+'preview').at(-1);
   return last?.args.scope;
  }).toBe('current');
  await expect(section.getByText('File outcomes (2 on this page)',{exact:true})).toBeVisible();
  await scope.selectOption('all');await previewButton.click();
  await expect.poll(()=>requests.filter(row=>row.action===prefix+'preview').length).toBe(2);
  const lastPreview=requests.filter(row=>row.action===prefix+'preview').at(-1);
  assert.equal(lastPreview.args.scope,'all');assert.equal('sessionId' in lastPreview.args,false);
  const apply=section.getByRole('button',{name:'Apply optimization and diagnostic expiry',exact:true});
  await expect(apply).toBeVisible();await expect(apply).toBeDisabled();
  const frozenText=await section.innerText();
  assert.match(frozenText,/requests 12 hours; responses retained/);
  await section.locator('summary').filter({hasText:'File outcomes'}).click();
  await expect(section).toContainText('eligible');await expect(section).toContainText('held');
  assertPendingOutbox(before,await observe());
  evidence.checks.push('current and explicitly all scopes','truthful held and positive eligible coverage',
   'separate frozen UTC request cutoff and retained response','explicit expiry confirmation');
  await section.getByLabel('Permanently expire eligible raw diagnostics before the frozen UTC cutoffs above',{exact:true}).check();
  const controlled=await page.request.post(base+'/fixture/optimize',{data:{op:'hold-before-first-file'}});
  assert.equal(controlled.ok(),true);
  await apply.click();
  await expect.poll(async()=>(await observe()).workCheckpoint.entered).toBe(true);
  await section.getByRole('button',{name:'Cancel optimization at a safe checkpoint',exact:true}).click();
  assert.equal((await observe()).sourceSha256,before.sourceSha256,'Cancel checkpoint precedes file effects');
  assertPendingOutbox(before,await observe());
  const released=await page.request.post(base+'/fixture/optimize',{data:{op:'release-first-file'}});
  assert.equal(released.ok(),true);
  const resume=section.getByRole('button',{name:'Resume saved optimization',exact:true});
  await expect(resume).toBeEnabled();
  const afterCancelMutations=requests.filter(row=>mutations.has(row.action)).length;
  await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'repair');
  await expect(resume).toBeEnabled();
  assert.equal(requests.filter(row=>mutations.has(row.action)).length,afterCancelMutations);
  assert.equal((await observe()).sourceSha256,before.sourceSha256,'Reload does not implicitly resume');
  assertPendingOutbox(before,await observe());
  evidence.checks.push('real apply receipt then cancellation at durable pre-file checkpoint',
   'cancelled operation reload is passive and explicit resume is available');
  // Lose only the HTTP acknowledgement AFTER the actual host returned its receipt.
  let lost;
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(body?.action===prefix+'resume'&&!lost){
    lost=body;
    const response=await route.fetch();const receipt=await response.json();
    assert.equal(receipt.accepted,true);assert.ok(receipt.result.operationId);
    evidence.resumeReceipt=receipt.result;
    return route.abort('connectionreset');
   }
   return route.fallback();
  });
  await resume.click();
  const exact=section.getByRole('button',{name:'Read exact optimization command outcome',exact:true});
  await expect(exact).toBeEnabled();
  assert.equal(requests.find(row=>row.action===prefix+'apply').args.confirmExpiry,true);
  const mutationCount=requests.filter(row=>mutations.has(row.action)).length;
  await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'repair');
  await expect(exact).toBeEnabled();
  assert.equal(requests.filter(row=>mutations.has(row.action)).length,mutationCount,
   'Reconnect must not replay any mutation');
  await exact.click();
  await expect(section).toContainText('No command was repeated.');
  assertReadOnlyReconcile(requests,lost);
  await expect.poll(async()=>Number((await observe()).sourceSha256!==before.sourceSha256)).toBe(1);
  const after=await observe();
  assertPendingOutbox(before,after);
  assert.equal(after.canonicalUnchanged,true);
  assert.deepEqual(after.eventIds,before.eventIds);assert.deepEqual(after.deliveries,before.deliveries);
  assert.deepEqual(after.runtimeCalls,before.runtimeCalls);
  assert.deepEqual(after.automaticCleanupPolicy,before.automaticCleanupPolicy);
  assert.equal(after.diagnosticsConfigSha256,before.diagnosticsConfigSha256);
  assert.equal(after.diagnosticFacts['optimize-request'].rawPresent,false);
  assert.ok(after.diagnosticFacts['optimize-request'].expired);
  assert.equal(after.diagnosticFacts['optimize-response'].rawPresent,true);
  assert.deepEqual(after.diagnosticFacts['optimize-response'].usage,before.diagnosticFacts['optimize-response'].usage);
  evidence.checks.push('lost real resume receipt reconciled by exact read without mutation replay',
   'reload only reads','canonical source/event IDs/delivery outbox/runtime preserved');
  await settingsCloseControl(page).click();
  const {showFixtureConversation}=await import('./browser-chat-pages.mjs');
  await showFixtureConversation(page);
  // Only observed, ordinary expand/load controls. Never inject a model node or
  // a replacement expiration marker into browser state.
  const model=page.locator('[data-kind="llm"]').filter({hasText:'optimize-fixture'});
  for(let step=0;step<12&&await model.count()===0;step++){
   const earlier=page.getByRole('button',{name:'Load earlier conversation',exact:true});
   if(await earlier.isVisible()){await earlier.click();continue;}
   const collapsed=page.locator('button.a-execution-turn-line[aria-expanded="false"]');
   if(await collapsed.count()){await collapsed.first().click();continue;}
   break;
  }
  await expect(model).toHaveCount(1);
  await model.locator('button.a-execution-action-line').click();
  const expired=model.getByRole('region',{name:'Expired request recording',exact:true});
  await expect(expired).toContainText('intentionally expired');
  await expect(expired).toContainText('(UTC)');
  assert.match(await expired.locator('time').getAttribute('datetime'),/Z$/);
  await expect(model.getByRole('button',{name:/^View request/})).toHaveCount(0);
  await expect(model.getByRole('link',{name:/Download full recorded request/})).toHaveCount(0);
  await expect(model.getByRole('button',{name:'Open recording settings',exact:true})).toHaveCount(0);
  await expect(model.getByRole('button',{name:'Show saved recordings',exact:true})).toHaveCount(0);
  await expect(model).not.toContainText('OPTIMIZE-SYNTHETIC-REQUEST-ONLY');
  await model.getByRole('button',{name:/^View response/}).click();
  await model.getByRole('button',{name:/^Response Object/}).click();
  await expect(model).toContainText('OPTIMIZE-SYNTHETIC-RESPONSE-KEEP');
  const downloading=page.waitForEvent('download',{timeout:15000});
  await model.getByRole('link',{name:'Download full recorded response (JSON)',exact:true}).click();
  const responseDownload=await downloading;
  const responseBytes=await readFile(await responseDownload.path(),'utf8');
  assert.match(responseBytes,/OPTIMIZE-SYNTHETIC-RESPONSE-KEEP/);
  assert.doesNotMatch(responseBytes,/OPTIMIZE-SYNTHETIC-REQUEST-ONLY/);
  evidence.checks.push('actual expired request DOM has UTC notice and no disclosure/download/settings',
   'opposite response disclosure and full download preserved');
  await openSettingsPage(page,'diagnostics');
  await page.getByLabel('Stream filter',{exact:true}).fill('usage');
  await page.getByLabel('Session ID (optional)',{exact:true}).fill(before.nativeId);
  await page.getByRole('button',{name:'Read records',exact:true}).click();
  const exporting=page.waitForEvent('download',{timeout:15000});
  await page.getByRole('button',{name:'Export this page',exact:true}).click();
  const usageFile=await exporting;
  const usageBytes=await readFile(await usageFile.path(),'utf8');
  const exportedCalls=assertSafeUsageExport(usageBytes);
  evidence.usageExport={events:exportedCalls.map(row=>row.event),
   data:exportedCalls.map(row=>row.data),sha256:sha(usageBytes)};
  assertPendingOutbox(before,await observe());
  evidence.outbox={before:before.deliveries,after:(await observe()).deliveries,
   clientCalls:0,acceptance:'pending rows preserved; no remote ACK or send attempted'};
  evidence.checks.push('two real owner-staged pending outbox rows remain byte-for-byte unchanged with no extra enqueue');
  evidence.checks.push('usage page export remains available without raw diagnostic bodies');
  await Promise.all(pendingAssets);assert.ok(served.length>0,'Actual built scripts must have been served');
  for(const script of served){
   const asset=component.assets[script.path.replace(/^\//,'')];
   assert.ok(asset,`Served script missing from built component manifest: ${script.path}`);
   assert.equal(script.status,200);assert.equal(script.sha256,asset.sha256);
  }
  evidence.servedScripts=served;assert.deepEqual(offOrigin,[]);assert.deepEqual(errors,[]);
  evidence.requests=requests;evidence.after=after;evidence.passed=true;
  evidence.notCovered=[];
  evidence.acceptanceComplete=true;
 }catch(error){failure=error;evidence.failure={name:error.name,message:error.message};}
 finally{
  if(owned)try{await finishFixture({label:'Optimize storage',
   close:browser?[['browser',()=>browser.close()]]:[],stop:owned.stop,originalError:failure});}
  catch(error){cleanupFailure=error;evidence.passed=false;evidence.cleanupFailure=error.message;}
  await writeFile(join(outputDir,'result.json'),JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify({outputDir,passed:evidence.passed,checks:evidence.checks,
   notCovered:evidence.notCovered,provenance}));
 }
 if(failure)throw failure;if(cleanupFailure)throw cleanupFailure;
}

if(process.argv.includes('--source-check'))sourceCheck();else await run();