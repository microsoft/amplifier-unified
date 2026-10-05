/** Selected conversation facts only. Never start a native runtime or model call
 * just to tell voice which tools are mounted. An absent runtime means unknown.
 */
export async function voiceContext(context,session){
 const snapshot=await context.readSessionContext(session);
 if(typeof context.nativeControlExisting!=='function')return snapshot;
 try{
  const catalog=await context.nativeControlExisting(session,'catalog.inspect',{});
  const current=await context.inspectSession(session);
  if(current.configurationRevision!==snapshot.configurationRevision)return snapshot;
  const tools=Array.isArray(catalog.tools)?catalog.tools:null;
  if(!tools)return snapshot;
  return {...snapshot,capabilities:{...snapshot.capabilities,nativeTools:'inspected',
   tools:tools.slice(0,48).map(tool=>({name:String(tool.name??'').slice(0,100),description:String(tool.description??'').slice(0,120)})),
   toolsTruncated:tools.length>48}};
 }catch{
  // Cold, retiring, or unavailable runtime: do not infer an empty tool catalog.
  return snapshot;
 }
}
