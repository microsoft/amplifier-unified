/** Stateless Host13 wire binding; durable authority remains in the Python owner. */
export function admissionAbortRequest(context:any,ownerId:string){
 const fields=['commandId','fenceId','instanceId','dataScope'];
 const token=(v:any)=>typeof v==='string'&&v.length>0&&v.length<=200&&!/[\x00-\x1f]/.test(v);
 if(context?.purpose!=='distribution-update'||!token(ownerId)||fields.some(k=>!token(context[k])))throw Error('Exact distribution admission abort context required');
 const proof=context.proof;
 if(!proof||Object.keys(proof).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,kind,purpose,receiptId,verified'||proof.kind!=='distribution-admission-abort'||proof.verified!==true||proof.purpose!=='distribution-update'||!token(proof.receiptId)||fields.some(k=>proof[k]!==context[k]))throw Error('Distinct authenticated admission abort proof required');
 return {...Object.fromEntries(fields.map(k=>[k,context[k]])),purpose:'distribution-update',ownerId,proof:structuredClone(proof)};
}
export function admissionAbortReceipt(value:any,input:any){
 if(!value||Object.keys(value).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,ownerId,receiptId,status'||!['released','not-acquired'].includes(value.status)||typeof value.receiptId!=='string'||!value.receiptId||value.receiptId.length>200||/[\x00-\x1f]/.test(value.receiptId)||['commandId','fenceId','instanceId','dataScope','ownerId'].some(k=>value[k]!==input[k]))throw Error('Exact conclusive owner admission abort receipt required');
 return structuredClone(value);
}
export async function forwardAdmissionAbort(request:(method:string,params:any)=>Promise<any>,context:any,ownerId:string,method='quiescence.abortAdmission'){
 const input=admissionAbortRequest(context,ownerId);
 if((await request('initialize',{})).quiescence?.admissionAbort?.version!==1)throw Error('Installed owner admission abort contract unavailable');
 return admissionAbortReceipt(await request(method,input),input);
}
