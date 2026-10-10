import {createHash} from 'node:crypto';
import type {Job,Json} from './types.js';
import type {ConversationPresentationPort,PresentationReadiness,PresentationReview,PresentationReviewPage,PresentationReceipt,PresentationRebuildReceipt} from './presentation-types.js';
export const isPresentation=(operation:string)=>operation.startsWith('recovery.presentation.');
const record=(v:any)=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const text=(v:any,max=256)=>typeof v==='string'&&v.length>0&&v.length<=max&&!/[\x00-\x1f]/.test(v);
const hash=(v:any)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const natural=(v:any)=>Number.isSafeInteger(v)&&v>=0;
const exactKeys=(v:any,keys:string[])=>record(v)&&Object.keys(v).every(k=>keys.includes(k));
const reviewKeys=['reviewId','reviewHash','commandId','operation','selectionHash','count','bytes','expiresAt','observedRevision','resetCommandId','preservesCanonical','preservesAuthority','eligibility'];
const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):record(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const equal=(a:any,b:any)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
export const presentationCommand=(account:string,command:string,kind:string)=>'recovery-presentation:'+createHash('sha256').update(JSON.stringify([account,command,kind])).digest('hex');
export function presentationPort(port:ConversationPresentationPort|undefined){
 if(!port)return undefined;
 if(port.presentationReset?.version!==1||port.presentationReset.preservesCanonical!==true||port.presentationReset.preservesAuthority!==true||['prepareConversationPresentation','readConversationPresentationReview','applyConversationPresentation','conversationPresentationReceipt','reconcileConversationPresentation','inspectConversationPresentation','rebuildConversationPresentation','reconcileConversationPresentationRebuild'].some(k=>typeof (port as any)[k]!=='function'))throw Error('Complete negotiated host presentation port required');
 return port;
}
export class PresentationResets {
 constructor(readonly port:ConversationPresentationPort){}
 async inspect():Promise<PresentationReadiness>{
  const v=await this.port.inspectConversationPresentation();
  if(!exactKeys(v,['enabled','ready','revision','source','projectionRevision','phase','reason'])||typeof v.enabled!=='boolean'||typeof v.ready!=='boolean'||!natural(v.revision)||!['disabled','checking','ready','applying','unknown','rebuilding'].includes(v.phase)||v.source!==undefined&&!text(v.source)||v.projectionRevision!==undefined&&!natural(v.projectionRevision)||v.reason!==undefined&&!text(v.reason))throw Error('Invalid host presentation readiness');
  return structuredClone(v);
 }
 private reviewPage(job:Job,value:PresentationReviewPage,limit=50):PresentationReviewPage{
  const p=job.presentation!,v=value;
  if(!exactKeys(v,[...reviewKeys,'items','nextCursor'])||!text(v.reviewId)||!hash(v.reviewHash)||v.commandId!==p.prepareCommandId||v.operation!==job.args.operation&&job.operation.endsWith('.prepare')||!hash(v.selectionHash)||v.count!==p.sessionIds.length||!natural(v.bytes)||v.bytes>8*1024*1024||!natural(v.expiresAt)||!natural(v.observedRevision)||v.preservesCanonical!==true||v.preservesAuthority!==true||v.eligibility!=='requires-selected-validation'||v.resetCommandId!==p.resetCommandId||!Array.isArray(v.items)||v.items.length>limit||!v.items.length||v.nextCursor!==null&&!text(v.nextCursor,16384)||Buffer.byteLength(JSON.stringify(v))>270336)throw Error('Invalid bounded host presentation review');
  const seen=new Set<string>();for(const row of v.items){if(!exactKeys(row,['session','title','priorHidden','targetHidden'])||!p.sessionIds.includes(row.session)||seen.has(row.session)||typeof row.title!=='string'||row.title.length>16384||typeof row.priorHidden!=='boolean'||typeof row.targetHidden!=='boolean')throw Error('Invalid presentation review row');seen.add(row.session);}
  const metadata=Object.fromEntries(reviewKeys.filter(k=>Object.hasOwn(v,k)).map(k=>[k,(v as any)[k]])) as unknown as PresentationReview;
  if(p.review&&!reviewKeys.every(k=>equal((p.review as any)[k],(metadata as any)[k])))throw Error('Immutable presentation review changed');
  return structuredClone(v);
 }
 async page(job:Job,args:Json){
  const p=job.presentation;if(!p?.review)throw Error('Exact presentation review required');
  const value=this.reviewPage(job,await this.port.readConversationPresentationReview({reviewId:p.review.reviewId,...(args.cursor?{cursor:args.cursor}:{}),limit:args.limit??25},p.actor),args.limit??25);
  return {jobId:job.id,kind:'conversation-presentation',coverage:'explicit-conversation-presentation',previewHash:value.reviewHash,...value};
 }
 private receipt(job:Job,value:PresentationReceipt):PresentationReceipt{
  const p=job.presentation!,r=p.review!,v=value,e=v?.effect,q=v?.projection;
  if(!exactKeys(v,['commandId','effect','projection'])||v.commandId!==p.effectCommandId||!exactKeys(q,['status','source','revision'])||!['pending','ready','unknown'].includes(q.status)||!text(q.source)||q.revision!==undefined&&!natural(q.revision)||!exactKeys(e,['commandId','operation','part','reviewId','reviewHash','selectionHash','count','previousRevision','revision','status','executed','preservesCanonical','preservesAuthority','reason'])||e.commandId!==p.effectCommandId||e.operation!==r.operation||e.part!=='conversation-presentation'||e.reviewId!==r.reviewId||e.reviewHash!==r.reviewHash||e.selectionHash!==r.selectionHash||e.count!==r.count||!natural(e.previousRevision)||e.preservesCanonical!==true||e.preservesAuthority!==true||!['completed','refused','unknown'].includes(e.status)||e.executed!==undefined&&e.executed!==false||e.reason!==undefined&&!text(e.reason,512)||e.revision!==undefined&&!natural(e.revision))throw Error('Exact host presentation receipt required');
  // The host compares each selected row, not the global revision captured at
  // review time. A disjoint visibility change must not invalidate reset or undo.
  if(e.status==='completed'&&(e.revision!==e.previousRevision+1||e.executed!==undefined)||e.status==='refused'&&e.executed!==false)throw Error('Presentation receipt lacks conclusive effect evidence');
  if(q.status==='ready'&&!natural(q.revision))throw Error('Presentation projection evidence differs');
  if(p.receipt&&(q.source!==p.receipt.projection.source||p.receipt.effect.status!=='unknown'&&!equal(e,p.receipt.effect)))throw Error('Original presentation effect or source changed');
  return structuredClone(v);
 }
 private settle(job:Job,value:PresentationReceipt){
  const receipt=this.receipt(job,value);job.presentation!.receipt=receipt;
  const e=receipt.effect;
  // Marker commit and derived discovery readiness are different facts. Never
  // hold global execution while catalog I/O/reconstruction is pending. Exact-ID
  // access remains available; only the original visibility receipt is observed.
  job.terminalState=e.status==='completed'?'succeeded':e.status==='refused'?'refused':undefined;
  job.result={coverage:'explicit-conversation-presentation',receipt,automaticResume:false,replayed:false};
  job.reason=e.status==='unknown'?'presentation-outcome-unknown-no-replay':e.status==='refused'?'presentation-selection-refused':receipt.projection.status==='ready'?undefined:'presentation-saved-projection-pending';
 }
 private settleRebuild(job:Job,v:PresentationRebuildReceipt){
  if(!exactKeys(v,['commandId','status','revision','source','count','bytes','markerCount','manifestHash','phase','targetCheckpoint'])||v.commandId!==job.presentation!.effectCommandId||!['completed','unknown'].includes(v.status)||v.revision!==job.args.expectedRevision||!text(v.source)||!natural(v.count)||v.count>100000||!natural(v.bytes)||v.bytes>64*1024*1024||!natural(v.markerCount)||v.markerCount>v.count||v.manifestHash!==''&&!hash(v.manifestHash)||!['capturing','metadata','projection','ready','unknown'].includes(v.phase)||v.targetCheckpoint!==undefined&&!natural(v.targetCheckpoint)||v.status==='completed'&&(v.phase!=='ready'||!hash(v.manifestHash)||!natural(v.targetCheckpoint)))throw Error('Invalid exact host presentation rebuild receipt');
  job.presentation!.rebuildReceipt=structuredClone(v);job.terminalState=v.status==='completed'?'succeeded':undefined;
  job.result={coverage:'derived-conversation-presentation',receipt:structuredClone(v),automaticResume:false,replayed:false};
  job.reason=v.status==='completed'?undefined:'presentation-rebuild-unconfirmed-no-replay';
 }
 private refused(job:Job,error:any){
  // Only the host's apply/rebuild invocation may prove a pre-admission refusal.
  // A malformed receipt or later inspection error cannot erase a committed effect.
  if(Number.isInteger(error?.code)&&error.code<=-32000&&error.code>=-32099&&error?.data?.executed===false){job.presentation!.preflightRefused=true;job.terminalState='refused';job.reason='presentation-host-preflight-refused';}
  else{job.terminalState=undefined;job.reason='presentation-outcome-unknown-no-replay';}
 }
 async perform(job:Job,changed:()=>void){
  const p=job.presentation!;changed();
  if(job.operation==='recovery.presentation.prepare'){
   try{
    const page=this.reviewPage(job,await this.port.prepareConversationPresentation({commandId:p.prepareCommandId!,operation:job.args.operation,selection:{kind:'explicit',sessionIds:p.sessionIds},...(p.resetCommandId?{resetCommandId:p.resetCommandId}:{})},p.actor));
    p.review=Object.fromEntries(reviewKeys.filter(k=>Object.hasOwn(page,k)).map(k=>[k,(page as any)[k]])) as unknown as PresentationReview;
    job.preview={kind:'conversation-presentation',coverage:'explicit-conversation-presentation',previewHash:page.reviewHash,...p.review};job.terminalState='prepared';
   }catch{job.terminalState='refused';job.reason='presentation-review-unavailable-no-visibility-change';}
   changed();return;
  }
  if(job.operation==='recovery.presentation.rebuild'){
   let value:PresentationRebuildReceipt;
   try{value=await this.port.rebuildConversationPresentation({commandId:p.effectCommandId!,expectedRevision:job.args.expectedRevision},p.actor);}catch(error){this.refused(job,error);changed();return;}
   this.settleRebuild(job,value);changed();return;
  }
  if(!p.review||!p.effectCommandId)throw Error('Exact presentation review required');
  let value:PresentationReceipt;
  try{value=await this.port.applyConversationPresentation({commandId:p.effectCommandId,reviewId:p.review.reviewId,reviewHash:p.review.reviewHash},p.actor);}catch(error){this.refused(job,error);changed();return;}
  this.settle(job,value);changed();
 }
 async reconcile(job:Job,changed:()=>void){
  const p=job.presentation!;
  if(job.operation==='recovery.presentation.prepare'){job.terminalState=p.review?'prepared':'refused';changed();return;}
  if(p.preflightRefused){job.terminalState='refused';changed();return;}
  if(!p.effectCommandId)return;
  // Passive original receipt reads only. Rebuilding the derived catalog is a
  // separate explicitly authorized command, never an automatic mutation retry.
  if(job.operation==='recovery.presentation.rebuild'){
   const value=await this.port.reconcileConversationPresentationRebuild(p.effectCommandId,p.actor);if(value)this.settleRebuild(job,value);
  }else if(p.review){const value=await this.port.reconcileConversationPresentation(p.effectCommandId,p.actor);if(value)this.settle(job,value);}
  changed();
 }
}
