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
let vite,browser,page,passed=false,failure=null,phase='setup',heldConfirmation;
const confirms=()=>calls.filter(call=>call.action==='maintenance.installationRepairConfirm');
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
 phase='reinstalled not healthy';state.maintenance.installationRepair.operation={...operation,status:'reinstalled',reinstalled:true,serviceState:'running',updatedAt:2};await publish();
 await expect(repair).toContainText('service health not verified');await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toHaveCount(0);
 await repair.locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('installation-repair-reinstalled-not-healthy');
 phase='verified receipt';state.maintenance.installationRepair.operation={...operation,status:'ready',reinstalled:true,serviceState:'running',healthVerified:true,updatedAt:3};await publish();
 await expect(repair.getByText('Installation repair verified healthy',{exact:true})).toBeVisible();
 await repair.locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('installation-repair-verified-mobile');await checkpoint('only verified ready receipt reports healthy');
 assert.deepEqual(pageErrors,[]);assert.deepEqual(routeErrors,[]);
 passed=true;phase='complete';console.log('PASS installation repair synthetic client: preview scope/extras, mobile, explicit confirm once, lost ack/reload/reconnect notification read-only recovery, verified health gate. No actual install/service restart exercised.');
}catch(error){failure=String(error.stack||error);console.error(failure);process.exitCode=1;try{await capture('installation-repair-failure')}catch{}}
finally{
 if(evidence){await mkdir(evidence,{recursive:true});await writeFile(evidence+'/receipt.json',JSON.stringify({passed,phase,failure,scope:'synthetic client HTTP/state; no actual install, provider, host or service restart',transport:'reload and explicit reconnect notification; actual transport reconnect not covered',checkpoints,screenshots,pageErrors,routeErrors,confirmationPosts:confirms().length,calls},null,2)+'\n')}
 await browser?.close();await vite?.close();
}
