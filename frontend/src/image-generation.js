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
