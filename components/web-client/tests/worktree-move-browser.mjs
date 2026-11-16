import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// Client-only mounted component and synthetic state/actions. No actual host,
// writer release, continuation, provider, checkout mutation or native recovery.
const root=fileURLToPath(new URL('../',import.meta.url));
const deferred=process.argv.includes('--deferred');
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/'+(deferred?'deferred-placement-browser':'worktree-move-browser');
const fixturePath='tests/fixtures/worktree-move.jsx';
const row={id:'original-move',revision:1,phase:'pending',source:'/fixture/home',target:'/fixture/checkout',displayState:'waiting',waitingFor:['generation'],detail:'Waiting for the current turn to finish.'};
const session={id:'chat',workspace:'/fixture/home',workingDirectory:'/fixture/home',configurationBusy:true,executionRevision:1,worktrees:[{id:'checkout',path:row.target,source:row.source,status:'ready',owned:true,revision:1}],worktreeHandoffs:[row]};
if(deferred){session.worktreeHandoffs=[];session.configurationBusy=false;session.status='working';session.workers=[{id:'idle-child',status:'idle'}]}
const report={scope:'synthetic mounted client component and HTTP responses; not actual host/native recovery or bundled build acceptance',checks:[],calls:[],errors:[]};
let vite,browser,page,held,mode='hold';
const card=()=>page.locator('[data-handoff-id="original-move"]');
const check=()=>card().getByRole('button',{name:'Check move',exact:true});
async function publish(patch,sessionPatch={}){
 Object.assign(row,patch);Object.assign(session,sessionPatch);session.worktreeHandoffs=[{...row}];
 await page.evaluate(value=>window.publishMoveFixture(value),session);
}
async function capture(name){await page.screenshot({path:output+'/'+name+'.png',animations:'disabled'})}
function checkpoint(name){report.checks.push({name,checkCalls:report.calls.length})}
async function runDeferred(){
 const choose=()=>page.getByRole('button',{name:'Use this folder next',exact:true});
 const current=()=>page.locator('[data-part="execution-directory"]'),next=()=>page.locator('[data-part="next-execution-directory"]');
 const intent={...row,placementMode:'deferred-retirement',createdAt:10,step:'desired',detail:'Existing workers can finish in the current folder.'};
 const project=async(rows,patch={})=>{
  Object.assign(session,patch,{worktreeHandoffs:rows});
  await page.evaluate(value=>window.publishMoveFixture(value),session);
 };
 await expect(current()).toHaveText(row.source);await expect(next()).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Check move',exact:true})).toHaveCount(0);
 assert.equal(report.calls.length,0);checkpoint('current canonical folder, no pending choice or Check');
 await choose().focus();await page.keyboard.press('Enter');
 await expect.poll(()=>report.calls.length).toBe(1);await expect(page.getByRole('status').filter({hasText:'Saving folder choice…'})).toBeVisible();
 await choose().dispatchEvent('click');assert.equal(report.calls.length,1);
 await held.fulfill({json:{accepted:true,result:intent,effects:[]}});mode='ok';
 await expect(choose()).toBeEnabled();await expect(current()).toHaveText(row.source);await expect(next()).toHaveCount(0);
 await expect(page.getByRole('status').filter({hasText:'Folder choice received.'})).toBeVisible();
 checkpoint('one keyboard choice, accepted response alone is not canonical placement');
 await project([intent]);await expect(next()).toHaveText(row.target);await expect(current()).toHaveText(row.source);
 await expect(page.getByText('Folder choice received.',{exact:false})).toHaveCount(0);
 await expect(page.getByText('Folder change is waiting for existing work to settle.',{exact:false})).toBeVisible();
 await expect(choose()).toBeDisabled();await expect(card().getByRole('button')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Use home folder next',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Archive and preserve files',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Remove clean checkout',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Check move',exact:true})).toHaveCount(0);
 assert.doesNotMatch(await page.locator('[data-part="worktrees"]').innerText(),/next input|next message|Task resumed|Task continued|original-move/);
 await capture('current-next-pending');checkpoint('soft choice does not freeze work or claim next input target');
 await page.reload();await expect(next()).toHaveText(row.target);await expect(current()).toHaveText(row.source);assert.equal(report.calls.length,1);
 checkpoint('reload reads current and desired from canonical projection without actions');
 const old={...intent,revision:2,phase:'rejected',displayState:'not_moved',supersededBy:'new-choice'};
 const newer={...intent,id:'new-choice',createdAt:20,target:'/fixture/new-choice'};
 await project([old,newer]);await expect(next()).toHaveText(newer.target);
 await expect(card()).toContainText('Not applied — a newer folder was chosen');
 // Reordered old rows cannot replace the latest canonical desired target.
 await project([newer,old]);await expect(next()).toHaveText(newer.target);await expect(current()).toHaveText(row.source);
 checkpoint('new desired target supersedes stale receipt without a local move');
 await expect(choose()).toBeEnabled();mode='hold';await choose().focus();await page.keyboard.press('Enter');
 await expect.poll(()=>report.calls.length).toBe(2);
 await project([{...newer,revision:2},old]);await held.abort('failed');mode='ok';
 await expect(choose()).toBeEnabled();await expect(page.locator('[data-part="worktrees"]').getByRole('alert')).toHaveCount(0);
 await expect(page.getByText('Folder choice received.',{exact:false})).toHaveCount(0);
 await expect(next()).toHaveText(newer.target);checkpoint('late HTTP failure after newer projection cannot overwrite canonical status');
 await project([{...newer,step:'retiring',displayState:'moving'}],{configurationBusy:true});
 await expect(choose()).toBeDisabled();await expect(current()).toHaveText(row.source);await expect(next()).toHaveText(newer.target);
 await project([{...newer,step:'committing',displayState:'moving'}]);await expect(current()).toHaveText(row.source);
 await project([{...newer,revision:2,step:'applied',phase:'applied',displayState:'moved',continuation:{status:'waiting',detail:'Continuation has not run.'}}],{workingDirectory:newer.target,configurationBusy:false});
 await expect(current()).toHaveText(newer.target);await expect(next()).toHaveCount(0);
 await expect(page.locator('[data-handoff-id="new-choice"]')).toContainText('Folder changed');
 await expect(page.locator('[data-handoff-id="new-choice"]')).toContainText('Waiting to continue');
 checkpoint('actual switch gates briefly and placement remains distinct from continuation');
 // Separate synthetic failure input; not a real transition back from moved.
 const failure={...intent,phase:'unknown',displayState:'needs_recovery',detail:'Saved placement is uncertain. Review host recovery details.'};
 await project([failure],{workingDirectory:row.source,configurationBusy:true});
 await expect(card().getByRole('alert')).toHaveText('Folder change needs recovery');
 await expect(card().getByRole('button')).toHaveCount(0);await expect(next()).toHaveCount(0);await expect(current()).toHaveText(row.source);
 const legacy={...row,id:'legacy-unknown',phase:'unknown',displayState:'needs_recovery',detail:'Original release remains unconfirmed.'};
 await project([failure,legacy]);
 const legacyCheck=page.locator('[data-handoff-id="legacy-unknown"]').getByRole('button',{name:'Check move',exact:true});
 await expect(legacyCheck).toBeEnabled();mode='error';
 await legacyCheck.focus();await page.keyboard.press('Space');
 await expect(page.locator('[data-handoff-id="legacy-unknown"]').getByRole('alert')).toHaveText('Synthetic check unavailable. Saved move unchanged.');
 await expect(legacyCheck).toBeFocused();await expect(legacyCheck).toHaveAttribute('aria-disabled','false');
 checkpoint('new uncertain placement stays honest and only legacy Check remains keyboard-accessible');
 await page.setViewportSize({width:390,height:844});await current().scrollIntoViewIfNeeded();await capture('deferred-mobile-legacy');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No mobile horizontal overflow');
 assert.deepEqual(report.calls.map(call=>call.action),['worktree.handoff','worktree.handoff','worktree.check']);
}
if(process.argv.includes('--preflight')){
 await transformWithEsbuild(await readFile(root+fixturePath,'utf8'),fixturePath,{loader:'jsx',jsx:'automatic'});
 const registry=JSON.parse(await readFile(root+'tests/browser-checks.json','utf8'));
 assert.equal(registry.scenarios.filter(item=>item.id==='worktree-move').length,1);
  assert.equal(registry.scenarios.filter(item=>item.id==='deferred-placement').length,1);
 console.log('PASS worktree move fixture JSX + registry preflight; browser not run.');
}else{
 await mkdir(output,{recursive:true});
 try{
  vite=await createServer({configFile:false,root,cacheDir:root+'output/.vite-worktree-move',server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']},
   plugins:[{name:'worktree-move-fixture',configureServer(server){server.middlewares.use((req,res,next)=>{
    if(req.url!=='/worktree-move-fixture')return next();
    res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><main id="amp-one"><div id="fixture"></div></main><script type="module" src="/'+fixturePath+'"></script>');
   })}}]});
  await vite.listen();
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--single-process','--no-zygote','--no-sandbox']}: {})});
  page=await browser.newPage({viewport:{width:1100,height:1000}});page.on('pageerror',error=>report.errors.push(error.message));
  await page.route('**/fixture-state',route=>route.fulfill({json:session}));
  await page.route('**/api/**',async route=>{
   const request=route.request();
   assert.equal(new URL(request.url()).pathname,'/api/actions');
   const body=request.postDataJSON();report.calls.push(body);
   if(deferred){
    assert.deepEqual(body,report.calls.length<=2?{action:'worktree.handoff',args:{sessionId:'chat',id:'checkout',expectedExecutionRevision:1},options:{propagateError:true}}:{action:'worktree.check',args:{sessionId:'chat',id:'legacy-unknown'},options:{propagateError:true}},'Only explicit folder choice and legacy Check, no reconcile/Continue/Stop/native calls');
   }else assert.deepEqual(body,{action:'worktree.check',args:{sessionId:'chat',id:row.id},options:{propagateError:true}},'Only the same receipt may be observed; no handoff/reconcile/Continue/Stop/native calls');
   if(mode==='hold'){held=route;return}
   if(mode==='error')return route.fulfill({json:{accepted:false,error:'Synthetic check unavailable. Saved move unchanged.'}});
   await route.fulfill({json:{accepted:true,result:{...row},effects:[]}});
  });
  await page.goto(vite.resolvedUrls.local[0]+'worktree-move-fixture');
  if(deferred)await runDeferred();
  else{
  await expect(card().locator('[data-part="move-status"]')).toHaveText('Waiting for this turn');
  await expect(page.locator('[data-part="execution-directory"]')).toHaveText(row.source);
  await expect(card()).toContainText(row.source);await expect(card()).toContainText(row.target);
  await expect(card().getByRole('button')).toHaveCount(1);
  await expect(card().getByRole('textbox')).toHaveCount(0);assert.equal(report.calls.length,0);
  checkpoint('one original receipt, source retained, no action on mount');
  await check().focus();await page.keyboard.press('Enter');
  await expect.poll(()=>report.calls.length).toBe(1);await expect(check()).toHaveAttribute('aria-disabled','true');await expect(check()).toBeFocused();
  await expect(card()).toContainText('Checking move…');
  await page.keyboard.press('Enter');await page.keyboard.press('Space');
  assert.equal(report.calls.length,1,'Busy keyboard repeats do not dispatch');
  await publish({revision:2,displayState:'moving',waitingFor:[]});
  await expect(card().locator('[data-part="move-status"]')).toHaveText('Moving');await expect(check()).toBeFocused();
  await held.fulfill({json:{accepted:true,result:{...row},effects:[]}});mode='ok';
  await expect(check()).toHaveAttribute('aria-disabled','false');await expect(check()).toBeFocused();
  checkpoint('keyboard, in-flight duplicate guard and delayed same-row update');
  await publish({revision:3,phase:'applied',displayState:'moved',continuation:{status:'waiting',detail:'The task has not continued yet.'}},{workingDirectory:row.target,configurationBusy:false});
  await expect(card().locator('[data-part="move-status"]')).toHaveText('Moved');await expect(card()).toContainText('Waiting to continue');
  await expect(page.locator('[data-part="execution-directory"]')).toHaveText(row.target);
  await expect(card()).toHaveCount(1);await capture('moved-continuation-pending');checkpoint('moved distinct from continuation');
  await publish({revision:4,phase:'unknown',displayState:'needs_recovery',detail:'Writer release is not confirmed.',continuation:{status:'suppressed',detail:'No work was sent.'}},{workingDirectory:row.source,configurationBusy:true});
  await page.reload();await expect(card().locator('[data-part="move-status"]')).toHaveText('Needs recovery');
  assert.equal(report.calls.length,1,'Reload only reads canonical state');
  await expect(page.locator('[data-part="execution-directory"]')).toHaveText(row.source);
  await expect(card().getByRole('button')).toHaveCount(1);
  for(let n=0;n<2;n++){await check().focus();await page.keyboard.press('Enter');await expect.poll(()=>report.calls.length).toBe(2+n);await expect(check()).toHaveAttribute('aria-disabled','false')}
  checkpoint('needs recovery reload and repeated checks never replay move or work');
  mode='error';await check().focus();await page.keyboard.press('Space');await expect(card().getByRole('alert')).toHaveText('Synthetic check unavailable. Saved move unchanged.');
  await expect(check()).toBeFocused();await expect(card().locator('[data-part="move-status"]')).toHaveText('Needs recovery');
  await capture('needs-recovery-check-error');checkpoint('keyboard error announced, focus and confirmed source preserved');
  await publish({revision:5,phase:'rejected',displayState:'not_moved',continuation:undefined},{configurationBusy:true});
  const move=page.getByRole('button',{name:'Use this folder next',exact:true});
  await expect(move).toBeDisabled();await publish({revision:6},{configurationBusy:false});await expect(move).toBeEnabled();
  await expect(page.locator('[data-part="execution-directory"]')).toHaveText(row.source);checkpoint('not moved leaves source and obeys backend retry fence');
  await page.setViewportSize({width:390,height:844});await card().scrollIntoViewIfNeeded();await capture('move-mobile');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Card has no mobile horizontal overflow');
  assert.equal(report.calls.length,4);
  }
  assert.deepEqual(report.errors,[]);report.status='PASS';
 }catch(error){report.status='FAIL';report.failure=error.stack||String(error);throw error}
 finally{await writeFile(output+'/result.json',JSON.stringify(report,null,2)+'\n');await browser?.close();await vite?.close()}
 console.log(JSON.stringify(report));
}