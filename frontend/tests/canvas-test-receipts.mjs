// Browser-test receipt barrier. Reads the observer's original promise once;
// it never replaces a response, clears the audit, or retries an action.
import assert from 'node:assert/strict';
export async function settleCanvasReceipt(entry){
 assert.ok(entry,'The exact action response must have an observer entry');
 if(entry.row.receiptTimeout)throw Error(entry.row.receiptTimeout);
 let timer;
 try{
  await Promise.race([entry.settled,new Promise((_,reject)=>{timer=setTimeout(()=>{
   entry.row.receiptTimeout=`Action receipt body remained unsettled: ${entry.row.request.action||''} ${entry.row.request.id||''}`;
   reject(Error(entry.row.receiptTimeout));
  },8000)})]);
  return entry.row;
 }finally{clearTimeout(timer)}
}
export async function acceptedCanvasReceipt(entry,commandId){
 assert.ok(typeof commandId==='string'&&commandId.length>0,'Clicked command must have an id');
 const row=await settleCanvasReceipt(entry);
 assert.equal(row.request.id,commandId,'Receipt must belong to the clicked command');
 assert.equal(row.transportError,undefined,'Action transport failed');
 assert.equal(row.readError,undefined,'Action receipt body could not be read');
 assert.equal(row.receiptTimeout,undefined,'Action receipt previously timed out');
 assert.equal(row.status,200,'Action HTTP response must succeed');
 assert.equal(row.body?.accepted,true,'Action receipt must explicitly accept the command');
 return row;
}
export async function settleCanvasReceipts(entries){
 const results=await Promise.allSettled([...entries].map(settleCanvasReceipt));
 const failures=results.filter(row=>row.status==='rejected');
 assert.deepEqual(failures.map(row=>row.reason.message),[],'Every observed receipt must settle');
}
