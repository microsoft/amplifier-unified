import {publicationMode} from './artifact-presentation.js';

export function imageJobs(state,messageId){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const saved=new Set((state.canvasArtifacts||[]).filter(row=>row.sessionId===session?.id&&row.messageId===messageId).map(row=>row.imageRequestId));
 return (session?.execution?.imageGeneration||[]).filter(row=>row.messageId===messageId&&!saved.has(row.requestId));
}

export function imageJobLabel(job){
 if(job.resultError)return 'Image generated, but could not be displayed. Its saved output is in the work details.';
 if(job.phase==='running')return job.operation==='edit'?'Editing image…':'Creating image…';
 if(job.phase==='completed')return 'Image generated';
 if(job.phase==='error')return 'Image generation did not finish';
 return 'Image generation stopped; check activity for its outcome';
}

// Keep a request in the same gallery slot as its receipt arrives. Execution
// order, not completion order, determines the thumbnail order.
export function imageGalleryEntries(state,messageId,images){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const jobs=(session?.execution?.imageGeneration||[]).filter(job=>job.messageId===messageId);
 // imageRequestId is a host receipt projection, but a free-standing/unmatched
 // identifier is not generation evidence. Only this turn's projected work binds
 // it to a gallery; ordinary images keep the general artifact shell.
 const verified=images.filter(item=>item.row.kind==='image'&&
  item.row.sessionId===session?.id&&jobs.some(job=>job.requestId&&job.requestId===item.row.imageRequestId));
 const visible=verified.filter(item=>publicationMode(item)==='inline');
 const remaining=new Set(visible),entries=[],requests=new Map();
 for(const job of jobs){
  if(verified.some(item=>item.row.imageRequestId===job.requestId)&&
   !visible.some(item=>item.row.imageRequestId===job.requestId))continue;
  const key=job.requestId?JSON.stringify([job.operation,job.requestId]):job.id;
  const existing=requests.get(key);
  if(existing){existing.job=job;continue}
  const entry={id:'job:'+job.id,job};requests.set(key,entry);entries.push(entry);
 }
 for(const entry of entries){
  const {job}=entry;
  const matches=visible.filter(item=>remaining.has(item)&&job.requestId&&item.row.imageRequestId===job.requestId);
  entry.item=matches[0];
  if(matches[0])remaining.delete(matches[0]);
 }
 for(const item of remaining)entries.push({id:'artifact:'+item.row.id+':'+item.version,item});
 return entries;
}
