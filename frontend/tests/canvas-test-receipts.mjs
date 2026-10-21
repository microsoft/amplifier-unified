// Browser-test receipt barrier. Reads the observer's original promise once;
// it never replaces a response, clears the audit, or retries an action.
import assert from 'node:assert/strict';
export async function acceptedCanvasReceipt(entry,commandId){
 assert.ok(entry,'The exact action response must have an observer entry');
 let timer;
 try{
  await Promise.race([entry.settled,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Action receipt body remained unsettled')),8000)})]);
  const row=entry.row;
  assert.equal(row.request.id,commandId,'Receipt must belong to the clicked command');
  assert.equal(row.transportError,undefined,'Action transport failed');
  assert.equal(row.readError,undefined,'Action receipt body could not be read');
  assert.equal(row.status,200,'Action HTTP response must succeed');
  assert.equal(row.body?.accepted,true,'Action receipt must explicitly accept the command');
  return row;
 }finally{clearTimeout(timer)}
}
