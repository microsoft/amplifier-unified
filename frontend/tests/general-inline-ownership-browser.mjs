// Authored, not executed in the source lane. Synthetic transport exercises the
// actual App, detail owner, GeneralArtifact and contributed Renderer in Chromium.
// This is DOM/lifecycle regression coverage, not producer or deployed acceptance.
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {shellFor} from './shell-host.mjs';

const pixel='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
const thumbnailBytes=Buffer.from(pixel.split(',')[1],'base64');
const thumbnailHash=createHash('sha256').update(thumbnailBytes).digest('hex');
const full=Array.from({length:240},(_,n)=>({id:'m'+n,role:n%10===0?'user':'assistant',text:'Message '+n,createdAt:1000+n}));
const baseSession={id:'chat',sessionKind:'root',title:'Long fixture chat',workspaceId:'project',workspace:'/fixture',status:'idle',historyLoaded:true,
 messages:full.slice(200),messageWindow:{offset:200,total:240,before:'m200'},execution:{nodes:[],turns:[],imageGeneration:[]}};
const publication={messageId:'m210',version:2,chat:'inline',outputId:'saved-doc-output'};
const doc={id:'doc',workspaceId:'project',sessionId:'chat',kind:'markdown',title:'Private editor',revision:2,messageId:'m10',
 publications:[publication],presentationCapabilities:{compact:true,inline:true,canvas:true}};
let state={revision:1,client:{id:'fixture-tab',hostInstanceId:'fixture-host'},settings:{workspace:'/fixture',workspaces:{defaultRoot:'/fixture'}},
 runtime:{available:true},theme:{name:'Fixture',css:''},view:{navPinned:true},sessions:[baseSession,{...baseSession,id:'other',title:'Other chat',messages:[],messageWindow:{offset:0,total:0}}],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedWorkspaceId:'project',selectedSessionId:'chat',
 canvas:{open:false},canvasWorkspace:{views:[]},canvasArtifacts:[doc]};
const fields=['viewId','resourceId','resourceRevision','generation'];
const editorSource=`export default ({React})=>function Editor({host}){
 const [text,setText]=React.useState('');
 const instance=React.useRef(null);
 if(!instance.current)instance.current=++window.editorMounts;
 React.useEffect(()=>()=>{window.editorUnmounts++},[]);
 return React.createElement('section',{'data-editor-instance':instance.current},
  React.createElement('textarea',{'aria-label':'Private inline input',value:text,onChange:event=>{
   setText(event.target.value);host.setDirty(true).catch(error=>{window.dirtyError=error.message});
  }}),
  React.createElement('button',{onClick:()=>{window.cleanSettlement=host.setDirty(false).catch(error=>{window.dirtyError=error.message})}},'Acknowledge clean'));
}`;
let vite,browser,page,holdAround,aroundWaiting=false,rejectDirty=true;
const calls=[],errors=[],thumbnailReads=[];
const emit=async()=>{state={...state,revision:state.revision+1};await page.evaluate(value=>window.emitState(value),state)};
const setMessages=messages=>{state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,messages,messageWindow:{offset:220,total:240,before:messages[0]?.id}}:row)}};
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});
 await vite.listen();
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(initial=>{
  window.editorMounts=0;window.editorUnmounts=0;const sources=[];
  window.EventSource=class extends EventTarget{
   constructor(){super();sources.push(this);setTimeout(()=>{this.onopen?.();this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(initial)}))},0)}
   close(){}
  };
  window.emitState=value=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(value)})));
 },state);
 await page.route('**/fixture-editor.js',route=>route.fulfill({contentType:'text/javascript',body:editorSource}));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/shell')return route.fulfill({json:shellFor(state,()=>{}).data});
  if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[{name:'canvas.present'}]});
  if(path==='/api/outputs/media')return route.fulfill({json:{items:[],overflow:false,mediaRevision:0}});
  if(/^\/api\/outputs\/[^/]+\/image$/.test(path)){
   // This is a synthetic transport, not evidence of server canonical authority.
   // The real-host ten-image journey is generated-images-browser --retained-thumbnails.
   thumbnailReads.push(url);
   const id=path.split('/')[3],row=state.canvasArtifacts.find(row=>row.id===url.searchParams.get('artifactId'));
   assert.ok(row);assert.equal(url.searchParams.get('sessionId'),row.sessionId);
   assert.equal(url.searchParams.get('messageId'),'m210');assert.equal(url.searchParams.get('version'),'1');
   assert.ok(row.publications.some(link=>link.outputId===id&&link.chat==='inline'));
   assert.equal(url.searchParams.get('sha256'),thumbnailHash);
   return route.fulfill({contentType:'image/png',body:thumbnailBytes,headers:{
    'Content-Length':String(thumbnailBytes.length),'X-Amplifier-Image-SHA256':thumbnailHash,'Cache-Control':'no-store'}});
  }
  if(path==='/api/conversation/navigation'){
   const id=url.searchParams.get('messageId');
   if(!id)return route.fulfill({json:{revision:'fixture-index',turns:full.filter(row=>row.role==='user').map(row=>({id:row.id,position:Number(row.id.slice(1))}))}});
   if(url.searchParams.get('window')!=='true')return route.fulfill({json:{id,text:full.find(row=>row.id===id)?.text}});
   aroundWaiting=true;if(holdAround)await holdAround;
   const at=Number(id.slice(1)),start=Math.max(0,at-5);
   return route.fulfill({json:{messages:full.slice(start,start+10),offset:start,total:240,before:'m30',after:'m230',execution:{nodes:[],turns:[]}}});
  }
  if(path==='/api/conversation/detail')return route.fulfill({json:{items:[full[190]],offset:190,sourceOffset:190}});
  if(path.match(/^\/api\/canvas\/views\/[^/]+\/resource$/)){
   const view=state.canvasWorkspace.views.find(row=>row.viewId===path.split('/')[4]);
   assert.ok(view,'Resource must have an exact authorized fixture view');
   const row=state.canvasArtifacts.find(row=>row.id===view.resourceId);
   return route.fulfill({json:{...row,open:true,content:row.kind==='image'?pixel:'# Retained document',...Object.fromEntries(fields.map(field=>[field,view[field]]))}});
  }
  if(path==='/api/actions'&&request.method()==='POST'){
   const envelope=request.postDataJSON(),command=envelope.action==='shell.command'?envelope.args:envelope;
   calls.push(envelope);let result={};
   if(command.action==='canvas.views.inline'){
    if(state.canvasWorkspace.views.filter(view=>view.viewId.startsWith('inline-')).length>=8)
     return route.fulfill({status:409,json:{error:'Close a preview to open another here, or open this artifact in Canvas.'}});
    const id=command.args.resourceId,view={viewId:'inline-'+command.args.previewId,resourceId:id,resourceRevision:'revision-'+command.args.version,generation:1,
     renderer:id==='doc'?'fixture.editor':'builtin.canvas.image',available:true,view:{},dirty:false,
     choices:[{id:id==='doc'?'fixture.editor':'builtin.canvas.image',url:'/fixture-editor.js'}]};
    state={...state,canvasWorkspace:{views:[...state.canvasWorkspace.views,view]}};result=view;
   }
   if(command.action==='canvas.views.dirty'){
    if(rejectDirty&&command.args.dirty){rejectDirty=false;return route.fulfill({status:409,json:{error:'Synthetic dirty declaration rejection'}})}
    state={...state,canvasWorkspace:{views:state.canvasWorkspace.views.map(row=>row.viewId===command.args.viewId?{...row,dirty:command.args.dirty}:row)}};
   }
   if(command.action==='canvas.views.release')state={...state,canvasWorkspace:{views:state.canvasWorkspace.views.filter(row=>row.viewId!==command.args.viewId)}};
   if(command.action==='view.update')state={...state,view:{...state.view,...command.args.patch}};
   if(command.action==='session.select')state={...state,selectedSessionId:command.args.id,canvasWorkspace:{views:[]}};
   if(command.action==='canvas.present'){
    result={publication:{status:'unchanged',accepted:true,chat:command.args.presentation.chat,version:command.args.version,messageId:'m210'},
     canvas:{status:command.args.presentation.canvas==='open'?'opened':'not-requested'}};
    if(command.args.presentation.canvas==='open')state={...state,view:{...state.view,panel:null},canvas:{open:true,id:command.args.id,kind:'markdown'}};
   }
   state={...state,revision:state.revision+1};
   return route.fulfill({json:{accepted:true,result,state}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={},meta={})=>page.evaluate(([name,args,meta])=>window.amplifier.dispatch(name,args,meta),[name,args,meta]);
 const card=page.locator('.a-general-artifact[data-artifact-id="doc"]');
 await card.scrollIntoViewIfNeeded();
 const input=card.getByRole('textbox',{name:'Private inline input'}),clean=card.getByRole('button',{name:'Acknowledge clean'});
 const acknowledgeClean=async()=>{await clean.click();await page.evaluate(()=>window.cleanSettlement)};
 await expect(input).toBeVisible();
 const remember=async()=>input.evaluate(node=>{
  window.retainedEditor=node;node.focus();node.setSelectionRange(2,6);
  return {instance:node.closest('[data-editor-instance]').dataset.editorInstance,value:node.value,selection:[node.selectionStart,node.selectionEnd],unmounts:window.editorUnmounts};
 });
 const same=async saved=>{
  await expect(input).toHaveValue(saved.value);
  assert.deepEqual(await input.evaluate(node=>({same:node===window.retainedEditor,instance:node.closest('[data-editor-instance]').dataset.editorInstance,
   selection:[node.selectionStart,node.selectionEnd],unmounts:window.editorUnmounts})),
   {same:true,instance:saved.instance,selection:saved.selection,unmounts:saved.unmounts});
 };

 // Queue navigation while clean; typing starts after admission but before execution.
 await page.evaluate(()=>{window.leave=window.amplifier.dispatch('session.select',{id:'other'},{before:new Promise(resolve=>{window.resumeLeave=resolve})}).then(()=>null,error=>error.message)});
 await input.fill('PRIVATE QUEUED EDIT');let saved=await remember();
 await expect.poll(()=>page.evaluate(()=>window.dirtyError)).toContain('Synthetic dirty declaration rejection');
 await page.evaluate(()=>window.resumeLeave());
 assert.match(await page.evaluate(()=>window.leave),/inline preview edit/);
 assert.equal(calls.filter(row=>row.action==='session.select').length,0);
 await same(saved);

 // Additive history retains the very same DOM/editor despite an unrelated dirty primary.
 state={...state,canvasWorkspace:{views:[...state.canvasWorkspace.views,{viewId:'primary',resourceId:'other-doc',resourceRevision:'r1',generation:1,dirty:true}]}};
 await emit();
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="m190"]')).toHaveCount(1);await same(saved);
 await acknowledgeClean();
 await same(saved);

 // Collapse also rechecks at queued release execution, not only at the click.
 await page.evaluate(()=>{window.queueBlock=window.amplifier.dispatch('view.update',{patch:{}},{before:new Promise(resolve=>{window.resumeBlock=resolve})})});
 await card.getByRole('button',{name:'Collapse Private editor',exact:true}).click();
 await expect(card.getByRole('button',{name:'Collapse Private editor',exact:true})).toBeDisabled();
 await expect(card.getByRole('status').filter({hasText:'inline preview edit'})).toHaveCount(0);
 await input.fill('PRIVATE DURING COLLAPSE');saved=await remember();
 await page.evaluate(()=>window.resumeBlock());await page.evaluate(()=>window.queueBlock);
 await expect(card.getByRole('status')).toContainText('inline preview edit');await same(saved);
 await acknowledgeClean();

 // An around request started while clean may not commit after newer typing.
 let resumeAround;holdAround=new Promise(resolve=>{resumeAround=resolve});aroundWaiting=false;
 await page.locator('.a-rail-mark[data-turn-id="m30"]').click();await expect.poll(()=>aroundWaiting).toBe(true);
 await input.fill('PRIVATE DURING AROUND');saved=await remember();resumeAround();holdAround=null;
 await expect(page.getByRole('alert').filter({hasText:'inline preview edit'})).toBeVisible();await same(saved);
 await acknowledgeClean();
 // A clean unloaded jump followed by latest clears the saved paging overlay.
 await page.locator('.a-rail-mark[data-turn-id="m30"]').click();
 await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 // Make the publication anchor unloaded, then use the real rail owner.
 setMessages(full.slice(220));await emit();
 await expect(page.locator('[data-message-id="m210"]')).toHaveCount(0);
 await page.locator('.a-rail-mark[data-turn-id="m210"]').click();
 await expect(page.getByRole('button',{name:'Earlier messages',exact:true})).toBeVisible();
 await expect(input).toBeVisible();
 await input.fill('PRIVATE FOCUSED EDIT');saved=await remember();
 for(const name of ['Earlier messages','Later messages','Return to latest']){
  await page.getByRole('button',{name,exact:true}).click();await same(saved);
 }
 await page.locator('.ProseMirror').fill('Send must not discard the focused input');
 await page.locator('.ProseMirror').press('Enter');
 await expect(page.locator('.ProseMirror')).toHaveText('Send must not discard the focused input');
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,0);await same(saved);
 await acknowledgeClean();await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 await expect(input).toHaveCount(0);

 // Automatic same-session source-window replacement also vetoes before unmount.
 setMessages(full.slice(200));await emit();await card.scrollIntoViewIfNeeded();await expect(input).toBeVisible();
 await page.locator('.ProseMirror').fill('');
 await input.fill('PRIVATE AUTOMATIC EDIT');saved=await remember();
 const restoreSession=state.sessions.find(row=>row.id==='chat');
 setMessages(full.slice(220));
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,status:'working',
  collaborationGeneration:{id:'live-generation',terminal:false},approvals:[{id:'live-approval',prompt:'Current host approval',status:'pending'}]}:row)};
 await emit();await same(saved);
 await expect(page.locator('[data-part="composer-primary-action"]')).toHaveAttribute('data-action','conversation.stop');
 await expect(page.locator('.a-approvals')).toContainText('Current host approval');
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,draftAttachments:[{id:'live-attachment',name:'current-host.txt',size:12}]}:row)};
 await emit();await same(saved);
 await expect(page.getByText('current-host.txt',{exact:true})).toBeVisible();
 // Current ownership/read-only metadata gates the composer, not the private
 // renderer. A pinned display window must never pin the former owner state.
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,
  ownership:{status:'blocked',source:'fixture-peer'},historyReadOnlyReason:'Current host read-only reason'}:row)};
 await emit();await same(saved);
 await expect(page.getByRole('region',{name:'Conversation access'})).toContainText('Current host read-only reason');
 await expect(page.locator('.a-composer-fields')).toBeDisabled();
 // Restore write eligibility but keep the newer generation while the anchor is
 // still outside the tail. The actual App send must bind that generation.
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,status:'idle',ownership:undefined,historyReadOnlyReason:undefined}:row)};
 await emit();await same(saved);
 const sendsBefore=calls.filter(row=>row.action==='conversation.send').length;
 await page.locator('.ProseMirror').fill('Current generation discriminator');
 await page.locator('.ProseMirror').press('Enter');
 await expect.poll(()=>calls.filter(row=>row.action==='conversation.send').length).toBe(sendsBefore+1);
 const sent=calls.filter(row=>row.action==='conversation.send').at(-1);
 assert.equal(sent.args.expectedGenerationId,'live-generation');
 assert.deepEqual(sent.args.attachmentIds,['live-attachment']);
 await same(saved);
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...restoreSession,messages:row.messages,messageWindow:row.messageWindow}:row)};
 await emit();
 await acknowledgeClean();await expect(input).toHaveCount(0);

 // Unlink/library-only publication is not authority revocation. The exact dirty
 // component stays until its matching clean declaration succeeds.
 setMessages(full.slice(200));await emit();await card.scrollIntoViewIfNeeded();await expect(input).toBeVisible();
 await input.fill('PRIVATE UNLINKED EDIT');saved=await remember();
 state={...state,canvasArtifacts:[{...doc,publications:[]}]};await emit();await same(saved);
 await acknowledgeClean();await expect(card).toHaveCount(0);
 state={...state,canvasArtifacts:[doc]};await emit();

 // Accepted host revocation is not a user-navigation veto.
 setMessages(full.slice(200));await emit();await card.scrollIntoViewIfNeeded();await expect(input).toBeVisible();
 await input.fill('PRIVATE REVOKED EDIT');saved=await remember();
 state={...state,canvasWorkspace:{views:[]}};await emit();
 await expect(input).toHaveCount(0);await expect(card.getByRole('status').last()).toContainText('access was removed');
 assert.equal(await page.evaluate(()=>window.editorUnmounts),saved.unmounts+1);
 assert.equal(await page.getByText('PRIVATE REVOKED EDIT',{exact:true}).count(),0);

 // Settings cancellation uses real noncredential private editor state.
 await action('view.update',{patch:{panel:'settings',settingsSection:'workspaces',settingsExpanded:['workspaces']}});
 const settingsInput=page.locator('#default-workspace-root');
 await settingsInput.fill('/fixture/private-unsaved-root');
 await settingsInput.evaluate(node=>{window.settingsEditor=node});
 for(const wrapped of [false,true]){
  const args={sessionId:'chat',id:'doc',version:2,presentation:{chat:'compact',canvas:'open'}};
  await page.evaluate(([wrapped,args])=>{window.openResult=window.amplifier.dispatch(wrapped?'shell.command':'canvas.present',wrapped?{action:'canvas.present',args}:args,{id:'settings-open-'+wrapped})},[wrapped,args]);
  await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
  assert.equal((await page.evaluate(()=>window.openResult)).accepted,false);
  assert.equal(await settingsInput.evaluate(node=>node===window.settingsEditor),true);
  await expect(settingsInput).toHaveValue('/fixture/private-unsaved-root');
 }
 await action('canvas.present',{sessionId:'chat',id:'doc',version:2,presentation:{chat:'none',canvas:'unchanged'}});
 await expect(page.getByRole('alertdialog')).toHaveCount(0);
 await expect(settingsInput).toHaveValue('/fixture/private-unsaved-root');
 await page.evaluate(()=>{window.acceptedOpen=window.amplifier.dispatch('canvas.present',{sessionId:'chat',id:'doc',version:2,presentation:{chat:'compact',canvas:'open'}},{id:'accepted-settings-open'})});
 await page.getByRole('button',{name:'Discard changes',exact:true}).click();await page.evaluate(()=>window.acceptedOpen);
 assert.equal(calls.filter(row=>row.id==='accepted-settings-open').length,1);
 assert.equal(calls.filter(row=>row.id?.startsWith('settings-open-')).length,0);

 // Retained generation slots, including explicit selection, do not follow job pruning.
 const beforeGallery=calls.length;
 setMessages(full.slice(200));state={...state,view:{...state.view,panel:null},canvas:{open:false},canvasWorkspace:{views:[]}};
 const generated=(id,requestId)=>({id,workspaceId:'project',sessionId:'chat',kind:'image',title:'Generated '+id,revision:1,
  versions:[{version:1,kind:'image',body:{$resource:'image-body-'+id},outputBinding:{
   outputId:'out-'+id,outputVersion:1,sessionId:'chat',sha256:thumbnailHash}}],
  publications:[{messageId:'m210',version:1,chat:'inline',outputId:'out-'+id,imageGeneration:{verified:true,requestId,operation:'generate',parentId:null}}]});
 state={...state,canvasArtifacts:[generated('c','unrelated'),generated('a','shared'),generated('b','shared')],sessions:state.sessions.map(row=>row.id==='chat'?{...row,execution:{...row.execution,imageGeneration:[
  {id:'job-c',requestId:'unrelated',messageId:'m210',operation:'generate',phase:'completed'},
  {id:'job-ab',requestId:'shared',messageId:'m210',operation:'generate',phase:'completed'}
 ]}}:row)};await emit();
 const gallery=page.locator('.a-inline-gallery');await gallery.scrollIntoViewIfNeeded();
 await gallery.getByRole('button',{name:'Image 3: Generated b',exact:true}).click();
 await expect(gallery.getByRole('button',{name:'Image 3: Generated b',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(gallery.locator('.a-inline-artifact-heading strong')).toHaveText('Generated b');
 for(const chat of ['compact','none']){
  state={...state,canvasArtifacts:state.canvasArtifacts.map(row=>row.id==='a'?{...row,publications:row.publications.map(link=>({...link,chat}))}:row)};
  await emit();
  await expect(gallery.locator('.a-inline-artifact-heading strong')).toHaveText('Generated b');
  await expect(gallery.getByRole('button',{name:'Image 2: Generated b',exact:true})).toHaveAttribute('aria-pressed','true');
 }
 state={...state,sessions:state.sessions.map(row=>row.id==='chat'?{...row,execution:{...row.execution,imageGeneration:[]}}:row)};await emit();
 await expect(gallery.locator('.a-inline-artifact-heading strong')).toHaveText('Generated b');
 await expect(gallery.getByRole('button',{name:'Image 2: Generated b',exact:true})).toHaveAttribute('aria-pressed','true');
 assert.equal(await page.locator('.a-general-artifact').count(),0,'No duplicate ordinary/generated appearance');
 await gallery.getByRole('button',{name:'Close preview of Generated b',exact:true}).click();
 await expect(gallery.getByRole('button',{name:'Show preview',exact:true})).toBeVisible();
 await expect(gallery.getByText('Preview access was removed by the host.',{exact:true})).toHaveCount(0);
 await gallery.getByRole('button',{name:'Show preview',exact:true}).click();
 // The original selected-view revocation negative remains. Retained thumbnails
 // are independently authorized by saved publications, not by those admissions.
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await expect(gallery.locator('.a-gallery-thumbnail img')).toHaveCount(2);
 assert.ok(calls.slice(beforeGallery).filter(row=>row.action==='canvas.views.inline').every(row=>['b','c'].includes(row.args.resourceId)),
  'The unselected a thumbnail did not obtain a Canvas admission');
 const oldViews=state.canvasWorkspace.views;
 const readsBeforeRelease=thumbnailReads.length;
 state={...state,canvasWorkspace:{views:[]}};await emit();
 await expect(gallery.locator('.a-inline-image img')).toHaveCount(0);
 await expect(gallery.locator('.a-gallery-thumbnail img')).toHaveCount(2);
 await expect(gallery.getByRole('button',{name:'Use in a follow-up',exact:true})).toHaveCount(0);
 await expect(gallery.getByRole('button',{name:'Download image',exact:true})).toHaveCount(0);
 await expect(gallery.getByRole('status')).toContainText('access was removed');
 const admissions=calls.filter(row=>row.action==='canvas.views.inline').length;
 state={...state,canvasWorkspace:{views:oldViews}};await emit();
 await expect(gallery.locator('.a-inline-image img')).toHaveCount(0);
 await expect(gallery.locator('.a-gallery-thumbnail img')).toHaveCount(2);
 assert.equal(thumbnailReads.length,readsBeforeRelease,'Individual view release does not invalidate or refetch retained thumbnails');
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,admissions);
 // Changing the publication's own scope does clear its thumbnail. Remounting a
 // rail later must read anew; no old Canvas view may restore cached pixels.
 state={...state,selectedSessionId:'other',canvasArtifacts:[],canvasWorkspace:{views:[]}};await emit();
 await expect(gallery).toHaveCount(0);
 assert.deepEqual(errors,[]);
}finally{await browser?.close();await vite?.close()}
