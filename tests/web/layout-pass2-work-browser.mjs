// Installed host/assets, explicit synthetic populations, presentation evidence only.
// Stage with scripts/web_test_harness.py; supply AMPLIFIER_TEST_PYTHON,
// AMPLIFIER_TEST_PACKAGE_ROOT and AMPLIFIER_TEST_WHEEL[_IDENTITY]. No Vite/build,
// provider, production history or live peer is used. This file does not launch a DTU.
// A "visited" checkpoint means selectors/scene assertions and evidence completed,
// NOT visual acceptance. Geometry is raw observation, not an invented CSS oracle.
// Focused repair: LAYOUT_PASS2_PHASES=detail,workspace and
// LAYOUT_PASS2_CHECKPOINTS=W03.tool-bodies.*,C02.details.reference.390 is NOT a regex:
// only a final "*" is supported. Use exact IDs or prefixes ending "*".
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,access} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation,openChatAdvancedPage} from './browser-chat-pages.mjs';
import {updateAppearance} from './settings-fixture.mjs';
import {fenceLayoutServiceWorkers} from './layout-pass2-browser-context.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||process.env.AMPLIFIER_TEST_OUTPUT_DIR;
assert.ok(output,'Supply a new owned AMPLIFIER_TEST_ARTIFACTS directory');
assert.ok(process.env.AMPLIFIER_TEST_PYTHON&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT,
 'Installed Python/package paths must be supplied; no checkout fallback');
assert.ok(process.env.AMPLIFIER_TEST_WHEEL||process.env.AMPLIFIER_TEST_WHEEL_IDENTITY,
 'Supply original wheel or retained full wheel identity manifest');
const out=resolve(output),ledgerPath=join(out,'ledger.json');
await mkdir(out,{recursive:true});
try{await access(ledgerPath);throw Error('Refusing to overwrite an earlier pass-two ledger')}catch(error){
 if(error.code!=='ENOENT')throw error;
}
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const slug=value=>value.replace(/[^A-Za-z0-9_.-]+/g,'-');
const errorRecord=error=>({name:error?.name||'Error',message:String(error?.message||error),
 stack:String(error?.stack||'').slice(0,6000)});
const report={schema:'layout-pass2-interactive-v1',status:'running',startedAt:new Date().toISOString(),
 target:{unified:'e7bc1845c63731cf42dc91f3761861aee7326c42',
  selectedClient:'ad9d5dfedddf12ea6e99910717280cc37a56da76',
  binding:'authoring target only; installed identity and declared build source recorded separately',
  suppliedSourceRevision:process.env.AMPLIFIER_TEST_SOURCE_REVISION||null},
 sourceFiles:{},phases:[],checkpoints:[],safety:{blocked:[],admitted:[],admittedOmitted:0,outbound:[],syntheticReads:[]},
 limits:[
  'Synthetic hierarchy is labelled test input through existing fixture patch, not production ownership/capture proof.',
  'Work currently presents a flat interleaved stream; raw actor/owner/indentation evidence is retained without a recursive-indent oracle.',
  'Text-fragment bottoms are baseline proxies, not measured typographic baselines.',
  'Screenshots require independent pixel review. No source defect or spacing pass follows from capture alone.',
  'Global Settings and its ledger/environment remain parent-owned.',
 ]};
for(const path of ['tests/web/layout-pass2-work-browser.mjs','tests/fixtures/layout_pass2_work_server.py',
 'tests/web/fixture-lifecycle.mjs','tests/web/browser-chat-pages.mjs','tests/web/settings-fixture.mjs',
 'tests/web/layout-pass2-browser-context.mjs']){
 report.sourceFiles[path]=sha(await readFile(join(root,path)));
}
const pendingReason='Not reached; dependency/setup may fail. No execution claimed.';
function plan(id,phase,source,reason=pendingReason){
 const row={id,phase,status:'unvisited',reason,source};report.checkpoints.push(row);return row;
}
const looks=[{id:'reference',label:'Reference'},{id:'builtin:default',label:'builtin-default-candidate'}];
const screens=[1440,390],hierarchyWidths=[1440,768,390,320];
for(const phase of ['detail','questions','approvals','workspace','workspace-config','navigation'])
 plan(`${phase}.provenance`,phase,['tests/fixtures/layout_pass2_work_server.py','scripts/web_test_harness.py']);
plan('D.hierarchy.seed','detail',['tests/web/delegate-work-stream-browser.mjs:440-480',
 'components/web-client/tests/timeline.test.mjs:174-200']);
for(const look of looks)for(const width of hierarchyWidths)
 plan(`W10.hierarchy.${slug(look.id)}.${width}`,'detail',['components/web-client/src/timeline.jsx:56-84',
  'components/web-client/src/work-stream.js:64-89']);
// High-yield gap completion, not another full Cartesian route census.
for(const [look,width] of [[looks[0],1440],[looks[1],390]]){
 plan(`W10.top-visible.${slug(look.id)}.${width}`,'detail',[
  'components/web-client/src/timeline.jsx:56-84','tests/web/delegate-work-stream-browser.mjs']);
 plan(`H06.diagnostics-manual.${slug(look.id)}.${width}`,'detail',[
  'tests/web/support-diagnostics-browser.mjs:27-35','components/web-client/src/conversation-controls.jsx:73-123']);
}
for(const id of ['P00.composer','W03.tool-bodies','W04.model-recordings','W11.plan','H04.error-diagnostics',
 'H05.help-report','C02.details','C09.preview-cancel','C10.writing-edit-preview']){
 for(const look of looks)for(const width of screens)plan(`${id}.${slug(look.id)}.${width}`,'detail',[
  'tests/fixtures/browser_detail_server.py','components/web-client/src/chat-pages.jsx',
  'components/web-client/src/canvas-workspace.jsx','components/web-client/src/inline-artifacts.jsx']);
}
const advanced=[
 ['Memory & past work','chat-memory',[]],
 ['Access & approvals','chat-write-files',['File restrictions when asking for permission']],
 ['Project file access','chat-files',[]],
 ['Work & automation','chat-work',[]],
 ['Model & tools','chat-models',['Conversation bundle','Conversation modules','Save & share a bundle']],
 ['Context & recovery','chat-context',['Recovery & recorded errors']],
 ['Computer access','chat-computer',[]],
];
for(const [title,id] of advanced)for(const width of screens)
 plan(`P03.${id}.${width}`,'detail',['components/web-client/src/chat-pages.jsx:103-110',
  'tests/web/browser-chat-pages.mjs:18-35']);
for(const look of looks)for(const width of screens){
 for(const id of ['W12.question-alternative-context','W13.question-original','C08.question-artifacts'])
  plan(`${id}.${slug(look.id)}.${width}`,'questions',['tests/fixtures/questions_ui_server.py:64-141',
   'components/web-client/src/questions.jsx:95-131']);
 plan(`W14.approval-review-cancel.${slug(look.id)}.${width}`,'approvals',[
  'tests/web/approval-layout-browser.mjs','components/web-client/src/approval-card.jsx:129-153']);
}
for(const width of screens){
 plan(`P08.workspace-review-bodies.${width}`,'workspace',['tests/web/ordinary-workspace-work-browser.mjs:36-44,100-108']);
 plan(`P09.workspace-worker-details.${width}`,'workspace',[
  'components/web-client/src/workspace-review.jsx:84-90','tests/fixtures/ordinary_workspace_work_ui_server.py']);
 plan(`P10.workspace-configuration.${width}`,'workspace-config',[
  'tests/web/standard-ux-workspace-configuration-browser.mjs:22-38',
  'components/web-client/src/routing-settings.jsx','components/web-client/src/registry.jsx']);
 for(const id of ['L01.home','L02.sidebar-status','L03.search','L04.context-menu'])
  plan(`${id}.${width}`,'navigation',['tests/web/home-sidebar-browser.mjs','tests/web/chat-search-browser.mjs']);
}
plan('G.workspace-commissioned-child-bodies','workspace',[
 'tests/fixtures/ordinary_workspace_work_ui_server.py:78-95'],
 'Unvisited by design: fixture member seed requires a commissioned assignment. This presentation journey does not create/commission work.');
plan('G.real-provider-native-mobile','detail',[],
 'Unvisited by design: no provider calls, physical keyboard/native platform or actual user data.');
plan('G.source-file-edit-cancel','detail',['components/web-client/src/writing-block.js:12-14'],
 'Unvisited as a nonexistent generic control: writing copy has Edit copy/Preview copy, not Cancel. C09 covers actual Keep current preview cancellation.');
const phaseNames=['detail','questions','approvals','workspace','workspace-config','navigation'];
const tokens=name=>(process.env[name]||'').split(',').map(value=>value.trim()).filter(Boolean);
const phaseFilter=tokens('LAYOUT_PASS2_PHASES'),checkpointFilter=tokens('LAYOUT_PASS2_CHECKPOINTS');
for(const phase of phaseFilter)assert.ok(phaseNames.includes(phase),'Unknown phase filter '+phase);
const matches=(id,pattern)=>pattern.endsWith('*')?id.startsWith(pattern.slice(0,-1)):id===pattern;
for(const pattern of checkpointFilter){
 assert.ok(!pattern.slice(0,-1).includes('*'),'Only a final checkpoint wildcard is supported');
 assert.ok(report.checkpoints.some(row=>matches(row.id,pattern)),'Unmatched checkpoint filter '+pattern);
}
const selectable=row=>!row.id.startsWith('G.');
const chosen=new Set(report.checkpoints.filter(row=>selectable(row)&&
 (!phaseFilter.length||phaseFilter.includes(row.phase))&&
 (!checkpointFilter.length||checkpointFilter.some(pattern=>matches(row.id,pattern)))).map(row=>row.id));
assert.ok(chosen.size,'Filters selected no runnable checkpoints');
const dependencies=new Set();
for(const phase of phaseNames)if(report.checkpoints.some(row=>row.phase===phase&&chosen.has(row.id)))
 dependencies.add(phase+'.provenance');
if([...chosen].some(id=>id.startsWith('W10.hierarchy.')||id.startsWith('W10.top-visible.')))
 dependencies.add('D.hierarchy.seed');
const selected=new Set([...chosen,...dependencies]);
const wants=prefix=>[...selected].some(id=>id===prefix||id.startsWith(prefix+'.'));
for(const row of report.checkpoints)if(selectable(row)&&!selected.has(row.id))
 row.reason='Filtered out of this invocation; unvisited here, not reused/passed from a prior ledger.';
report.selection={phaseFilter,checkpointFilter,requestedIds:[...chosen],dependencyIds:[...dependencies],
 selectedIds:[...selected],fullDenominator:report.checkpoints.length,
 priorEvidencePolicy:'Earlier receipts remain unchanged; this subset ledger makes no inherited pass claims.'};
let current=null,currentCheckpoint=null,writeTail=Promise.resolve(),evidenceSequence=0;
function save(){
 const snapshot=JSON.stringify(report,null,2)+'\n';
 writeTail=writeTail.then(async()=>{await writeFile(ledgerPath+'.next',snapshot);await rename(ledgerPath+'.next',ledgerPath)});
 return writeTail;
}
await writeFile(ledgerPath,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
async function checkpoint(id,run){
 const row=report.checkpoints.find(row=>row.id===id);assert.ok(row,'Undeclared checkpoint '+id);
 if(!selected.has(id))return false;
 currentCheckpoint=id;row.status='running';delete row.reason;row.startedAt=new Date().toISOString();await save();
 try{row.result=await run();row.status='visited';return true}
 catch(error){
  row.status='failed';row.error=errorRecord(error);
  if(current?.page&&!current.page.isClosed()){
   const file=slug(id)+'-failure.png';
   try{await current.page.screenshot({path:join(out,file),timeout:5000});row.failureScreenshot=file}
   catch(captureError){row.captureError=errorRecord(captureError)}
  }
  return false;
 }finally{row.finishedAt=new Date().toISOString();currentCheckpoint=null;await save()}
}
const unwrap=raw=>{
 let body=raw;
 for(let depth=0;depth<5;depth++){
  if(body?.action==='shell.command'){body=body.args?.command||body.args;continue}
  if(body?.action==='canvas.views.command'){body={action:body.args?.action,args:body.args?.args||{}};continue}
  return body;
 }
 return null;
};
// Default deny on action dispatch, rather than a blacklist with a grant/install
// escape. Fixture controls use APIRequestContext separately and exact helpers below.
const safeActions=new Set([
 'view.update','shell.view.update','shell.report','client.report',
 'theme.list','theme.read','appearance.preferences.read','appearance.preferences.update',
 'appearance.preferences.report','appearance.preferences.select','appearance.client.report',
 'session.select','session.inspect','session.history','session.children',
 'canvas.visibility','canvas.select','canvas.view','canvas.inspect','canvas.versions.inspect',
 'canvas.views.inspect','canvas.views.acquire','canvas.views.release','canvas.views.observe',
 'canvas.views.status','canvas.renderReport','canvas.report',
 'capacity.read','capability.read','coordination.context','coordination.read',
 'coordination.list','coordination.review','workspace.inspect','workspace.list','feedback.diagnostics',
 'workspace.bundleDefault.inspect','workspace.resources.list','access.get','permissions.get',
 'memory.status','recall.status','memory.list','bundles.list',
 'conversation.delivery',
]);
async function guardContext(context,phase){
 const permits=[],syntheticReads=[];
 const permit=(action,args)=>permits.push({action,key:JSON.stringify(args)});
 const seedRead=(action,args,result,label)=>{
  assert.ok(['runtime.provenance','session.inspect'].includes(action));
  const record={action,args,result,label,checkpoint:currentCheckpoint,consumed:false};
  syntheticReads.push(record);report.safety.syntheticReads.push(record);return record;
 };
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(!['http:','https:'].includes(url.protocol))return route.continue();
  if(url.origin!==phase.base){
   report.safety.outbound.push({checkpoint:currentCheckpoint,url:url.origin+url.pathname,method:request.method()});
   await save();return route.abort('blockedbyclient');
  }
  const method=request.method(),path=url.pathname;
  if(['GET','HEAD'].includes(method))return route.continue();
  if(path==='/api/actions'){
   let body;try{body=unwrap(request.postDataJSON())}catch{}
   const action=body?.action,args=body?.args||{};
   const synthetic=syntheticReads.find(row=>!row.consumed&&row.checkpoint===currentCheckpoint&&
    row.action===action&&JSON.stringify(row.args)===JSON.stringify(args));
   if(synthetic){
    synthetic.consumed=true;synthetic.interception='fulfilled before host dispatch; presentation fixture only';
    await save();return route.fulfill({status:200,json:{accepted:true,result:synthetic.result}});
   }
   // A worker provenance inspection is allowed only through its exact declared
   // synthetic read, never by inspecting a real worker in the test host.
   if(action==='runtime.provenance'){
    report.safety.blocked.push({phase:phase.name,checkpoint:currentCheckpoint,action,
     reason:'No exact one-shot synthetic worker observation; real runtime inspection refused before host'});
    await save();return route.fulfill({status:409,json:{
     accepted:false,error:'No declared synthetic worker observation for this checkpoint'}});
   }
   const index=permits.findIndex(row=>row.action===action&&row.key===JSON.stringify(args));
   const artifactId=args.resourceId||args.id;
   const artifactObservation=['canvas.views.inline','canvas.snapshot'].includes(action)&&phase.artifactIds.has(artifactId);
   const configRead=phase.name==='workspace-config'&&
    ['providers.list','routing.list','routing.show','modules.list','sources.list','modules.diagnostics'].includes(action)&&
    args.configurationTarget?.kind==='workspace'&&['local','project'].includes(args.scope);
   let allowed=safeActions.has(action)||index>=0||artifactObservation||configRead;
   // Never let a navigation click silently turn into a reset/executing reload.
   if(action==='canvas.select'&&args.resetPreview)allowed=false;
   if(index>=0)permits.splice(index,1);
   const record={phase:phase.name,checkpoint:currentCheckpoint,action:action||null,
    operation:args.operation||null,decision:args.decision||null};
   if(allowed){
    if(report.safety.admitted.length<1600)report.safety.admitted.push(record);
    else report.safety.admittedOmitted++;
    return route.continue();
   }
   report.safety.blocked.push({...record,reason:'Presentation allowlist refused before host dispatch'});
   await save();
   return route.fulfill({status:409,json:{accepted:false,code:'layout_audit_guard',
    error:'Presentation audit: this operation was not dispatched.'}});
  }
  if(['/api/clients/attach','/api/shell/resources','/api/conversation/work-sync','/api/view'].includes(path))
   return route.continue();
  report.safety.blocked.push({phase:phase.name,checkpoint:currentCheckpoint,path,method,
   reason:'Non-read endpoint refused before host dispatch'});
  await save();return route.fulfill({status:409,json:{accepted:false,error:'Presentation audit endpoint guard'}});
 });
 return {permit,seedRead,unconsumed:()=>[
  ...permits.map(({action})=>action),
  ...syntheticReads.filter(row=>!row.consumed).map(row=>'synthetic:'+row.action),
 ]};
}
async function startPhase(name){
 const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON,[
  '-I','-B','-u',join(root,'tests/fixtures/layout_pass2_work_server.py'),'--phase',name,
 ],{stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONPATH:'',PYTHONHOME:'',PYTHONDONTWRITEBYTECODE:'1',
  AMPLIFIER_TEST_INSTALLED_PACKAGE:process.env.AMPLIFIER_TEST_PACKAGE_ROOT,
  ...(name==='questions'?{AMPLIFIER_QUESTIONS_MODE:'dismiss'}:{})}});
 const stop=ownFixture(fixture,{label:'Layout pass two '+name});
 let stderr='',provenance;
 fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16000)});
 try{
  const ready=await new Promise((resolveReady,reject)=>{
   let pending='',listener;
   const clean=()=>{clearTimeout(timer);fixture.stdout.off('data',listener);fixture.off('exit',exited);fixture.off('error',failed)};
   const failed=error=>{clean();reject(error)};
   const exited=(code,signal)=>failed(Error(`Fixture ${name} exited before ready: ${code??signal}; ${stderr}`));
   listener=chunk=>{
    pending+=chunk;
    for(let end;(end=pending.indexOf('\n'))>=0;){
     const line=pending.slice(0,end);pending=pending.slice(end+1);let value;
     try{value=JSON.parse(line)}catch{continue}
     if(value.fixture==='layout-pass2-provenance'){provenance=value;continue}
     if(value.port||value.url){
      if(!provenance||provenance.pid!==fixture.pid)return failed(Error('Missing owner-bound provenance before readiness'));
      const base=value.url||`http://127.0.0.1:${value.port}`;
      if(!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(base))return failed(Error('Non-loopback fixture readiness'));
      clean();resolveReady({...value,base});return;
     }
    }
    if(pending.length>1000000)failed(Error('Fixture readiness line exceeded finite bound'));
   };
   const timer=setTimeout(()=>failed(Error(`Fixture ${name} readiness timeout; ${stderr}`)),60000);
   fixture.stdout.on('data',listener);fixture.once('exit',exited);fixture.once('error',failed);
  });
  const file=name+'-installed-provenance.json';
  await writeFile(join(out,file),JSON.stringify(provenance,null,2)+'\n');
  const phase={name,base:ready.base,ready,provenance,provenanceFile:file,stop,stderr:()=>stderr,artifactIds:new Set(),
   pageErrors:[],browserAssetReads:[],assetReadsPending:[],assetReadErrors:[]};
  report.phases.push({name,pid:fixture.pid,base:ready.base,provenanceFile:file,status:'running'});
  return phase;
 }catch(error){
  try{await stop()}catch(cleanupError){error.cleanupError=errorRecord(cleanupError)}
  throw error;
 }
}
const button=(scope,name)=>scope.getByRole('button',{name,exact:true});
const state=()=>current.page.evaluate(()=>window.amplifier.getState());
async function action(name,args={}){
 const receipt=await current.page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 assert.equal(receipt?.accepted,true,`${name}: ${receipt?.error||'missing accepted receipt'}`);return receipt.result;
}
async function control(args){
 assert.equal(current.name,'detail');
 assert.ok(['patch','heavy','inspection','inspection-actions','inspection-finish','canonical-work',
  'canonical-append','chat-plan','inspect-work-source'].includes(args.op));
 const response=await current.context.request.post(current.base+'/api/fixture/control',{data:args});
 assert.equal(response.status(),200,await response.text());return response.json();
}
async function fixturePost(path,data={}){
 assert.ok((current.name==='questions'&&path==='/fixture/reviews'));
 const response=await current.context.request.post(current.base+path,{data});
 assert.equal(response.status(),200,await response.text());return response.json();
}
async function readFixture(path){
 assert.ok(['/fixture/check','/fixture/ordinary-work','/fixture/workspace-configuration','/api/fixture/info'].includes(path));
 const response=await current.context.request.get(current.base+path);
 assert.equal(response.status(),200,await response.text());return response.json();
}
async function seedCanvas(args){
 // One exact author-specified synthetic canvas publication; never a broad action
 // permission left open while interacting with a page.
 current.guard.permit('canvas.show',args);const result=await action('canvas.show',args);
 current.artifactIds.add(result.id);return result;
}
async function enter(){
 await current.page.goto(current.base);
 await current.page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await showFixtureConversation(current.page,{navPinned:false});
}
async function fresh(){
 if(current.page)await current.page.close();
 current.page=await current.context.newPage();await enter();
}
async function closePanel(){
 const panel=current.page.locator('[data-part="dialog"]:visible');
 if(await panel.count())await panel.getByRole('button',{name:/^Close (panel|settings)$/i}).click();
 await expect(panel).toHaveCount(0);
}
async function openDetails(scope,label){
 const summary=scope.locator('summary').filter({hasText:new RegExp('^'+label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')});
 await expect(summary).toHaveCount(1);
 if(!await summary.evaluate(el=>el.parentElement.open))await summary.click();
 return summary.locator('..');
}
async function size(width){
 await current.page.setViewportSize({width,height:width<=390?1000:1080});
 await expect.poll(()=>current.page.evaluate(()=>innerWidth)).toBe(width);
}
async function lookAt(look){
 const selected=await updateAppearance(current.page,{appearanceId:look.id,accent:null,scheme:'light',
  interfaceDetail:'detailed',executionDetail:'detailed',density:'comfortable',
  executionVisibility:{step:true,turn:true,conversation:true},
  executionMetrics:['elapsedSeconds','grossTotalTokens','costUsd','calls'],planPlacement:'composer'});
 assert.equal(selected.effective.appearanceId,look.id);
 let entry=null;
 if(look.id!=='reference'){
  entry=await action('theme.read',{id:look.id});assert.equal(typeof entry.css,'string');
  await expect.poll(()=>current.page.locator('#amp-one > style').evaluateAll(
   (nodes,css)=>nodes.some(node=>node.textContent===css),entry.css)).toBe(true);
 }
 current.look={requested:look,observed:{effective:selected.effective,activeScope:selected.activeScope,
  compositionId:selected.composition?.id,observedCompositionId:selected.observedCompositionId,
  entry:entry?{id:entry.id,name:entry.name,fingerprint:entry.fingerprint,cssSha256:sha(entry.css)}:null}};
}
// No inserted measurement markup/styles. Text ranges retain both raw fragment
// rectangles and text so 046/3ee-like glyph/baseline reports can be compared.
function measureSurface(scope){
 const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,left:r.left,top:r.top,right:r.right,
  bottom:r.bottom,width:r.width,height:r.height}};
 const visible=el=>!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden';
 const identify=el=>{
  for(const attr of ['data-node-id','data-group-id','data-question-id','data-approval-id','data-artifact-id','data-part']){
   if(el.hasAttribute(attr))return `[${attr}=${JSON.stringify(el.getAttribute(attr))}]`;
  }
  const classes=typeof el.className==='string'?el.className.trim().split(/\s+/).filter(Boolean):[];
  return el.id?'#'+CSS.escape(el.id):el.getAttribute('aria-label')?
   `${el.tagName.toLowerCase()}[aria-label=${JSON.stringify(el.getAttribute('aria-label'))}]`:
   el.tagName.toLowerCase()+(classes.length?'.'+classes.map(value=>CSS.escape(value)).join('.'):'');
 };
 const style=el=>{
  const s=getComputedStyle(el);return {display:s.display,position:s.position,boxSizing:s.boxSizing,
   padding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft],
   margin:[s.marginTop,s.marginRight,s.marginBottom,s.marginLeft],
   border:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth],
   gap:s.gap,rowGap:s.rowGap,columnGap:s.columnGap,alignItems:s.alignItems,alignSelf:s.alignSelf,
   verticalAlign:s.verticalAlign,lineHeight:s.lineHeight,fontSize:s.fontSize,fontFamily:s.fontFamily,
   textAlign:s.textAlign,whiteSpace:s.whiteSpace,overflowX:s.overflowX,overflowY:s.overflowY};
 };
 const all=selector=>[...(scope.matches(selector)?[scope]:[]),...scope.querySelectorAll(selector)].filter(visible);
 const ranges=[];
 const walker=document.createTreeWalker(scope,NodeFilter.SHOW_TEXT);
 for(let text;((text=walker.nextNode()))&&ranges.length<140;){
  if(!text.textContent.trim()||text.parentElement.closest('input,textarea,[contenteditable="true"]')||
   !visible(text.parentElement)||text.parentElement.closest('script,style'))continue;
  const range=document.createRange();range.selectNodeContents(text);
  const boxes=[...range.getClientRects()].filter(r=>r.width&&r.height);
  if(!boxes.length)continue;
  ranges.push({text:text.textContent.trim().slice(0,180),parent:identify(text.parentElement),
   closestOwner:identify(text.parentElement.closest('[data-node-id],[data-question-id],[data-approval-id],[data-artifact-id]')||scope),
   rects:boxes.slice(0,8).map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}))});
 }
 const controls=all('button,summary,select,input,textarea,a[href],[role="textbox"],[role="tab"],[role="separator"]');
 const rows=all('[data-node-id],.a-delegate-message,.a-work-delegate-status,.a-execution-turn-line,.a-execution-action-line,.a-dialog-actions,.a-execution-payload,.a-question-context,[data-part="feedback-diagnostics"]');
 return {scope:identify(scope),bounds:rect(scope),style:style(scope),
  viewport:{width:innerWidth,height:innerHeight},pageExtent:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
  limits:{controls:controls.length,retainedControls:Math.min(controls.length,180),rows:rows.length,retainedRows:Math.min(rows.length,100),textFragmentCap:140},
  controls:controls.slice(0,180).map(el=>({selector:identify(el),role:el.getAttribute('role'),tag:el.tagName,
   action:el.dataset.action||null,label:el.getAttribute('aria-label')||el.labels?.[0]?.textContent?.trim()||el.textContent.trim().slice(0,160),
   expanded:el.getAttribute('aria-expanded'),disabled:!!el.disabled||el.getAttribute('aria-disabled')==='true',
   inert:!!el.closest('[inert]'),tabIndex:el.tabIndex,focused:el===document.activeElement,bounds:rect(el),style:style(el),
   ...(el.tagName==='SELECT'?{options:[...el.options].map(o=>({value:o.value,label:o.label,selected:o.selected}))}:{})})),
  rows:rows.slice(0,100).map(el=>({selector:identify(el),nodeId:el.dataset.nodeId||null,groupId:el.dataset.groupId||null,
   kind:el.dataset.kind||null,ownerId:el.dataset.ownerId||null,workEntry:el.dataset.workEntry||null,
   actor:el.querySelector('.a-work-actor')?.textContent||null,text:el.textContent.trim().slice(0,300),
   bounds:rect(el),leftRelativeToScope:rect(el).left-rect(scope).left,
   intersectsViewport:rect(el).right>0&&rect(el).left<innerWidth&&rect(el).bottom>0&&rect(el).top<innerHeight,
   style:style(el)})),
  textFragments:ranges,
  links:all('a[href]').slice(0,35).map(el=>{
   const range=document.createRange();range.selectNodeContents(el);
   const previous=el.previousSibling,next=el.nextSibling;
   const adjacent=node=>{if(!node)return null;const r=document.createRange();r.selectNodeContents(node);
    return {text:node.textContent.slice(0,140),rects:[...r.getClientRects()].slice(0,5).map(b=>({top:b.top,bottom:b.bottom,left:b.left,right:b.right}))}};
   return {text:el.textContent,href:el.getAttribute('href'),bounds:rect(el),range:rect(range),
    fragmentBottoms:[...range.getClientRects()].map(r=>r.bottom),previous:adjacent(previous),next:adjacent(next),style:style(el)};
  }),
  paint:{accent:getComputedStyle(document.getElementById('amp-one')).getPropertyValue('--a-accent').trim(),
   scheme:document.getElementById('amp-one').dataset.themeScheme,
   styles:[...document.querySelectorAll('#amp-one > style')].map(el=>({length:el.textContent.length}))}};
}
async function capture(label,locator,selector,{scroll=true}={}){
 await expect(locator).toBeVisible();if(scroll)await locator.scrollIntoViewIfNeeded();
 await current.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const stem=slug(currentCheckpoint+'-'+(++evidenceSequence)+'-'+label);
 const observation=await locator.evaluate(measureSurface);
 const appearance=await current.page.evaluate(()=>{
  const a=window.amplifier.getState().appearance;
  return {effective:a?.effective,renderedPreferences:a?.renderedPreferences,
   observedCompositionId:a?.observedCompositionId,compositionId:a?.composition?.id,
   styles:[...document.querySelectorAll('#amp-one > style')].map(el=>el.textContent)};
 });
 appearance.styleSha256=appearance.styles.map(sha);delete appearance.styles;
 observation.appearance={...current.look,...appearance};
 observation.selector=selector;observation.provenanceFile=current.provenanceFile;
 observation.wheelSha256=current.provenance.wheelSha256;
 observation.syntheticReads=report.safety.syntheticReads.filter(row=>row.checkpoint===currentCheckpoint)
  .map(({action,label,consumed,interception})=>({action,label,consumed,interception}));
 observation.visualReview='not-performed';observation.screenshot=stem+'.png';
 // Natural pixel crop of the on-screen intersection, not a scaled full-element
 // screenshot which could hide the real scroll clipping of a long Work body.
 const box=await locator.boundingBox(),viewport=current.page.viewportSize();
 const left=Math.max(0,Math.floor(box.x)-8),top=Math.max(0,Math.floor(box.y)-8);
 const right=Math.min(viewport.width,Math.ceil(box.x+box.width)+8);
 const bottom=Math.min(viewport.height,Math.ceil(box.y+box.height)+8);
 const clip={x:left,y:top,width:right-left,height:bottom-top};
 assert.ok(clip.width>0&&clip.height>0,'Measured subject intersects the natural viewport');
 observation.naturalCrop={file:stem+'-crop.png',clip,scale:'css',meaning:'visible intersection only; viewport screenshot retained alongside'};
 const evidence={measurement:stem+'.json',screenshot:stem+'.png',selector,
  naturalCrop:stem+'-crop.png',crop:clip,screenshotStatus:'pending',visualReview:'not-performed'};
 const checkpointRow=report.checkpoints.find(row=>row.id===currentCheckpoint);
 (checkpointRow.evidence??=[]).push(evidence);await save();
 await writeFile(join(out,stem+'.json'),JSON.stringify(observation,null,2)+'\n');
 await current.page.screenshot({path:join(out,stem+'.png'),animations:'disabled'});
 await current.page.screenshot({path:join(out,stem+'-crop.png'),clip,scale:'css',animations:'disabled'});
 evidence.screenshotStatus='saved';await save();return evidence;
}
async function verifyServed(){
 const {page,context,provenance}=current,assets=await page.locator('script[src],link[rel="stylesheet"][href]').evaluateAll(
  nodes=>nodes.map(node=>node.src||node.href));
 const paths=['/',...new Set(assets)];
 const joins=[];
 for(const asset of paths){
  const url=new URL(asset,current.base);assert.equal(url.origin,current.base);
  const relative=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname).replace(/^\/(?:static\/)?/,'');
  const expected=provenance.staticSha256[relative];assert.ok(expected,'No wheel static identity for '+relative);
  const response=await context.request.get(url.href);assert.equal(response.status(),200);
  const observed=sha(await response.body());assert.equal(observed,expected,'Served bytes/wheel '+relative);
  joins.push({path:url.pathname,installedStatic:relative,wheelPath:'amplifier_web/static/'+relative,expected,observed});
 }
 assert.ok(joins.some(row=>row.path.endsWith('.js')));assert.ok(joins.some(row=>row.path.endsWith('.css')));
 await Promise.all(current.assetReadsPending);
 assert.deepEqual(current.assetReadErrors,[],'Browser-selected response bytes match the installed wheel');
 for(const extension of ['.js','.css'])
  assert.ok(current.browserAssetReads.some(row=>row.path.endsWith(extension)&&row.matches),
   'Observe a browser-loaded '+extension+' response, not only a fresh APIRequestContext fetch');
 return {provenanceFile:current.provenanceFile,packageRoot:provenance.packageRoot,
  wheelSha256:provenance.wheelSha256,wheelJoin:provenance.wheelJoin,served:joins,
  browserSelected:current.browserAssetReads.slice()};
}
function syntheticHierarchy(sid){
 const base=1791409080,failed='pass2-failed',worked='pass2-worked';
 const common={turnId:failed,rootSessionId:sid,phase:'completed',sessionId:sid};
 const tool=(id,owner,sessionId,label,input,output,start,end,phase='completed')=>({
  ...common,id,parentId:owner,sessionId,kind:'tool',label,phase,startedAt:base+start,endedAt:base+end,
  input:JSON.stringify(input),output:JSON.stringify(output)});
 const request=(id,owner,sessionId,label,start,end)=>{
  const node=tool(id,owner,sessionId,'delegate',
   {agent:label,instruction:'SYNTHETIC PRESENTATION ONLY: '+label+' retained request.'},
   {success:true,output:{response:'SYNTHETIC '+label+' reply',session_id:id+'-session'}},start,end);
  return {...node,exchange:{id,sessionId,startedAt:base+start,endedAt:base+end,phase:'completed',
   delegateRequest:'SYNTHETIC PRESENTATION ONLY: '+label+' retained request.',
   delegateResponse:'SYNTHETIC '+label+' reply'}};
 };
 const worker=(id,parentId,sessionId,label,start,end,phase)=>({...common,id,parentId,sessionId,
  kind:'worker',label,phase,startedAt:base+start,endedAt:base+end,
  summary:'Synthetic fixture status, not evidence about a real worker.'});
 const nodes=[
  tool('pass2-root-failed',null,sid,'bash',{command:'synthetic-check --never-executed'},
   {success:false,output:{stdout:'SYNTHETIC ROOT INPUT/OUTPUT',stderr:'SYNTHETIC root failure',returncode:1}},1,2,'failed'),
  request('pass2-child-request',null,sid,'Synthetic child',3,18),
  worker('pass2-child','pass2-child-request','synthetic-child','Synthetic child',3,18,'failed'),
  tool('pass2-child-failed','pass2-child','synthetic-child','read_file',{file_path:'/synthetic/child.txt'},
   {success:false,output:{content:'SYNTHETIC CHILD BODY\nInput and output remain inspectable.',
    stderr:'Synthetic child tool failure',returncode:2}},4,5,'failed'),
  request('pass2-grandchild-request','pass2-child','synthetic-child','Synthetic grandchild',6,11),
  worker('pass2-grandchild','pass2-grandchild-request','synthetic-grandchild','Synthetic grandchild',6,11,'failed'),
  tool('pass2-grandchild-failed','pass2-grandchild','synthetic-grandchild','tool_exec',
   {code:'// SYNTHETIC ONLY, NOT EXECUTED\ntext(\"grandchild\")'},
   {success:false,output:{stdout:'SYNTHETIC GRANDCHILD BODY',stderr:'Synthetic grandchild failure',returncode:3}},7,8,'failed'),
  request('pass2-sibling-request',null,sid,'Synthetic sibling',12,16),
  worker('pass2-sibling','pass2-sibling-request','synthetic-sibling','Synthetic sibling',12,16,'completed'),
  tool('pass2-sibling-tool','pass2-sibling','synthetic-sibling','read_file',{file_path:'/synthetic/sibling.txt'},
   {success:true,output:{content:'SYNTHETIC SIBLING BODY'}},13,14),
  {...common,id:'pass2-model',parentId:'pass2-child',sessionId:'synthetic-child',kind:'llm',
   label:'Model call',model:'synthetic-model',provider:'synthetic-provider',startedAt:base+15,endedAt:base+17,
   usage:{inputTokens:8,outputTokens:2,totalTokens:10,costUsd:0.001}},
  {...tool('pass2-worked-tool',null,sid,'bash',{command:'synthetic-success --never-executed'},
   {success:true,output:{stdout:'SYNTHETIC WORKED BODY',stderr:'',returncode:0}},31,33),turnId:worked},
 ];
 // Explicit fixture exchange/ancestry fields use the existing unit population
 // vocabulary. They are saved in the input record, never inferred from the UI.
 for(const [workerId,requestId] of [['pass2-child','pass2-child-request'],
  ['pass2-grandchild','pass2-grandchild-request'],['pass2-sibling','pass2-sibling-request']]){
  nodes.find(node=>node.id===workerId).exchange=nodes.find(node=>node.id===requestId).exchange;
 }
 nodes.find(node=>node.id==='pass2-grandchild').parentWorkerId='pass2-child';
 return {seedKind:'labelled synthetic presentation hierarchy; no ownership/capture claim',
  expectedOwners:{'pass2-child-failed':'pass2-child','pass2-grandchild-failed':'pass2-grandchild',
   'pass2-sibling-tool':'pass2-sibling','pass2-model':'pass2-child'},
  session:{status:'idle',workers:[],messages:[
   {id:'pass2-question',role:'user',text:'SYNTHETIC FAILED HIERARCHY: inspect children, grandchild and sibling. Adjacent [baseline link](https://example.com/audit) text.',createdAt:base},
   {id:'pass2-answer',role:'assistant',text:'Synthetic failed work is retained, not rerun.',createdAt:base+20},
   {id:'pass2-worked-question',role:'user',text:'SYNTHETIC successful comparison.',createdAt:base+30},
   {id:'pass2-worked-answer',role:'assistant',text:'Synthetic worked comparison is retained.',createdAt:base+35},
  ],execution:{turns:[
   {id:failed,anchorMessageId:'pass2-question',phase:'failed',startedAt:base,endedAt:base+19},
   {id:worked,anchorMessageId:'pass2-worked-question',phase:'completed',startedAt:base+30,endedAt:base+34},
  ],nodes}}};
}
async function expandGroup(selector){
 const group=current.page.locator(selector),toggle=group.locator(':scope > button.a-execution-turn-line');
 await expect(toggle).toHaveCount(1);
 if(await toggle.getAttribute('aria-expanded')!=='true')await toggle.click();
 await expect(toggle).toHaveAttribute('aria-expanded','true');return group;
}
async function expandNode(selector){
 const inspector=current.page.locator('#work-step-inspector');
 if(await inspector.getByRole('button',{name:'Close step details'}).isVisible())await inspector.getByRole('button',{name:'Close step details'}).click();
 const node=current.page.locator(selector),toggle=node.locator(':scope > button.a-execution-action-line');
 await expect(toggle).toBeVisible();
 if(await toggle.getAttribute('aria-expanded')!=='true')await toggle.click();
 await expect(inspector).toBeVisible();return inspector;
}
async function inspectionTool(callId){
 // The maintained finish event can move the public call between message gaps.
 // Source identity comes from the fixture's read-only public execution rows,
 // not from a node which is absent while its group body is collapsed/deferred.
 const inspector=current.page.locator('#work-step-inspector');
 if(await inspector.isVisible())await inspector.getByRole('button',{name:'Close step details'}).click();
 const source=await control({op:'inspect-work-source'});
 const sid=(await state()).selectedSessionId;
 const sourceNodes=source.sessions.find(row=>row.id===sid)?.execution?.nodes||[];
 const candidates=sourceNodes.filter(node=>node.kind==='tool'&&node.toolCallId===callId);
 assert.equal(candidates.length,1,'One maintained public fixture toolCallId '+callId);
 const sourceNode=candidates[0];
 assert.equal(sourceNode.turnId,'inspect','The requested call belongs to the maintained inspection scene');
 assert.equal(typeof sourceNode.id,'string');
 await expect.poll(()=>current.page.locator('.a-messages').getAttribute('data-work-projection')
  .then(value=>{try{const row=JSON.parse(value);return row.status==='ready'&&row.current===true}catch{return false}}))
  .toBe(true);
 const groupRoot=`.a-messages [data-group-id^=${JSON.stringify(sourceNode.turnId+'@')}]`;
 // Discover the GROUP summaries first. Neither this locator nor the expansion
 // prerequisite depends on rendered descendant tool rows.
 const summaries=current.page.locator(groupRoot+' > button.a-execution-turn-line');
 await expect(summaries.first()).toBeVisible();
 const observedSummaries=await summaries.evaluateAll(nodes=>nodes.map(node=>({
  groupId:node.parentElement.dataset.groupId,expanded:node.getAttribute('aria-expanded'),
  label:node.textContent,
 })));
 const groupIds=observedSummaries.map(row=>row.groupId);
 assert.ok(groupIds.length&&new Set(groupIds).size===groupIds.length,'Observed inspect groups are uniquely identified');
 const checkpointRow=report.checkpoints.find(row=>row.id===currentCheckpoint);
 const discovery={callId,sourceNode:{id:sourceNode.id,toolCallId:sourceNode.toolCallId,turnId:sourceNode.turnId},
  observedSummaries,expandedSummaries:[],lookupStatus:'groups-discovered-before-tool-lookup'};
 (checkpointRow.inspectionDiscoveries??=[]).push(discovery);await save();
 // "Everything" can report aria-expanded=true by default while useWorkDetail
 // defers off-screen IO. Pin the OBSERVED summary identities through the normal
 // shared expansion action, then scroll/observe each real summary. This is not
 // a DOM fake and does not fabricate a saved work group or tool result.
 const expanded=new Set((await state()).view.executionExpanded||[]);
 for(const id of groupIds){expanded.delete('closed:turn:'+id);expanded.add('turn:'+id)}
 await action('view.update',{patch:{executionExpanded:[...expanded]}});
 for(const id of groupIds){
  const group=await expandGroup(`${groupRoot}[data-group-id=${JSON.stringify(id)}]`);
  const summary=group.locator(':scope > button.a-execution-turn-line');
  await summary.scrollIntoViewIfNeeded();
  await expect(summary).toHaveAttribute('aria-expanded','true');
  discovery.expandedSummaries.push({groupId:id,expanded:await summary.getAttribute('aria-expanded')});
 }
 discovery.lookupStatus='all-observed-summaries-expanded';await save();
 const exactNode=`${groupRoot} [data-kind="tool"][data-node-id=${JSON.stringify(sourceNode.id)}]`;
 await expect(current.page.locator(exactNode)).toHaveCount(1);
 const observed=await current.page.locator(exactNode).evaluate(node=>({
  groupId:node.closest('[data-group-id]').dataset.groupId,nodeId:node.dataset.nodeId,
 }));
 assert.equal(observed.nodeId,sourceNode.id,'Rendered identity is the exact fixture call, not a suffix guess');
 assert.ok(groupIds.includes(observed.groupId),'Tool is inside a group whose summary was observed and opened');
 const selector=`.a-messages [data-group-id=${JSON.stringify(observed.groupId)}] [data-kind="tool"][data-node-id=${JSON.stringify(observed.nodeId)}]`;
 const node=await expandNode(selector);
 discovery.lookupStatus='exact-fixture-call-rendered';discovery.rendered=observed;await save();
 return {node,selector:'#work-step-inspector',rowSelector:selector,observedGroups:groupIds,publicFixtureNodeId:sourceNode.id,toolCallId:callId,...observed};
}
function messageArtifact(id,version=1){
 return current.page.locator(`.a-messages .a-chat-artifacts > section.a-document-output[data-artifact-id=${JSON.stringify(id)}][data-artifact-version="${version}"]`);
}
async function canvasDetails(){
 const canvas=current.page.locator('#workspace-canvas'),inspector=canvas.getByRole('region',{name:'Canvas details',exact:true});
 await expect(canvas).toBeVisible();
 // On a narrow pane the open inspector makes its reader aria-hidden/inert.
 // Its toggle is then not an accessible role match; the sheet itself is the
 // actual open control state. Do not click through it or force the reader.
 if(!await inspector.isVisible()){
  const details=button(canvas,'Canvas details');await expect(details).toBeVisible();await details.click();
 }
 await expect(inspector).toBeVisible();return inspector;
}
async function hierarchyTopProof(group){
 const proof=await group.evaluate(section=>{
  const pane=section.closest('.a-messages'),p=pane.getBoundingClientRect();
  const clip={left:Math.max(0,p.left+pane.clientLeft),top:Math.max(0,p.top+pane.clientTop),
   right:Math.min(innerWidth,p.left+pane.clientLeft+pane.clientWidth),
   bottom:Math.min(innerHeight,p.top+pane.clientTop+pane.clientHeight)};
  const selectors=[':scope > button.a-execution-turn-line',
   '[data-node-id="pass2-root-failed"] > button.a-execution-action-line'];
  // The next delegated operation is intentionally separated by its Request
  // message. Requiring that farther row in the SAME narrow viewport tested a
  // false compacting promise. Preserve its raw position separately; the accepted
  // witness is parent + first operation, followed by individual descendant views.
  const deferred=section.querySelector('[data-node-id="pass2-child-failed"] > button.a-execution-action-line');
  const d=deferred?.getBoundingClientRect();
  const deferredChild=d?{nodeId:'pass2-child-failed',top:d.top,bottom:d.bottom,
   left:d.left,right:d.right,relativeToParent:d.top-section.getBoundingClientRect().top}:null;
  return {paneClip:clip,scrollTop:pane.scrollTop,deferredChild,targets:selectors.map(selector=>{
   const node=section.querySelector(selector),r=node?.getBoundingClientRect();
   return {selector,text:node?.textContent,bounds:r?{left:r.left,right:r.right,top:r.top,bottom:r.bottom,
    width:r.width,height:r.height}:null,fullyVisible:!!r&&r.width>0&&r.height>0&&
    r.left>=clip.left-1&&r.right<=clip.right+1&&r.top>=clip.top-1&&r.bottom<=clip.bottom+1};
  })};
 });
 const row=report.checkpoints.find(row=>row.id===currentCheckpoint);
 (row.topVisibilityProbes??=[]).push(proof);await save();
 assert.ok(proof.targets.every(row=>row.fullyVisible),
  'The actual Failed summary and first operation row must be together inside the message viewport: '+JSON.stringify(proof));
 assert.match(proof.targets[0].text,/Failed/);return proof;
}
async function eachLook(id,run,{freshPage=false}={}){
 for(const look of looks)for(const width of screens)
  await checkpoint(`${id}.${slug(look.id)}.${width}`,async()=>{
   if(freshPage){
    const previous=(await state()).client.id;await fresh();
    assert.notEqual((await state()).client.id,previous,'Independent fixture page owns a fresh presentation identity');
   }
   await closePanel();await lookAt(look);await size(width);return run({look,width});
  });
}
async function detailScenes(){
 const initialRuntime=await control({op:'inspect-work-source'});
 await eachLook('P00.composer',async()=>{
  const input=current.page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await input.fill('Unsent pass-two composer specimen.\nSecond synthetic line.');
  const shots=[await capture('composer',current.page.locator('[data-part="composer"]'),'[data-part="composer"]')];
  await button(current.page,'Add attachments').click();
  const add=current.page.getByRole('dialog',{name:'Add to your message',exact:true});
  shots.push(await capture('attachment-menu',add,'dialog("Add to your message")'));
  await button(add,'Close attachment menu').click();
  const trigger=button(current.page,'Model and reasoning settings');await trigger.click();
  const model=current.page.getByRole('dialog',{name:'Model for this chat',exact:true});
  await openDetails(model,'Advanced');
  shots.push(await capture('model-advanced-popover',model,'dialog("Model for this chat")'));
  await current.page.keyboard.press('Escape');await expect(model).toHaveCount(0);
  return {shots,sent:false,uploaded:false,providerControlsActivated:false};
 });
 let seed;
 const seeded=await checkpoint('D.hierarchy.seed',async()=>{
  const sid=(await state()).selectedSessionId;seed=syntheticHierarchy(sid);
  await writeFile(join(out,'synthetic-hierarchy-input.json'),JSON.stringify(seed,null,2)+'\n');
  await control({op:'patch',sessions:{[sid]:seed.session}});await fresh();
  return {sessionId:sid,input:'synthetic-hierarchy-input.json',productionOwnershipClaim:false};
 });
 if(seeded)for(const [look,width] of [[looks[0],1440],[looks[1],390]])
  await checkpoint(`W10.top-visible.${slug(look.id)}.${width}`,async()=>{
   await lookAt(look);await size(width);
   // Exact fixture expansion reset, not DOM/style mutation. Capture the natural
   // compact parent and FIRST operation line BEFORE opening bodies. The child
   // delegate Request is intentionally interleaved before its later operation.
   await action('view.update',{patch:{executionExpanded:[
    'turn:pass2-failed@pass2-question','closed:turn:pass2-worked@pass2-worked-question',
   ],navPinned:false,navExpanded:false}});
   const selector='[data-group-id="pass2-failed@pass2-question"]';
   const group=await expandGroup(selector),summary=group.locator(':scope > button.a-execution-turn-line');
   for(const id of ['pass2-root-failed','pass2-child-failed'])
    await expect(group.locator(`[data-node-id="${id}"] > button`)).toHaveAttribute('aria-expanded','false');
   await summary.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));
   await current.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const top=await hierarchyTopProof(group);
   const shots=[await capture('failed-parent-first-operation-together',group,selector,{scroll:false})];
   assert.deepEqual((await hierarchyTopProof(group)).targets.map(row=>row.bounds),top.targets.map(row=>row.bounds),
    'Measurement/capture must not scroll the summary out of its witnessed position');
   for(const id of ['pass2-child-failed','pass2-grandchild-failed','pass2-sibling-tool']){
    const exact=`${selector} [data-node-id="${id}"]`;
    const node=await expandNode(exact);shots.push(await capture(id,node,'#work-step-inspector'));
   }
   await current.page.locator('#work-step-inspector').getByRole('button',{name:'Close step details'}).click();
   return {top,shots,input:'synthetic-hierarchy-input.json',
    togetherMeaning:'Failed parent + first operation line; the farther child operation follows an intentional delegate Request',
    oracleChange:'Accepted parent b37 scope: one operation in the shared viewport, no CSS change or tolerance increase; farther raw coordinates retained',
    captureOrder:'Failed parent+first collapsed operation line first; separate expanded child/grandchild/sibling bodies afterwards'};
  });
 if(seeded)for(const look of looks)for(const width of hierarchyWidths)
  await checkpoint(`W10.hierarchy.${slug(look.id)}.${width}`,async()=>{
   await lookAt(look);await size(width);
   const failed=await expandGroup('[data-group-id="pass2-failed@pass2-question"]');
   await expect(failed.locator(':scope > button')).toContainText('Failed');
   const worked=await expandGroup('[data-group-id="pass2-worked@pass2-worked-question"]');
   await expect(worked.locator(':scope > button')).toContainText('Worked');
   for(const [id,owner] of Object.entries(seed.expectedOwners)){
    const node=current.page.locator(`[data-node-id="${id}"]`);
    await expect(node).toHaveAttribute('data-owner-id',owner);
    await expect(node.locator('.a-work-actor')).toContainText('Synthetic');
    await expandNode(`[data-node-id="${id}"]`);
   }
   await expandNode('[data-node-id="pass2-root-failed"]');
   await expandNode('[data-node-id="pass2-worked-tool"]');
   await current.page.locator('#work-step-inspector').getByRole('button',{name:'Close step details'}).click();
   let hierarchyAssertions=null;
   if(process.env.LAYOUT_REQUIRE_FIXED==='1'){
    const {assertWorkHierarchy}=await import('../../.build/web-client/tests/work-hierarchy-geometry.mjs');
    hierarchyAssertions={
     failed:await assertWorkHierarchy(failed,`installed Failed ${look.id} ${width}`),
     worked:await assertWorkHierarchy(worked,`installed Worked ${look.id} ${width}`),
    };
   }
   const shots=[];
   shots.push(await capture('failed-group',failed,'[data-group-id="pass2-failed@pass2-question"]'));
   shots.push(await capture('adjacent-link-baseline',current.page.locator('[data-message-id="pass2-question"]'),
    '[data-message-id="pass2-question"]'));
   for(const id of ['pass2-child-failed','pass2-grandchild-failed','pass2-sibling-tool'])
    shots.push(await capture(id,current.page.locator(`[data-node-id="${id}"]`),`[data-node-id="${id}"]`));
   shots.push(await capture('worked',worked,'[data-group-id="pass2-worked@pass2-worked-question"]'));
   return {expectedSource:'synthetic-hierarchy-input.json',hierarchyAssertions,
    hierarchyMeaning:'flat actor/owner-labelled stream, not claimed recursive indentation',shots};
  });
 if(wants('W03.tool-bodies')){
 await control({op:'inspection'});await control({op:'inspection-actions'});await control({op:'inspection-finish'});await fresh();
 await eachLook('W03.tool-bodies',async()=>{
  const shots=[],calls=[];
  for(const id of ['command','read','failed','code','nested','inspect-tool']){
   const {node,selector,...binding}=await inspectionTool(id);
   if(id==='failed')await expect(node).toContainText('AssertionError: expected committed');
   shots.push(await capture(id,node,selector));calls.push(binding);
  }
  await current.page.locator('#work-step-inspector').getByRole('button',{name:'Close step details'}).click();
  return {shots,calls,scope:'Observed inspect-turn groups and exact public fixture calls; recorded Input/Output/failed/nested bodies; no tools executed'};
 });
 }
 if(wants('W04.model-recordings')){
 await control({op:'canonical-work'});await fresh();
 await eachLook('W04.model-recordings',async()=>{
  const turn=await expandGroup('[data-group-id="canonical@before"]');
  const row=turn.locator('[data-kind="llm"]'),node=current.page.locator('#work-step-inspector');
  await row.locator(':scope > button.a-execution-action-line').click();
  await expect(node.getByRole('button',{name:/^View request/})).toBeVisible();
  await node.getByRole('button',{name:/^View request/}).click();
  const request=node.getByRole('group',{name:'Request payload',exact:true});
  await request.getByRole('button',{name:/^Request Object/}).click();
  await node.getByRole('button',{name:/^View response/}).click();
  const response=node.getByRole('group',{name:'Response payload',exact:true});
  await response.getByRole('button',{name:/^Response Object/}).click();
  const shot=await capture('model-input-output',node,'#work-step-inspector');
  // Close only the local toggles; the next checkpoint opens them explicitly.
  await node.getByRole('button',{name:'Close step details'}).click();
  return {shots:[shot],downloadsAndCopies:'controls observed, not activated'};
 });
 }
 if(wants('W11.plan')){
 await control({op:'chat-plan'});await fresh();
 await eachLook('W11.plan',async()=>{
  const plan=current.page.getByRole('region',{name:'Plan',exact:true});
  const summary=plan.locator('summary');await expect(summary).toHaveCount(1);
  if(!await summary.evaluate(el=>el.parentElement.open))await summary.click();
  await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();
  return {shots:[await capture('expanded-plan',plan,'region("Plan")')],sourceDefectClaim:false};
 });
 }
 // All actual per-chat Advanced pages, no global Settings navigation.
 for(const [title,panel,summaries] of advanced)for(const width of screens)
  await checkpoint(`P03.${panel}.${width}`,async()=>{
   await size(width);await lookAt(looks[0]);await openChatAdvancedPage(current.page,title);
   const dialog=current.page.getByRole('dialog',{name:title,exact:true});
   const shots=[await capture('page',dialog,`dialog(${JSON.stringify(title)})`)];
   for(const name of summaries){
    const section=await openDetails(dialog,name);shots.push(await capture(slug(name),section,`dialog(${JSON.stringify(title)}) summary(${JSON.stringify(name)})`));
   }
   await closePanel();return {shots,configurationMutations:'not activated'};
  });
 const sid=(await state()).selectedSessionId;
 if(wants('H04.error-diagnostics')||wants('H05.help-report')){
 await control({op:'patch',sessions:{[sid]:{status:'error',
  error:'Synthetic pass-two support specimen. '+('synthetic-diagnostic-detail-'.repeat(24)),
  failure:{category:'context_limit',summary:'Synthetic support failure',guidance:'Original synthetic messages are kept.'}}}});
 await eachLook('H04.error-diagnostics',async()=>{
  await action('view.update',{patch:{panel:'chat-error'}});
  const dialog=current.page.getByRole('dialog',{name:'Error details',exact:true});
  await openDetails(dialog.getByRole('region',{name:'Recorded error details',exact:true}),'Technical details');
  return {shots:[await capture('error-details',dialog,'dialog("Error details")')],
   controlsNotActivated:['Create recovery copy','retry','restart','clear context']};
 });
 await eachLook('H05.help-report',async()=>{
  await action('view.update',{patch:{panel:'chat-help'}});
  const dialog=current.page.getByRole('dialog',{name:'Help & diagnostics',exact:true});
  const diagnostics=dialog.locator('[data-part="feedback-diagnostics"]');
  await diagnostics.locator('summary').click();
  await expect(button(diagnostics,'Copy report')).toBeVisible();
  const shots=[await capture('diagnostics-padding',diagnostics,'[data-part="feedback-diagnostics"]')];
  await button(dialog,'Report a problem').click();
  const form=current.page.locator('[data-part="feedback"]');await expect(form).toBeVisible();
  shots.push(await capture('unsent-report',form,'[data-part="feedback"]'));
  return {shots,submitted:false,captureSettingsChanged:false};
 });
 await closePanel();
 // Restore idle presentation only, so error controls cannot obscure artifacts.
 await control({op:'patch',sessions:{[sid]:{status:'idle',error:null,failure:null}}});
 }
 if(wants('C02.details')){
 const html=await seedCanvas({kind:'html',title:'SYNTHETIC preview cancellation',presentation:'saved',
  content:'<!doctype html><html><body><h1>Synthetic preview</h1><label>Local unsaved note<input aria-label="Local unsaved note"></label><p>Owned fixture only; no network or host calls.</p></body></html>'});
 const htmlId=html.id;
 // A second immutable version is initial synthetic setup, not user interaction.
 const revision={id:htmlId,expectedRevision:1,content:'<!doctype html><html><body><h1>Synthetic preview v2</h1><label>Local unsaved note<input aria-label="Local unsaved note"></label></body></html>'};
 current.guard.permit('canvas.versions.revise',revision);
 await action('canvas.versions.revise',revision);
 await eachLook('C02.details',async()=>{
  await action('canvas.select',{id:htmlId});await action('canvas.visibility',{open:true});
  const inspector=await canvasDetails();
  await expect(inspector.getByRole('combobox',{name:'Open with',exact:true})).toBeVisible();
  await expect(inspector.getByRole('combobox',{name:'Artifact version',exact:true})).toBeVisible();
  const result={shots:[await capture('nonempty-details',inspector,'#workspace-canvas region("Canvas details")')],
   versionOrRendererChanged:false};
  await button(inspector,'Close canvas details').click();return result;
 });
 }
 await eachLook('C09.preview-cancel',async({look,width})=>{
  await action('canvas.visibility',{open:false});
  const title=`SYNTHETIC preview cancellation ${look.id} ${width}`;
  const html=await seedCanvas({kind:'html',title,
   presentation:'saved',content:'<!doctype html><html><body><h1>Synthetic preview</h1><label>Local unsaved note<input aria-label="Local unsaved note"></label><p>Owned fixture only; no network or host calls.</p></body></html>'});
  const htmlId=html.id,card=messageArtifact(htmlId);
  await expect(card).toHaveCount(1);await card.scrollIntoViewIfNeeded();
  await button(card,`Open ${title} in canvas`).click();
  const canvas=current.page.locator('#workspace-canvas'),close=button(canvas,'Close canvas details');
  if(await close.isVisible())await close.click();
  const frame=canvas.frameLocator('iframe'),input=frame.getByLabel('Local unsaved note',{exact:true});
  await input.fill('Keep this synthetic local input');
  const original=await canvas.locator('iframe').elementHandle();
  // Narrow Canvas covers the chat. Closing the panel retains the actual preview;
  // it does not reset/release it, and lets the card's real move control be used.
  await button(canvas,'Close canvas panel').click();
  await expect(card).toBeVisible();await expect(card.locator('.a-document-preview-toggle')).toHaveAttribute('aria-expanded','false');
  await button(card,'Preview in chat').click();
  await expect(button(card,'Keep current preview')).toBeVisible();
  const selector=`.a-messages .a-chat-artifacts > section.a-document-output[data-artifact-id="${htmlId}"][data-artifact-version="1"]`;
  const shots=[await capture('preview-move-confirmation',card,selector)];
  await button(card,'Keep current preview').click();
  assert.equal(await original.evaluate(el=>el.isConnected),true);
  await action('canvas.visibility',{open:true});
  await expect(input).toHaveValue('Keep this synthetic local input');
  shots.push(await capture('cancel-retains-preview',canvas,'#workspace-canvas'));
  await original.dispose();await button(canvas,'Close canvas panel').click();
  return {shots,artifactId:htmlId,cancelControl:'Keep current preview',rendererRetained:true,resetActivated:false};
 },{freshPage:true});
 await eachLook('C10.writing-edit-preview',async({look,width})=>{
  // Reset is forbidden. Markdown navigation has no executing local preview.
  await action('canvas.visibility',{open:false});
  const writing=await seedCanvas({kind:'markdown',title:`SYNTHETIC writing specimen ${look.id} ${width}`,
   presentation:'saved',content:'## Links and local writing\n\nAdjacent [baseline link](https://example.com/audit) text.\n\n:::writing{variant="document" id="12345"}\nOriginal synthetic writing copy.\n:::\n'});
  const writingId=writing.id,card=messageArtifact(writingId);
  await expect(card).toHaveCount(1);await card.scrollIntoViewIfNeeded();
  const before=(await action('canvas.versions.inspect',{id:writingId,version:1,includeSource:true})).source;
  assert.equal(typeof before?.content,'string','Actual saved source is available for the preservation check');
  assert.ok(before.content.includes('Original synthetic writing copy.'));
  await expect(card.locator('.a-document-preview-toggle')).toHaveAttribute('aria-expanded','false');
  await button(card,'Preview in chat').click();
  const writingBlock=card.getByRole('region',{name:'Reusable writing',exact:true});
  await expect(writingBlock).toBeVisible();
  await button(writingBlock,'Edit copy').click();
  const editor=writingBlock.getByRole('textbox',{name:'Edit writing copy',exact:true});
  await editor.fill('Unsent synthetic local writing edit; not a saved version.');
  const shots=[await capture('edit-local-copy',writingBlock,'[aria-label="Reusable writing"]')];
  await button(writingBlock,'Preview copy').click();
  await expect(writingBlock).toContainText('Unsent synthetic local writing edit');
  shots.push(await capture('preview-local-copy',writingBlock,'[aria-label="Reusable writing"]'));
  await button(card,'Hide preview').click();
  await expect(card.locator('.a-document-preview-toggle')).toHaveAttribute('aria-expanded','false');
  const after=(await action('canvas.versions.inspect',{id:writingId,version:1,includeSource:true})).source;
  assert.deepEqual(after,before,'Local editing never creates/replaces a saved source version');
  return {shots,artifactId:writingId,actualSemantics:'Fresh saved artifact per case; Preview copy toggles editing, not Cancel/undo; no Save writing version dispatched'};
 },{freshPage:true});
 for(const [look,width] of [[looks[0],1440],[looks[1],390]])
  await checkpoint(`H06.diagnostics-manual.${slug(look.id)}.${width}`,async()=>{
   await fresh();await size(width);await lookAt(look);
   const sid=(await state()).selectedSessionId;
   const failure={category:'context_limit',summary:'SYNTHETIC manual diagnostic specimen',
    guidance:'Presentation-only report. No recovery, restart, resend or upload.',
    generationId:'synthetic-pass2-generation',inputIds:['synthetic-pass2-input'],recordedAt:1791409080};
   await control({op:'patch',sessions:{[sid]:{status:'error',error:failure.summary,failure}}});
   await action('view.update',{patch:{panel:'chat-error'}});
   const dialog=current.page.getByRole('dialog',{name:'Error details',exact:true});
   const synthetic={diagnosticsSchema:2,id:sid,sessionId:sid,status:'error',
    syntheticPresentation:true,fixtureLabel:'SYNTHETIC PRE-HOST REPORT; no real worker inspected',
    failure,environment:{applicationVersion:'synthetic-fixture-version'},
    failureEvidence:[{source:'labelled synthetic report',detail:'long-layout-token-'.repeat(24)}]};
   const seededRead=current.guard.seedRead('session.inspect',{id:sid},synthetic,
    'Synthetic diagnostics response for actual ConversationDetails copy/manual-fallback controls');
   await current.page.evaluate(()=>{
    const original=Object.getOwnPropertyDescriptor(navigator,'clipboard');
    window.layoutPass2RestoreClipboard=()=>{if(original)Object.defineProperty(navigator,'clipboard',original);
     else delete navigator.clipboard;delete window.layoutPass2RestoreClipboard};
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
     writeText:async()=>{throw new DOMException('Synthetic audit clipboard denial','NotAllowedError')},
    }});
   });
   try{
    await button(dialog,'Copy diagnostics').click();
    await expect.poll(()=>seededRead.consumed).toBe(true);
    await openDetails(dialog,'Copy diagnostic text manually');
    const manual=dialog.getByRole('textbox',{name:'Diagnostic report',exact:true});
    const parsed=JSON.parse(await manual.inputValue());
    assert.equal(parsed.syntheticPresentation,true);assert.deepEqual(parsed.failure,failure);
    assert.equal(parsed.fixtureLabel,synthetic.fixtureLabel);
    await expect(dialog.getByText('Diagnostics copied',{exact:true})).toHaveCount(0);
    const shot=await capture('clipboard-denied-manual-report',dialog,'dialog("Error details")');
    return {shots:[shot],syntheticRead:seededRead,
     boundary:'Browser-only clipboard denial and one exact pre-host synthetic session.inspect; actual component rendered',
     noClipboardWriteClaim:true,noRecoveryOrSubmission:true};
   }finally{
    await current.page.evaluate(()=>window.layoutPass2RestoreClipboard?.());
    await closePanel();
    await control({op:'patch',sessions:{[sid]:{status:'idle',error:null,failure:null}}});
   }
  });
 const final=await control({op:'inspect-work-source'});
 assert.equal(final.runtimeSends,initialRuntime.runtimeSends,'No synthetic input sent');
 assert.equal(final.runtimeStarts,initialRuntime.runtimeStarts,'No runtime started by presentation');
 return final;
}
async function questionScenes(){
 const {page,ready}=current;
 for(const look of looks)for(const width of screens){
  await checkpoint(`W12.question-alternative-context.${slug(look.id)}.${width}`,async()=>{
   await action('session.select',{id:ready.sessionId});await lookAt(look);await size(width);
   const card=page.locator(`[data-question-id="${ready.required}"]`);
   await openDetails(card,'Write a different answer');await openDetails(card,'Question context');
   await expect(card.locator('textarea')).toBeVisible();
   return {shots:[await capture('alternative-and-context',card,`[data-question-id="${ready.required}"]`)],
    answered:false,skipped:false};
  });
  await checkpoint(`W13.question-original.${slug(look.id)}.${width}`,async()=>{
   const reminder=ready.reminders.find(row=>row.delivery.status==='rejected');
   assert.ok(reminder,'Dismiss mode supplies pre-answered rejected question');
   await action('session.select',{id:reminder.sessionId});
   const card=page.locator(`[data-question-id="${reminder.id}"]`);
   await openDetails(card,'Original question & provenance');
   return {shots:[await capture('original-question',card,`[data-question-id="${reminder.id}"]`)],
    priorAnswer:'fixture-preseeded',dismissed:false,deliveryRetry:false};
  });
 }
 if(wants('C08.question-artifacts')){
 await action('session.select',{id:ready.sessionId});
 const seeded=await fixturePost('/fixture/reviews');
 current.artifactIds.add(seeded.markdown.id);current.artifactIds.add(seeded.html.id);
 await action('canvas.visibility',{open:false});
 for(const look of looks)for(const width of screens)
  await checkpoint(`C08.question-artifacts.${slug(look.id)}.${width}`,async()=>{
   await lookAt(look);await size(width);
   const card=page.locator(`[data-question-id="${seeded.question.id}"]`);
   const compact=card.locator('.a-compact-artifact').first();
   const preview=compact.locator('.a-document-preview-toggle');
   if(await preview.getAttribute('aria-expanded')!=='true')await preview.click();
   await expect(card.locator('[data-full-artifact-preview]')).toBeVisible();
   return {shots:[await capture('pinned-decision-artifact',card,`[data-question-id="${seeded.question.id}"]`)],
    syntheticVersion:1,latestSeededVersion:2,interactiveHtmlNotActivated:true};
  });
 }
 const final=await readFixture('/fixture/check');
 assert.deepEqual(final.sent,[]);assert.deepEqual(final.retries,[]);
 assert.equal(final.networkAttempts,0,'No external/sandbox fixture probe activated');
 return final;
}
async function approvalScenes(){
 const before=await readFixture('/fixture/check');
 for(const look of looks)for(const width of screens)
  await checkpoint(`W14.approval-review-cancel.${slug(look.id)}.${width}`,async()=>{
   await lookAt(look);await size(width);
   const selector=`[data-approval-id="${current.ready.grant}"]`,card=current.page.locator(selector);
   await button(card,'Review current scope').click();
   const review=card.getByRole('region',{name:'Current configuration scope',exact:true});
   await expect(review).toBeVisible();
   const shots=[await capture('approval-scope-review',card,selector)];
   await button(card,'Close scope review').click();await expect(review).toHaveCount(0);
   shots.push(await capture('review-cancelled',card,selector));
   const after=await readFixture('/fixture/check');assert.deepEqual(after,before);
   return {shots,cancelControl:'Close scope review',approvalOrGrantDecision:false,fixtureSnapshotUnchanged:true};
  });
 return readFixture('/fixture/check');
}
async function openWorkspaceDetails(workspacePath){
  await closePanel();
  await showFixtureConversation(current.page,{navPinned:false});
  const crumb=current.page.locator('[data-part="header"] .a-work-crumb:not(.a-work-home-crumb)');
  let entryRoute;
  if(await crumb.isVisible()){
   await crumb.click();entryRoute='visible header workspace breadcrumb';
  }else{
   // work-shell.css hides the breadcrumb on mobile. Use the real sidebar
   // All workspaces -> exact folder row path instead of forcing the hidden DOM.
   await button(current.page,'Open navigation').click();
   const nav=current.page.locator('#workspace-navigation');
   await button(nav,'Browse').click();
   await button(nav,'All workspaces').click();
   const close=button(current.page,'Close navigation');
   if(await close.isVisible())await close.click();
   const row=current.page.locator(`.a-work-browser .a-workspace-row[data-workspace-path=${JSON.stringify(workspacePath)}]`);
   await expect(row).toHaveCount(1);
   await button(row,'Open chats in '+workspacePath).click();
   entryRoute='sidebar Browse -> All workspaces -> exact fixture workspace row';
  }
  await button(current.page.getByRole('group',{name:'Workspace view',exact:true}),'Details').click();
  return entryRoute;
}
async function openWorkspaceReview(workspacePath){
  const entryRoute=await openWorkspaceDetails(workspacePath);
  const entry=current.page.locator('.a-workspace-work-entry > summary');
  if(!await entry.evaluate(el=>el.parentElement.open))await entry.click();
  const related=current.page.getByRole('region',{name:'Workspace work overview',exact:true});
  await expect(related.getByRole('region',{name:'Working in this workspace',exact:true})).toBeVisible();
  return {entryRoute,related};
}
async function workspaceScenes(){
 const before=await readFixture('/fixture/ordinary-work');
 for(const width of screens)await checkpoint(`P08.workspace-review-bodies.${width}`,async()=>{
  await size(width);await lookAt(looks[0]);
  const {related,entryRoute}=await openWorkspaceReview(before.workspace);
  await openDetails(related,'Exact requests, messages and saved results');
  const message=related.locator('[data-request-id="ordinary-long-message"]');
  await button(message,'Open submitted message').click();
  const original=related.getByRole('article',{name:'Original message',exact:true});
  await expect(original).toContainText('Bounded excerpt; original retained.');
  const shots=[await capture('synthetic-retained-message-body',related,'region("Workspace work overview")')];
  const after=await readFixture('/fixture/ordinary-work');
  assert.deepEqual(after.files,before.files);assert.deepEqual(after.runtimeCalls,before.runtimeCalls);
  assert.equal(after.sourceMessage.text,before.sourceMessage.text);
  return {shots,entryRoute,syntheticPeerOnly:true,commissionedMemberBodies:'unvisited: no assignment created',projectAndRuntimeUnchanged:true};
 });
 for(const width of screens)await checkpoint(`P09.workspace-worker-details.${width}`,async()=>{
  await fresh();await size(width);await lookAt(width===1440?looks[0]:looks[1]);
  const {related,entryRoute}=await openWorkspaceReview(before.workspace);
  const responsePromise=current.page.waitForResponse(response=>{
   if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
   let body;try{body=unwrap(response.request().postDataJSON())}catch{return false}
   return body?.action==='coordination.review'&&body.args?.sessionId===before.selected;
  });
  responsePromise.catch(()=>{}); // Preserve a later timeout without an unhandled rejection after a navigation failure.
  await openDetails(related,'Review before continuing');
  const response=await responsePromise,receipt=await response.json();
  assert.equal(response.status(),200);assert.equal(receipt.accepted,true);
  const observed=receipt.result;assert.equal(observed.sessionId,before.selected);
  const review=related.getByRole('region',{name:'Review before continuing',exact:true});
  const app=await openDetails(review,'App update and worker details');
  const frontend=await openDetails(app,'Serving frontend identity');
  const buildResponse=await current.context.request.get(current.base+'/build.json');
  assert.equal(buildResponse.status(),200);const build=await buildResponse.json();
  assert.equal(observed.installedApp.frontend.id,build.id,'Actual serving frontend matches installed build endpoint');
  await expect(frontend.locator('code')).toHaveText(build.id);
  const shots=[await capture('serving-frontend-identity',app,
   'region("Review before continuing") details("App update and worker details")')];
  const synthetic={
   status:'synthetic-observed',sessionId:before.selected,syntheticPresentation:true,
   reason:'SYNTHETIC PRESENTATION ONLY: no worker inspected, started, refreshed or adopted.',
   observation:{id:'layout-pass2-synthetic-worker',source:'explicit browser fixture response',
    importedBytesVerified:false,packages:[{name:'synthetic-worker-module',revision:'fixture-only-not-a-code-revision'}]},
  };
  const read=current.guard.seedRead('runtime.provenance',{sessionId:before.selected},synthetic,
   'Worker-details presentation specimen; synthetic exact observation, never runtime/adoption evidence');
  await button(app,'Inspect this chat’s loaded sources').click();
  await expect.poll(()=>read.consumed).toBe(true);
  const exact=await openDetails(app,'Exact worker observation');
  const rendered=JSON.parse(await exact.locator('pre').innerText());
  assert.equal(rendered.syntheticPresentation,true);assert.equal(rendered.sessionId,before.selected);
  assert.equal(rendered.reason,synthetic.reason);
  shots.push(await capture('exact-synthetic-worker-observation',exact,
   'region("Review before continuing") details("Exact worker observation")'));
  const after=await readFixture('/fixture/ordinary-work');
  assert.deepEqual(after.runtimeCalls,before.runtimeCalls,'Synthetic read never reaches the fixture runtime');
  assert.deepEqual(after.files,before.files);assert.equal(after.assignment,before.assignment);
  return {shots,entryRoute,frontend:{source:'actual installed host coordination.review + /build.json',
    id:build.id,version:build.version},worker:{source:'pre-host labelled synthetic runtime.provenance',read},
   commissionedOrResumed:false,filesRuntimeAssignmentUnchanged:true};
 });
 return readFixture('/fixture/ordinary-work');
}
async function workspaceConfigurationScenes(){
 const before=await readFixture('/fixture/workspace-configuration');
 for(const width of screens)await checkpoint(`P10.workspace-configuration.${width}`,async()=>{
  await fresh();await size(width);await lookAt(width===1440?looks[0]:looks[1]);
  const entryRoute=await openWorkspaceDetails(current.ready.workspace);
  const config=current.page.getByRole('region',{name:'Workspace configuration',exact:true});
  const pages=config.getByRole('group',{name:'Workspace settings pages',exact:true});
  const choosePage=async name=>{
   const trigger=button(pages,name);
   if(await trigger.getAttribute('aria-pressed')!=='true')await trigger.click();
  };
  await choosePage('Model rules');
  const routing=config.locator('[data-part="routing-settings"]');
  await routing.locator('[data-collection-id="general"] > button').click();
  const description=routing.getByRole('textbox',{name:'general description',exact:true});
  const originalDescription=await description.inputValue();
  await description.fill('Synthetic layout draft role description; do not save.');
  const shots=[await capture('role-description-draft',routing,'region("Workspace configuration") [data-part="routing-settings"]')];
  await description.fill(originalDescription);
  await routing.locator('.a-routing-candidates [data-collection-id="0"] > button').click();
  const choiceToggle=button(routing,'Edit choice configuration');await choiceToggle.click();
  const choice=routing.getByRole('textbox',{name:'Choice configuration JSON',exact:true});
  const originalChoice=await choice.inputValue();
  await choice.fill(JSON.stringify({...JSON.parse(originalChoice),syntheticLayoutDraft:'not applied or saved'},null,2));
  shots.push(await capture('choice-json-unapplied',choice,'textarea[aria-label="Choice configuration JSON"]'));
  await choice.fill(originalChoice);await choiceToggle.click();
  await expect(choice).toHaveCount(0);
  await button(routing,'Preference order').click();
  const order=routing.locator('.a-order-editor');await expect(order).toBeVisible();
  shots.push(await capture('preference-order-cancel-controls',order,'.a-order-editor'));
  await button(order,'Cancel').click();await expect(order).toHaveCount(0);
  // These controls have no JSON/module Cancel action. Restore the typed drafts
  // through their actual inputs and close the page; never synthesize a Save.
  await choosePage('Modules & sources');
  const registry=config.locator('[data-part="registries"]');
  await button(registry,'Modules').click();await button(registry,'Add module').click();
  const module=registry.getByLabel('Module name',{exact:true});
  const source=registry.getByLabel('Source (optional)',{exact:true});
  const json=registry.getByLabel('Module configuration (JSON)',{exact:true});
  const originals={module:await module.inputValue(),source:await source.inputValue(),json:await json.inputValue()};
  await module.fill('tool-synthetic-layout-draft');await source.fill('./synthetic-layout-draft-never-resolved');
  await json.fill('{"syntheticLayoutDraft":true,"saved":false}');
  shots.push(await capture('module-source-json-draft',registry,'region("Workspace configuration") [data-part="registries"]'));
  await module.fill(originals.module);await source.fill(originals.source);await json.fill(originals.json);
  await button(registry,'Source overrides').click();
  const name=registry.getByLabel('Registered name',{exact:true});
  const uri=registry.getByLabel('Repository URL or local path',{exact:true});
  const sourceOriginals={name:await name.inputValue(),uri:await uri.inputValue()};
  await name.fill('tool-synthetic-unsaved-override');await uri.fill('./synthetic-unused-source');
  shots.push(await capture('source-override-draft',registry,'region("Workspace configuration") [data-part="registries"]'));
  await name.fill(sourceOriginals.name);await uri.fill(sourceOriginals.uri);
  await button(pages,'Modules & sources').click();
  await expect(registry).toHaveCount(0);
  const after=await readFixture('/fixture/workspace-configuration');
  assert.deepEqual(after,before,'Draft inspection/cancellation preserves session, workspace/global files, validator calls and runtime');
  assert.equal(after.validationCalls.length,0,'No module/source validation started');
  return {shots,entryRoute,cancelControl:'Preference order > Cancel',
   otherDraftExit:'Unapplied JSON restored; module/source inputs restored and editor closed, no nonexistent Cancel claimed',
   saved:false,validated:false,installed:false,configurationAndRuntimeUnchanged:true};
 });
 return readFixture('/fixture/workspace-configuration');
}
async function navigationScenes(){
 const page=current.page;
 for(const width of screens){
  await checkpoint(`L01.home.${width}`,async()=>{
   await size(width);await lookAt(looks[0]);await action('view.update',{patch:{
    workSurface:'home',workHomeView:'brief',navExpanded:false}});
   const home=page.locator('.a-adaptive-home');await expect(home.locator('.a-home-filters')).toBeVisible();
   await home.getByRole('searchbox',{name:'Search your work',exact:true}).fill('Alpha');
   return {shots:[await capture('landing-search',home,'.a-adaptive-home')]};
  });
  await checkpoint(`L02.sidebar-status.${width}`,async()=>{
   await action('view.update',{patch:{navExpanded:true,navPinned:width>900,navMode:'status',navAge:'all'}});
   const nav=page.locator('#workspace-navigation');await expect(nav.locator('.a-nav-chat').first()).toBeVisible();
   const shots=[await capture('populated-status',nav,'#workspace-navigation')];
   await button(nav,'App options').click();
   const options=nav.getByRole('group',{name:'App options',exact:true});await expect(options).toBeVisible();
   shots.push(await capture('footer-options',options,'#workspace-navigation group("App options")'));
   await page.keyboard.press('Escape');return {shots,workFailureGroupClaim:false};
  });
  await checkpoint(`L03.search.${width}`,async()=>{
   await action('view.update',{patch:{navExpanded:true,navMode:'browse',navAge:'all'}});
   await button(page.locator('#workspace-navigation'),'Search chats').click();
   if(width<=900&&await button(page,'Close navigation').isVisible())await button(page,'Close navigation').click();
   const area=page.getByRole('region',{name:'Chat search',exact:true});
   const search=area.getByRole('searchbox',{name:'Search chats',exact:true});
   await search.fill('Beta');
   await expect(area.locator('.a-nav-chat-select').first()).toBeVisible();
   const filters=area.locator('summary');await expect(filters).toHaveCount(1);
   if(!await filters.evaluate(el=>el.parentElement.open))await filters.click();
   return {shots:[await capture('expanded-search-filters',area,'region("Chat search")')],resultsSelected:false};
  });
  await checkpoint(`L04.context-menu.${width}`,async()=>{
   await action('view.update',{patch:{navExpanded:true,navMode:'browse',navAge:'all'}});
   const nav=page.locator('#workspace-navigation'),row=nav.locator('.a-nav-chat').filter({
    has:page.getByRole('button',{name:/Details and actions/})}).first();
   await expect(row).toBeVisible();
   const trigger=row.getByRole('button',{name:/Details and actions/});
   await trigger.click();
   const flyout=page.locator('.a-navigation-flyout');await expect(flyout).toBeVisible();
   const shots=[await capture('row-context-details',flyout,'.a-navigation-flyout')];
   await page.keyboard.press('Escape');return {shots,pinRenameArchiveDeleteActivated:false};
  });
 }
 return readFixture('/api/fixture/info');
}

let browser,fatal;
try{
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?
  {args:['--no-zygote','--single-process','--disable-gpu']}:{})});
 for(const [name,scenes] of [['detail',detailScenes],['questions',questionScenes],['approvals',approvalScenes],
  ['workspace',workspaceScenes],['workspace-config',workspaceConfigurationScenes],['navigation',navigationScenes]]){
  if(!report.checkpoints.some(row=>row.phase===name&&selected.has(row.id)))continue;
  let phase,phaseError;
  try{
   phase=await startPhase(name);current=phase;
   const token=name==='detail'?'fixture-detail-token':'fixture-browser-control-token';
   phase.context=await browser.newContext({viewport:{width:1440,height:1080},reducedMotion:'reduce',
    extraHTTPHeaders:{Authorization:'Bearer '+token},serviceWorkers:'allow'});
   await fenceLayoutServiceWorkers(phase.context);
   phase.context.setDefaultTimeout(8000);
   phase.context.on('response',response=>{
    const url=new URL(response.url());
    if(url.origin!==phase.base||!['document','script','stylesheet'].includes(response.request().resourceType()))return;
    const relative=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname).replace(/^\/(?:static\/)?/,'');
    const expected=phase.provenance.staticSha256[relative];
    if(!expected)return; // API/sandbox documents have their own renderer boundary.
    const pending=(async()=>{
     const observed=sha(await response.body());
     const row={path:url.pathname,status:response.status(),wheelPath:'amplifier_web/static/'+relative,
      expected,observed,matches:observed===expected};
     phase.browserAssetReads.push(row);
     if(!row.matches||row.status!==200)phase.assetReadErrors.push(row);
    })().catch(error=>phase.assetReadErrors.push({path:url.pathname,error:errorRecord(error)}));
    phase.assetReadsPending.push(pending);
   });
   phase.context.on('page',page=>page.on('pageerror',error=>{
    phase.pageErrors.push({checkpoint:currentCheckpoint,...errorRecord(error),pageUrl:page.url(),
     frames:page.frames().map(frame=>({url:frame.url(),name:frame.name()})),
     harnessServiceWorkersPolicy:'guarded-registration-fence',harnessInitScripts:'layout-pass2-browser-context.mjs'});
   }));
   phase.guard=await guardContext(phase.context,phase);
   phase.page=await phase.context.newPage();await enter();
   const identityVerified=await checkpoint(`${name}.provenance`,verifyServed);
   if(!identityVerified)throw Error('Served asset/wheel join failed; phase scenes not admitted');
   const observed=await scenes();
   await Promise.all(phase.assetReadsPending);
   await writeFile(join(out,name+'-browser-asset-joins.json'),JSON.stringify({
    wheelSha256:phase.provenance.wheelSha256,reads:phase.browserAssetReads,errors:phase.assetReadErrors,
   },null,2)+'\n');
   assert.deepEqual(phase.assetReadErrors,[],'Retain any later lazy-asset mismatch');
   // Full source contents are synthetic but unnecessary in the public ledger.
   await writeFile(join(out,name+'-fixture-final.json'),JSON.stringify(observed,null,2)+'\n');
   assert.deepEqual(phase.pageErrors,[],'Observed page errors are retained, not waived');
   assert.equal(phase.context.serviceWorkers().length,0,'Layout audit must not start a service worker');
   const row=report.phases.find(row=>row.name===name);row.status='observed';
   row.finalFixtureEvidence=name+'-fixture-final.json';row.pageErrors=phase.pageErrors;
   row.browserAssetJoins=name+'-browser-asset-joins.json';
   row.unconsumedSeedPermits=phase.guard.unconsumed();
  }catch(error){
   phaseError=error;report.phases.push({name,status:'failed',error:errorRecord(error)});
  }finally{
   if(phase){
    // Retain raw errors even if a failed assertion prevents the normal final
    // receipt. Do not filter anonymous sandbox errors or relabel them passed.
    try{
     await writeFile(join(out,name+'-page-errors.json'),JSON.stringify({
      errors:phase.pageErrors,serviceWorkersPolicy:'guarded-registration-fence',
      harnessInitScripts:'layout-pass2-browser-context.mjs',
      attribution:'All application errors retained. Playwright stock opaque-frame initializer replaced after focused causal regression.',
     },null,2)+'\n');
    }catch(error){
     // An evidence writer must never prevent the owned listener from stopping.
     report.phases.push({name,status:'failed',evidence:'page-errors',error:errorRecord(error)});
     phaseError??=error;
    }
    try{
     await finishFixture({label:'Layout pass two '+name,
      close:[['phase browser context',()=>phase.context?.close()]],stop:phase.stop});
     const row=report.phases.find(row=>row.name===name);row.cleanup='confirmed-graceful';
    }catch(error){report.phases.push({name,status:'cleanup-failed',error:errorRecord(error)});phaseError??=error}
    await writeFile(join(out,name+'-fixture.stderr.log'),phase.stderr());
   }
   for(const row of report.checkpoints)if(row.phase===name&&row.status==='unvisited'&&row.reason===pendingReason)
    row.reason=phaseError?'Phase dependency did not complete; see retained phase failure.':'Scene dependency not reached; no visit claimed.';
   current=null;currentCheckpoint=null;await save();
  }
 }
}catch(error){fatal=error;report.fatal=errorRecord(error)}
finally{
 try{await browser?.close()}catch(error){report.browserCleanupError=errorRecord(error);fatal??=error}
 report.finishedAt=new Date().toISOString();
 report.summary={visited:report.checkpoints.filter(row=>row.status==='visited').length,
  failed:report.checkpoints.filter(row=>row.status==='failed').length,
  unvisited:report.checkpoints.filter(row=>row.status==='unvisited').length,
  selectedDenominator:selected.size,
  selectedUnvisited:report.checkpoints.filter(row=>selected.has(row.id)&&row.status==='unvisited').length,
  filteredUnvisited:report.checkpoints.filter(row=>selectable(row)&&!selected.has(row.id)).length,
  fullDenominator:report.checkpoints.length,
  blockedBeforeHost:report.safety.blocked.length,outboundBlocked:report.safety.outbound.length};
 report.status=fatal||report.summary.failed||report.phases.some(row=>['failed','cleanup-failed'].includes(row.status))||
  report.safety.blocked.length||report.summary.selectedUnvisited?'incomplete':'evidence-collected';
 report.visualAcceptance='not-performed';await save();
}
console.log(JSON.stringify({ledger:ledgerPath,status:report.status,summary:report.summary,
 visualAcceptance:report.visualAcceptance}));
if(report.status!=='evidence-collected')process.exitCode=1;