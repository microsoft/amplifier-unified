import type {FenceContext,Json} from './index.js';
export type AdmissionAbortProof={kind:'distribution-admission-abort';verified:true;purpose:'distribution-update';receiptId:string;commandId:string;fenceId:string;instanceId:string;dataScope:string};
export type AdmissionAbortContext=Readonly<FenceContext>&{proof:AdmissionAbortProof};
export type AdmissionAbortReceipt={ownerId:string;status:'released'|'not-acquired';receiptId:string;commandId:string;fenceId:string;instanceId:string;dataScope:string};
const binding=['commandId','fenceId','instanceId','dataScope'] as const;
const token=(value:unknown)=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f]/.test(value);
const exact=(value:unknown,keys:readonly string[]):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
export function abortProof(value:AdmissionAbortContext):AdmissionAbortProof{
 const p=value.proof;if(!exact(value,[...binding,'purpose','proof'])||value.purpose!=='distribution-update'||!exact(p,[...binding,'purpose','kind','verified','receiptId'])||p.kind!=='distribution-admission-abort'||p.verified!==true||p.purpose!==value.purpose||!token(p.receiptId)||binding.some(k=>!token(value[k])||p[k]!==value[k]))throw Error('Exact distinct authenticated portability admission-abort proof required');
 return {...p} as AdmissionAbortProof;
}
export function abortReceipt(value:unknown,context:Readonly<FenceContext>,ownerId:string):AdmissionAbortReceipt{
 if(!exact(value,[...binding,'ownerId','status','receiptId'])||value.ownerId!==ownerId||!token(value.receiptId)||!['released','not-acquired'].includes(value.status)||binding.some(k=>value[k]!==context[k]))throw Error('Exact original portability subowner abort receipt required');
 return {...value} as AdmissionAbortReceipt;
}
