// Observations replace one another. Keep at most one request and one latest
// pending value so a slow host never receives an obsolete screen-report queue.
export function createViewReporter(send,onError=()=>{}){
 let pending=null,inflight=null,delivered=null;
 function flush(){
  if(inflight)return inflight;
  if(pending===delivered){pending=null;return Promise.resolve()}
  inflight=(async()=>{
   while(pending!==null){
    const value=pending;pending=null;
    if(value===delivered)continue;
    try{await send(JSON.parse(value));delivered=value}catch(error){onError(error)}
   }
  })().finally(()=>{inflight=null;if(pending!==null)flush()});
  return inflight;
 }
 return payload=>{pending=JSON.stringify(payload);return flush()};
}
