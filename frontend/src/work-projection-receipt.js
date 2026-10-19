const statuses=new Set(['loading','retrying','unavailable','ready']);
const identity=value=>typeof value==='string'&&value.length>0&&value.length<=200;
const count=value=>Number.isSafeInteger(value)&&value>=0;
const proofIdentity=value=>typeof value==='string'&&value.length===32&&/^[0-9a-f]{32}$/.test(value);

// DOM-only observation of the host's accepted display generation. This does
// not reconstruct Work, imply runtime completion, or expose source identities.
// A root-proof receipt qualifies bounded raw display, not complete association.
export function workProjectionReceipt(session,hostInstanceId){
 const projection=session?.workProjection;
 if(!identity(session?.id)||!identity(hostInstanceId)||!statuses.has(projection?.status))return undefined;
 const supplied=projection.receipt;
 const root=supplied?.scope==='root-proof';
 const window=root||supplied?.scope==='recent-events';
 const valid=supplied?.sessionId===session.id&&supplied?.hostInstanceId===hostInstanceId&&
  Number.isSafeInteger(supplied.generation)&&supplied.generation>0&&
  (window?identity(supplied.windowKey)&&supplied.historyComplete===false&&
   (root?proofIdentity(supplied.proofId)&&count(supplied.itemCount)&&supplied.itemCount<=100:
    ['sourceCount','itemCount','scannedBytes','scannedEvents'].every(key=>count(supplied[key]))):
   supplied?.scope===undefined&&['sourceCount','nodeCount','turnCount'].every(key=>count(supplied[key])));
 const receipt=!valid?null:root?
  {generation:supplied.generation,scope:'root-proof',windowKey:supplied.windowKey,historyComplete:false,
   proofId:supplied.proofId,itemCount:supplied.itemCount}:
  {generation:supplied.generation,sourceCount:supplied.sourceCount,
  ...(window?{scope:'recent-events',windowKey:supplied.windowKey,historyComplete:false,
   itemCount:supplied.itemCount,scannedBytes:supplied.scannedBytes,scannedEvents:supplied.scannedEvents}:
   {nodeCount:supplied.nodeCount,turnCount:supplied.turnCount})};
 const value=JSON.stringify({version:window?2:1,hostInstanceId,sessionId:session.id,status:projection.status,
  current:projection.current===true&&projection.status==='ready'&&valid,receipt});
 return new TextEncoder().encode(value).byteLength<=1024?value:undefined;
}
