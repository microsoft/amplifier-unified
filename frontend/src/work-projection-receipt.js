const statuses=new Set(['loading','retrying','unavailable','ready']);
const identity=value=>typeof value==='string'&&value.length>0&&value.length<=200;
const count=value=>Number.isSafeInteger(value)&&value>=0;

// DOM-only observation of the host's accepted display generation. This does
// not reconstruct Work, imply runtime completion, or expose source identities.
export function workProjectionReceipt(session,hostInstanceId){
 const projection=session?.workProjection;
 if(!identity(session?.id)||!identity(hostInstanceId)||!statuses.has(projection?.status))return undefined;
 const supplied=projection.receipt;
 const valid=supplied?.sessionId===session.id&&supplied?.hostInstanceId===hostInstanceId&&
  Number.isSafeInteger(supplied.generation)&&supplied.generation>0&&
  ['sourceCount','nodeCount','turnCount'].every(key=>count(supplied[key]));
 const receipt=valid?{generation:supplied.generation,sourceCount:supplied.sourceCount,
  nodeCount:supplied.nodeCount,turnCount:supplied.turnCount}:null;
 const value=JSON.stringify({version:1,hostInstanceId,sessionId:session.id,status:projection.status,
  current:projection.current===true&&projection.status==='ready'&&valid,receipt});
 return new TextEncoder().encode(value).byteLength<=1024?value:undefined;
}
