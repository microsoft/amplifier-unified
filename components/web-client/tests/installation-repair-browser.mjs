import './composer-test-helpers.mjs';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// Client-only HTTP/state fixture. No actual installer, service, provider or host
// repair is run. Reload + explicit reconnect notification test read-only recovery;
// actual transport/service restart qualification belongs to Unified integration.
const preview={supported:true,previewId:'a'.repeat(32),hostInstanceId:'fixture-host',hostLabel:'Isolated fixture host',dataDir:'/fixture/private-data',scope:'application-installation',target:{version:'1.2.3',revision:'c'.repeat(40),source:'stable'},installedExtras:['browser','voice'],preserves:['conversations','settings','credentials','workspaces','source-selections','downloaded-sources','operation-receipts','optional-features'],replaces:['uv-tool-environment','application-launch-pointer'],restart:'managed-service',expiresAt:9999999999};
const operation={operationId:'b'.repeat(32),previewId:preview.previewId,hostInstanceId:preview.hostInstanceId,status:'accepted',stage:'accepted',reinstalled:false,serviceState:'not-checked',healthVerified:false,detail:'Synthetic repair accepted, not complete.',updatedAt:1};
const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',title:'Repair fixture',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},maintenance:{installationRepair:{preview:null,operation:null}}};
const shell=createShellHttpFixture(()=>state),calls=[],pageErrors=[],routeErrors=[],checkpoints=[],screenshots=[];
const evidence=process.env.AMPLIFIER_TEST_ARTIFACTS;
let vite,browser,page,passed=false,failure=null,phase='setup',heldConfirmation,heldVerification,heldReconciliation;
const confirms=()=>calls.filter(call=>call.action==='maintenance.installationRepairConfirm');
const verifications=()=>calls.filter(call=>call.action==='maintenance.installationRepairVerify');
const verifyTarget={operationId:operation.operationId,hostInstanceId:'fixture-successor',confirm:true};
const reconciliationTarget={operationId:operation.operationId,hostInstanceId:'fixture-reconcile-host',confirm:true};
const reconciliations=()=>calls.filter(call=>call.action==='maintenance.installationRepairReconcile');
async function publish(){state.revision++;await page.evaluate(value=>window.emitState(value),shell.state());await page.waitForFunction(revision=>window.amplifier.getState().revision>=revision,state.revision)}
async function capture(name){if(evidence){await mkdir(evidence,{recursive:true});await page.screenshot({path:evidence+'/'+name+'.png',animations:'disabled'});screenshots.push(name+'.png')}}
async function checkpoint(name){assert.equal(confirms().length,1,name);checkpoints.push({name,confirmationPosts:confirms().length});}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),cacheDir:fileURLToPath(new URL('../output/.vite-installation-repair-fixture',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 page=await browser.newPage({viewport:{width:1280,height:1000}});page.on('pageerror',error=>pageErrors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  try{
   const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
   if(response!==undefined)return await route.fulfill({json:response});
   if(path==='/api/state')return await route.fulfill({json:shell.state()});
   if(path==='/api/actions'&&request.method()==='GET')return await route.fulfill({json:[]});
   if(path!=='/api/actions')return await route.fulfill({json:{ok:true}});
   const body=request.postDataJSON();calls.push(body);let result;
   assert.ok(!['maintenance.repair','maintenance.reset','updates.install','updates.forceRestart'].includes(body.action),'No fallback or unrelated mutation');
   if(body.action==='view.update')state.view={...state.view,...body.args.patch};
   if(body.action==='maintenance.installationRepairPreview')result=state.maintenance.installationRepair.preview=structuredClone(preview);
   if(body.action==='maintenance.installationRepairStatus'){
    const current=state.maintenance.installationRepair.operation;
    result={operation:body.args.previewId&&body.args.previewId!==current?.previewId?null:current};
   }
   if(body.action==='maintenance.installationRepairConfirm'){
    assert.deepEqual(body.args,{previewId:preview.previewId,hostInstanceId:preview.hostInstanceId,confirm:true});
    assert.equal(confirms().length,1,'Only one explicit confirmation request is allowed');
    heldConfirmation=route;return;
   }
   if(body.action==='maintenance.installationRepairVerify'){
    assert.deepEqual(body.args,verifyTarget,'Use advertised current successor, never original repair host');
    assert.equal(state.maintenance.installationRepair.operation.status,'reinstalled');
    if(verifications().length===2){heldVerification=route;return}
    result=state.maintenance.installationRepair.operation={...state.maintenance.installationRepair.operation,
     ...(verifications().length===3?{status:'ready',healthVerified:true,detail:'Synthetic verified healthy successor.'}:{detail:'Synthetic health probe did not pass; installation remains unverified.'}),updatedAt:verifications().length+2};
    if(result.status==='ready')state.maintenance.installationRepair.verifyTarget=null;
   }
   if(body.action==='maintenance.installationRepairReconcile'){
    assert.deepEqual(body.args,reconciliationTarget,'Reconciliation binds the current host target exactly');
    assert.equal(reconciliations().length,1,'No automatic reconciliation replay');
    heldReconciliation=route;return;
   }
   state.revision++;await route.fulfill({json:{accepted:true,result,state:shell.state()}});
  }catch(error){routeErrors.push(String(error.stack||error));await route.abort('failed')}
 });
 await page.goto(vite.resolvedUrls.local[0]);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await openSettingsPage(page,'repair',{activate:(_label,control,options)=>control.click(options)});
 const repair=page.locator('[data-part="installation-repair"]');
 await expect(repair).toBeVisible();assert.equal(confirms().length,0);
 phase='preview';await repair.getByRole('button',{name:'Preview installation repair',exact:true}).click();
 await expect(repair.getByText('Isolated fixture host',{exact:false})).toBeVisible();
 await expect(repair).toContainText('Workspace files');await expect(repair).toContainText('Credentials');await expect(repair).toContainText('Downloaded source caches');await expect(repair).toContainText('browser, voice');
 await expect(repair.getByRole('button',{name:'Confirm installation repair',exact:true})).toBeDisabled();
 await repair.locator('[data-part="installation-repair-preview"]').scrollIntoViewIfNeeded();await capture('installation-repair-preview-desktop');
 await page.setViewportSize({width:390,height:844});await repair.getByRole('checkbox').scrollIntoViewIfNeeded();await capture('installation-repair-preview-mobile');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile repair preview must not overflow');
 await repair.getByRole('checkbox').check();phase='confirm lost acknowledgement';
 await repair.getByRole('button',{name:'Confirm installation repair',exact:true}).click();
 await expect.poll(()=>confirms().length).toBe(1);
 assert.ok(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.installation-repair.v1')).previewId), 'Reference persisted before lost acknowledgement');
 await heldConfirmation.abort('failed');await expect(repair.getByRole('alert')).toContainText('outcome is unknown');
 await expect(repair.getByRole('button',{name:'Preview installation repair',exact:true})).toBeDisabled();
 await repair.locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('installation-repair-unknown-mobile');await checkpoint('unknown confirmation');
 phase='reload unknown';await page.reload();await expect(repair).toBeVisible();
 await expect(repair.getByRole('button',{name:'Preview installation repair',exact:true})).toBeDisabled();
 await expect.poll(()=>calls.filter(call=>call.action==='maintenance.installationRepairStatus').at(-1)?.args).toEqual({previewId:preview.previewId});
 await checkpoint('reload reads exact preview identity without confirm');
 phase='canonical receipt';state.maintenance.installationRepair.operation=structuredClone(operation);await publish();
 await expect(repair).toContainText('Repair accepted');await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toHaveCount(0);
 // Exercise reconnect notification selection, not a simulated network handshake.
 const before=calls.filter(call=>call.action==='maintenance.installationRepairStatus').length;
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await expect.poll(()=>calls.filter(call=>call.action==='maintenance.installationRepairStatus').length).toBeGreaterThan(before);
 await checkpoint('reconnect notification only reads status');
 phase='reinstalled not healthy';state.maintenance.installationRepair.operation={...operation,status:'reinstalled',reinstalled:true,serviceState:'running',updatedAt:2};state.maintenance.installationRepair.verifyTarget=verifyTarget;await publish();
 await expect(repair).toContainText('service health not verified');await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toHaveCount(0);
 await repair.locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('installation-repair-reinstalled-not-healthy');
 const verify=()=>repair.getByRole('button',{name:'Verify repaired installation',exact:true});
 await expect(verify()).toBeEnabled();assert.equal(verifications().length,0);
 await expect(repair).toContainText('does not install packages, stop or restart the service');
 phase='known failed verification';await verify().click();await expect.poll(()=>verifications().length).toBe(1);
 await expect(repair).toContainText('Health verification did not establish a healthy installation');
 await expect(verify()).toBeEnabled();await capture('installation-repair-verification-failed');
 phase='verification lost acknowledgement';await verify().click();await expect.poll(()=>verifications().length).toBe(2);await heldVerification.abort('failed');
 await expect(repair).toContainText('Verification outcome is unknown');await expect(verify()).toBeDisabled();
 await capture('installation-repair-verification-unknown');await page.reload();await expect(repair).toBeVisible();await expect(verify()).toBeDisabled();
 assert.equal(verifications().length,2,'reload never repeats health verification');
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await expect(verify()).toBeDisabled();assert.equal(verifications().length,2,'reconnect notification never repeats verification');
 await repair.getByRole('button',{name:'Check verification status',exact:true}).click();
 await expect(repair).toContainText('still unverified');await expect(verify()).toBeEnabled();assert.equal(verifications().length,2);
 phase='deliberate new health check';await verify().click();await expect.poll(()=>verifications().length).toBe(3);
 await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toBeVisible();
 await repair.locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('installation-repair-verified-mobile');await checkpoint('only verified ready receipt reports healthy');
 // Separate synthetic input row, not a real host state transition from healthy
 // back to unknown. Reload discards HTTP-local results; counters remain evidence.
 phase='unknown repair reconciliation';
 state.maintenance.installationRepair={preview:null,operation:{...operation,status:'unknown',reinstalled:false,healthVerified:false,stage:'preflight-unconfirmed',updatedAt:10},verifyTarget:null,reconcileTarget:reconciliationTarget};
 await page.evaluate(()=>{sessionStorage.removeItem('amplifier.installation-repair.v1');sessionStorage.removeItem('amplifier.installation-repair-verification.v1')});
 state.revision++;await page.reload();await expect(repair).toBeVisible();
 const reconcile=()=>repair.getByRole('button',{name:'Reconcile unfinished repair',exact:true});
 await expect(reconcile()).toBeEnabled();assert.equal(reconciliations().length,0,'advertised target is not a gesture');
 await expect(repair).toContainText('Eligibility is not proof');
 await reconcile().click();await expect.poll(()=>reconciliations().length).toBe(1);
 assert.ok(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.installation-repair-reconciliation.v1')).operationId));
 await heldReconciliation.abort('failed');await expect(repair).toContainText('Reconciliation outcome is unknown');
 await expect(reconcile()).toBeDisabled();await capture('installation-repair-reconciliation-unknown');
 await page.reload();await expect(repair).toBeVisible();await expect(reconcile()).toBeDisabled();
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await repair.getByRole('button',{name:'Check reconciliation status',exact:true}).click();
 await expect(repair).toContainText('repair remains held');await expect(reconcile()).toBeDisabled();
 assert.equal(reconciliations().length,1);assert.equal(confirms().length,1);assert.equal(verifications().length,3);
 state.maintenance.installationRepair.operation={...operation,status:'failed',reinstalled:false,healthVerified:false,stage:'interrupted',updatedAt:11};state.maintenance.installationRepair.reconcileTarget=null;await publish();
 await expect(repair.getByRole('button',{name:'Preview installation repair',exact:true})).toBeDisabled();
 await expect(repair.getByText('Repair closed without installation replacement',{exact:true})).toHaveCount(0);
 state.maintenance.installationRepair.operation={...operation,status:'failed',stage:'reconciled-no-effect',reinstalled:false,healthVerified:false,serviceState:'running',updatedAt:12,reconciliation:{noReplacement:true,verifiedAt:123,hostInstanceId:'wrong-host'}};await publish();
 await expect(repair.getByRole('button',{name:'Preview installation repair',exact:true})).toBeDisabled();
 await expect(repair.getByText('Repair closed without installation replacement',{exact:true})).toHaveCount(0);
 // Only an exact backend proof receipt releases this original pending request.
 state.maintenance.installationRepair.operation={...operation,status:'failed',stage:'reconciled-no-effect',reinstalled:false,healthVerified:false,serviceState:'running',updatedAt:13,reconciliation:{noReplacement:true,verifiedAt:123,hostInstanceId:reconciliationTarget.hostInstanceId}};
 state.maintenance.installationRepair.reconcileTarget=null;await publish();
 await expect(repair).toContainText('Repair closed without installation replacement');
 await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toHaveCount(0);
 await expect(repair.getByRole('button',{name:'Check reconciliation status',exact:true})).toHaveCount(0);
 assert.equal(reconciliations().length,1);await capture('installation-repair-reconciled-no-replacement');
 assert.deepEqual(pageErrors,[]);assert.deepEqual(routeErrors,[]);
 passed=true;phase='complete';console.log('PASS installation repair synthetic client: confirm once, exact-successor Verify, no-effect Reconcile explicit once, unknown ack/reload/reconnect/status without replay, positive settlement distinct from healthy ready. No actual install/service restart exercised.');
}catch(error){failure=String(error.stack||error);console.error(failure);process.exitCode=1;try{await capture('installation-repair-failure')}catch{}}
finally{
 if(evidence){await mkdir(evidence,{recursive:true});await writeFile(evidence+'/receipt.json',JSON.stringify({passed,phase,failure,scope:'synthetic client HTTP/state; no actual install, provider, host or service restart',transport:'reload and explicit reconnect notification; actual transport reconnect not covered',checkpoints,screenshots,pageErrors,routeErrors,confirmationPosts:confirms().length,healthVerificationPosts:verifications().length,reconciliationPosts:reconciliations().length,calls},null,2)+'\n')}
 await browser?.close();await vite?.close();
}
