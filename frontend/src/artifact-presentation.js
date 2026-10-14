// Presentation consumes host identities, never content hashes or renderer grants.
const inlineKinds=new Set(['image','html','babylon','mermaid','dot']);
const nativeKinds=new Set(['markdown','text','code','json','jsonl','image','html','babylon','mermaid','dot','pdf']);
const mediaKinds=new Set(['gif','mp4']);
const interactiveKinds=new Set(['canvas-app','mcp-app','browser','a2ui']);
export const followsLatest=kind=>kind==='canvas-app'||kind==='mcp-app';
const modes=new Set(['none','compact','inline']);

export function publicationMode(item){
 return modes.has(item.publication?.chat)?item.publication.chat:inlineKinds.has(item.row.kind)?'inline':'compact';
}

// This is a trusted host projection, not a client-supplied assertion or visual
// verification. Require its exact publication/output binding before consuming it.
export function publicationImageGeneration(item,messageId,sessionId){
 const link=item.publication,binding=link?.imageGeneration;
 if(item.row.kind!=='image'||item.row.sessionId!==sessionId||
  link?.messageId!==messageId||link.version!==item.version||
  !Number.isSafeInteger(link.version)||link.version<1||
  typeof link.outputId!=='string'||!link.outputId||
  binding?.verified!==true||typeof binding.requestId!=='string'||!binding.requestId||
  !['generate','edit'].includes(binding.operation)||
  !(binding.parentId===null||typeof binding.parentId==='string'&&binding.parentId))return null;
 return binding;
}

// A retained thumbnail is independent of a Canvas view. Scope and saved-body
// identity invalidate derived pixels; they are not authority supplied to the host.
export function retainedThumbnailTuple(item,messageId,state){
 const {row,version,publication}=item;
 if(state.selectedSessionId!==row.sessionId||row.workspaceId!=null&&state.selectedWorkspaceId!==row.workspaceId||publicationMode(item)!=='inline'||
  !publicationImageGeneration(item,messageId,row.sessionId))return null;
 const entries=row.versions||[];
 const saved=entries.find(entry=>entry.version===version)||
  (!entries.length&&version===(row.revision||1)?row:null);
 if(!saved)return null;
 const binding=saved.outputBinding;
 if(binding!==undefined&&(!binding||binding.outputId!==publication.outputId||binding.sessionId!==row.sessionId||
  !Number.isSafeInteger(binding.outputVersion)||binding.outputVersion<1||!/^[a-f0-9]{64}$/.test(binding.sha256)))return null;
 const query={sessionId:row.sessionId,artifactId:row.id,version,messageId,...(binding?{sha256:binding.sha256}:{})};
 const identity=JSON.stringify([state.client?.hostInstanceId,state.client?.id??state.client?.clientId,
  state.selectedWorkspaceId,query,publication,saved.body??null,saved.contentResource??null,binding??null]);
 return {identity,url:`/api/outputs/${encodeURIComponent(publication.outputId)}/image?`+new URLSearchParams(query),sha256:binding?.sha256};
}

export function artifactPublications(rows,messageId){
 return rows.filter(row=>row.kind!=='settings').flatMap(row=>{
  const links=row.publications??[{messageId:row.messageId,version:row.app?.revision||row.revision||1}];
  return links.filter(link=>link.messageId===messageId&&Number.isSafeInteger(link.version)&&link.version>0)
   .map(publication=>({row,version:publication.version,publication}));
 });
}

export function presentationCapabilities(item){
 const kind=item.row.kind,caps=item.row.presentationCapabilities;
 const legacy=nativeKinds.has(kind);
 // Interactive mounts require the host's qualified native lifecycle. A kind,
 // renderer package, or grant alone is never admission for a live app.
 const mountable=legacy||mediaKinds.has(kind)||interactiveKinds.has(kind);
 const inline=mountable&&(caps?caps.inline===true:legacy);
 return {compact:caps?caps.compact===true:true,inline,canvas:caps?caps.canvas===true:true,
  reason:inline?'':caps?.reason||(mountable?'Inline preview is unavailable on this host.':'Inline lifecycle qualification is pending for this artifact. Open it in Canvas.')};
}

// This consumes only the admitted view, never the primary Canvas app/binding.
// An explicit numbered selection is read-only even when it equals Latest.
export function artifactViewCanvas(body,view){
 return {...body,...(view.app?{app:view.app}:{}),
  ...Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(field=>[field,view[field]])),
  ...(followsLatest(body.kind)?{selectedVersion:view.selectedVersion,readOnlyVersion:view.selectedVersion!=null}:{}),
  view:view.view,renderReports:view.renderReports,document:view.document,interaction:view.interaction,events:view.events};
}

export function requireArtifactBody(body,target){
 const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
 const text=value=>typeof value==='string'&&value.length>0;
 const exact=body&&text(body.viewId)&&text(body.resourceRevision)&&
  Number.isSafeInteger(body.generation)&&body.generation>=0&&(!target||
   body.id===target.resourceId&&['viewId','resourceRevision','generation'].every(field=>body[field]===target[field]));
 const pointer=body?.contentResource;
 const deferred=exact&&['html','babylon','pdf'].includes(body.kind)&&object(pointer)&&
  /^[a-f0-9]{64}$/.test(pointer.$resource)&&Number.isSafeInteger(pointer.bytes)&&pointer.bytes>0;
 const reference=body?.mediaReference,item=reference?.item;
 const media=object(reference)&&text(reference.messageId)&&reference.messageId.length<=200&&object(item)&&
  text(item.id)&&item.id.length<=100&&item.sessionId===body.sessionId&&
  Number.isSafeInteger(item.version)&&item.version>0&&/^[a-f0-9]{64}$/.test(item.sha256)&&
  typeof item.title==='string'&&text(item.filename)&&object(item.media)&&
  item.media.mimeType===(body.kind==='mp4'?'video/mp4':'image/gif')&&
  (body.kind!=='gif'||item.media.loopCount===null||Number.isSafeInteger(item.media.loopCount)&&item.media.loopCount>=0&&item.media.loopCount<=65535);
 const valid=body&&(body.kind==='canvas-app'?object(body.app)&&object(body.app.manifest)&&
    Number.isSafeInteger(body.app.revision)&&body.app.revision>0:
   body.kind==='mcp-app'?object(body.mcp)&&text(body.mcp.serverId)&&text(body.mcp.configuration):
   body.kind==='browser'?text(body.url):
   body.kind==='a2ui'?object(body.surface)&&Array.isArray(body.surface.components)&&
    body.surface.components.some(row=>row.id===body.surface.root&&object(row.component)):
   mediaKinds.has(body.kind)?media:
   nativeKinds.has(body.kind)&&(deferred||typeof body.content==='string'&&pointer===undefined));
 if(!body||!text(body.id)||!valid||target&&!exact){
  throw Error('Saved artifact source is unavailable. Retry the preview or open it in Canvas.');
 }
 return body;
}

export function artifactOverrideKey(state,item,messageId){
 return JSON.stringify([state.client?.hostInstanceId??null,item.row.sessionId,messageId,item.row.id,item.version]);
}

// Admission receipts can precede the corresponding host state. An older state
// is not revocation, but it is not permission to display a body either.
export function inlineViewAccess(record,state){
 if(!record||record.revoked)return {status:record?'revoked':'pending'};
 if(record.hostInstanceId!==state.client?.hostInstanceId||record.clientId!==(state.client?.id??state.client?.clientId))return {status:'revoked'};
 // A deliberate close or failed-load cleanup is not host access revocation.
 // Neither permits displaying old bytes; a retry must obtain a fresh admission.
 if(record.localRelease)return {status:'closed'};
 const view=(state.canvasWorkspace?.views||[]).find(value=>
  ['viewId','resourceId','resourceRevision','generation'].every(field=>value[field]===record.view[field]));
 if(view)return {status:'live',view};
 return {status:record.seen||Number.isSafeInteger(record.stateRevision)&&state.revision>=record.stateRevision?'revoked':'pending'};
}

export function canvasOpenCommand(item,messageId,actions=[]){
 const names=new Set(actions.map(row=>row.name||row.action));
 const {row,version}=item;
 if(names.has('canvas.present'))return {action:'canvas.present',args:{
  sessionId:row.sessionId,id:row.id,version,messageId,
  // A private expansion is not a publication edit.
  presentation:{chat:publicationMode(item),canvas:'open'}
 }};
 // Explicit compatibility for hosts predating canvas.present. Never retry a
 // failed mutation under this different action.
 return {action:'canvas.select',args:{id:row.id,version}};
}

export function canvasOpenNotice(receipt){
 if(receipt?.accepted===false)return receipt.error||'The Canvas request was not accepted.';
 const publication=receipt?.result?.publication,outcome=receipt?.result?.canvas;
 const saved=publication&&!['published','unchanged'].includes(publication.status)?
  publication.reason||`Publication was not confirmed (${publication.status||'unknown'}).`:'';
 const opened=outcome?.status==='opened'?'':outcome?.reason||
  (outcome?.status?`Canvas was not opened (${outcome.status}).`:'Canvas opening could not be confirmed.');
 return [saved,opened].filter(Boolean).join(' ');
}

// Proposed explicit compact-summary binding: publication.outputId identifies
// the retained registry item for this exact publication version. A row-level
// outputId or equal body/hash is insufficient when versions differ.
export function publicationOutputIds(rows,messageId){
 return new Set(artifactPublications(rows,messageId).map(item=>item.publication.outputId)
  .filter(id=>typeof id==='string'&&id.length>0));
}