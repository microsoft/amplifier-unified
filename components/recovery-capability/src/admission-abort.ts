import type {FenceContext} from './types.js';

export type AdmissionBinding=Pick<FenceContext,'commandId'|'fenceId'|'instanceId'|'dataScope'>;
export type AdmissionAbortProof=AdmissionBinding&{kind:'distribution-admission-abort';verified:true;purpose:'distribution-update';receiptId:string};
export type AdmissionAbortReceipt=AdmissionBinding&{ownerId:string;status:'released'|'not-acquired';receiptId:string};
export const exact=(value:unknown):string=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

/** Only the authenticated Host participant port supplies this distinct proof. */
export function admissionAbortProof(context:Readonly<FenceContext>,value:AdmissionAbortProof):AdmissionAbortProof {
 const keys=['commandId','dataScope','fenceId','instanceId','kind','purpose','receiptId','verified'];
 if(context.purpose!=='distribution-update'||!value||Object.keys(value).sort().join(',')!==keys.join(',')||value.kind!=='distribution-admission-abort'||value.verified!==true||value.purpose!=='distribution-update'||['commandId','fenceId','instanceId','dataScope'].some(key=>value[key as keyof AdmissionBinding]!==context[key as keyof AdmissionBinding]))throw Error('Exact distinct authenticated admission abort proof required');
 if(typeof value.receiptId!=='string'||!value.receiptId||value.receiptId.length>200||/[\x00-\x1f]/.test(value.receiptId))throw Error('Bounded admission abort receipt identity required');
 return structuredClone(value);
}
