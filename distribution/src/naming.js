import {isAbsolute} from 'node:path';
const token=v=>typeof v==='string'&&v.length>0&&v.length<=200&&!/[\x00-\x1f]/.test(v);
const scope=v=>typeof v==='string'&&v.startsWith('ahp-session:/')&&v.length<=1000&&!/[\x00-\x1f]/.test(v);
const marker=v=>v?.version===1&&v.method==='_amplifier/naming'&&v.passiveMetadata===true&&v.commandIds===true&&v.replayUnknown===false;
function snapshot(value){
 if(typeof value?.title!=='string'||[...value.title].length>200||typeof value.naming?.automatic!=='boolean'||!Number.isSafeInteger(value.naming.nameRevision)||value.naming.nameRevision<0||!Number.isSafeInteger(value.naming.policyRevision)||value.naming.policyRevision<0)throw Error('Canonical Native naming snapshot is incomplete');
 return {title:value.title,titleRevision:String(value.naming.nameRevision),metadata:{naming:structuredClone(value.naming)}};
}
/** One existing admin authority, canonical history binding and Host title lock. */
export async function composeNaming({admin,engineId,host,inspectSession,hostPortsSupported}){
 if(!hostPortsSupported||typeof admin?.namingCapabilities!=='function'||typeof admin?.performNaming!=='function')return {available:false,close:async()=>{}};
 let negotiated;
 try{negotiated=await admin.namingCapabilities();}
 catch{return {available:false,availability:{state:'unavailable',reason:'native-initialization-failed'},close:async()=>{}};}
 if(!marker(negotiated))return {available:false,close:async()=>{}};
 function binding(context){
  if(!scope(context?.session)||context.engineId!==engineId||!token(context.nativeSessionId)||typeof context.workingDirectory!=='string'||!isAbsolute(context.workingDirectory)||context.workingDirectory.length>8192)throw Error('Canonical selected Native naming binding required');
  return {sessionId:context.nativeSessionId,cwd:context.workingDirectory};
 }
 async function selected(session){const value=await inspectSession(session);if((value.session??value.uri)!==session)throw Error('Naming session mapping differs from the selected channel');return {...value,session};}
 const nativeNaming=async(session,operation,args)=>{if(!scope(session))throw Error('Selected naming session required');return admin.performNaming({...binding(await selected(session)),operation,args});};
 const sessionMetadata={readTitle:async context=>snapshot(await admin.performNaming({...binding(context),operation:'read',args:{}})),writeTitle:async(context,input)=>admin.performNaming({...binding(context),operation:'rename',args:{commandId:context.commandId,title:input.title}})};
 const ports={nativeNaming,nativeNamingCapabilities:async()=>structuredClone(negotiated),readSessionTitle:session=>host().readSessionTitle(session),commitSessionTitle:(...args)=>host().commitSessionTitle(...args)};
 // Native owns automatic naming and sends standard ACP session_info_update.
 // Host reconciles that update through sessionMetadata. Custom naming events
 // remain available to other extension consumers and never project twice here.
 const diagnostics=()=>({pending:0,failures:0,dropped:0});
 return {available:true,sessionMetadata,ports,diagnostics,close:async()=>diagnostics()};
}
