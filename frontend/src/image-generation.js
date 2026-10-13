import {artifactPublications,publicationMode,publicationImageGeneration} from './artifact-presentation.js';

export function imageJobs(state,messageId){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const images=artifactPublications(state.canvasArtifacts||[],messageId);
 const saved=new Set(images.map(item=>generationBinding(item,messageId,session?.id,session?.execution?.imageGeneration||[])?.requestId).filter(Boolean));
 return (session?.execution?.imageGeneration||[]).filter(row=>row.messageId===messageId&&!saved.has(row.requestId));
}

export function imageJobLabel(job){
 if(job.resultError)return 'Image generated, but could not be displayed. Its saved output is in the work details.';
 if(job.phase==='running')return job.operation==='edit'?'Editing image…':'Creating image…';
 if(job.phase==='completed')return 'Image generated';
 if(job.phase==='error')return 'Image generation did not finish';
 return 'Image generation stopped; check activity for its outcome';
}

function generationBinding(item,messageId,sessionId,jobs){
 const binding=publicationImageGeneration(item,messageId,sessionId);
 if(binding)return binding;
 // Older hosts expose only an initial-version marker and recent work. Do not
 // inherit that row marker into ordinary later versions, or repair a malformed
 // explicit binding with weaker evidence.
 if(item.publication?.imageGeneration!==undefined||item.version!==1||
  item.row.kind!=='image'||item.row.sessionId!==sessionId||
  item.publication?.version!==undefined&&item.publication.version!==item.version||
  item.publication?.messageId&&item.publication.messageId!==messageId)return null;
 return jobs.find(job=>job.messageId===messageId&&job.requestId&&job.requestId===item.row.imageRequestId)||null;
}

// Pending work has a request slot; every resolved output has its own immutable
// artifact/version identity, independent of which siblings are currently visible.
export function imageGalleryEntries(state,messageId,images){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const jobs=(session?.execution?.imageGeneration||[]).filter(job=>job.messageId===messageId);
 const bindings=new Map(images.map(item=>[item,generationBinding(item,messageId,session?.id,jobs)]));
 const verified=images.filter(item=>bindings.get(item));
 const visible=verified.filter(item=>publicationMode(item)==='inline');
 const remaining=new Set(visible),entries=[],requests=new Map();
 for(const job of jobs){
  if(verified.some(item=>bindings.get(item).requestId===job.requestId)&&
   !visible.some(item=>bindings.get(item).requestId===job.requestId))continue;
  const key=job.requestId||job.id;
  const existing=requests.get(key);
  if(existing){existing.job=job;continue}
  const entry={id:'request:'+(job.requestId||job.id),job};requests.set(key,entry);entries.push(entry);
 }
 for(const entry of entries){
  const {job}=entry;
  const matches=visible.filter(item=>remaining.has(item)&&job.requestId&&bindings.get(item).requestId===job.requestId);
  entry.item=matches[0];
  if(entry.item){entry.requestSlot=entry.id;entry.id='artifact:'+entry.item.row.id+':'+entry.item.version}
  if(matches[0])remaining.delete(matches[0]);
 }
 for(const item of remaining){
  const binding=bindings.get(item),key=binding.requestId;
  const existing=requests.get(key);
  if(!existing){
   const entry={id:'artifact:'+item.row.id+':'+item.version,requestSlot:'request:'+key,item};requests.set(key,entry);entries.push(entry);
  }else entries.push({id:'artifact:'+item.row.id+':'+item.version,item});
 }
 return entries;
}

// Reconcile a selected progress placeholder once. Resolved output IDs never
// become request aliases, so removing A cannot reassign selected B's identity.
export function imageGallerySelection(entries,selected){
 return entries.find(entry=>entry.id===selected)||
  entries.find(entry=>entry.requestSlot===selected)||entries[0];
}
