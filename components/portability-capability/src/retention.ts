/** Held-only selected-family proof; no native runtime or history reads. */
export function retentionRequest(context:any, request:any) {
 const sessions=request?.sessions,limit=request?.limit??101;
 if(context?.purpose!=='retention-hide'||!Number.isInteger(limit)||limit<1||limit>101||!Array.isArray(sessions)||!sessions.length||sessions.length>limit||new Set(sessions).size!==sessions.length||sessions.some((s:any)=>typeof s!=='string'||!s.startsWith('ahp-session:/')||s.length>8192||/[\x00-\x1f]/.test(s)))throw Error('At most101 distinct explicit conversations under retention hold required');
 return {context:structuredClone(context),sessions:[...sessions],limit};
}
export function emptyRetention(context:any, request:any, current:any) {
 const args=retentionRequest(context,request),fence=current?.fence??current;
 if(!fence||['fenceId','commandId','purpose','instanceId','dataScope'].some(k=>fence[k]!==context[k])||(fence.state??fence.phase??'held')!=='held')throw Error('Exact live held retention fence required');
 return {coverage:'complete',protected:[],omissions:[]};
}

export function retentionParticipant(participant:any, read:(args:any)=>Promise<any>|any) {
 return {...participant,retentionHide:{version:1 as const},reconcileRelease:async (input:any)=>{if(input.purpose==='retention-hide'&&input.proof?.kind==='admission-refused')throw Error('Retention rollback requires the original live lease');return participant.reconcileRelease(input)},acquire:async (context:any)=>{
  const exact=structuredClone(context),lease=await participant.acquire(exact);if(!lease)return null;if(exact.purpose!=='retention-hide')return lease;let live=true;
  return {...lease,inspectRetentionReferences:async (request:any)=>{if(!live)throw Error('Retention lease is no longer live');const args=retentionRequest(exact,request);const result=await read(args);if(!live)throw Error('Retention lease changed during inspection');return result;},release:async(outcome:any,proof:any)=>{if(!live&&proof?.kind==='admission-refused')throw Error('Retention rollback requires the original live lease');live=false;return lease.release(outcome,proof);}};
 }};
}
