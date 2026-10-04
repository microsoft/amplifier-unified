import {AsyncResource} from 'node:async_hooks';
import {createHash} from 'node:crypto';
import {isAbsolute} from 'node:path';
const token=v=>typeof v==='string'&&v.length>0&&v.length<=200&&!/[\x00-\x1f]/.test(v);
const scope=v=>typeof v==='string'&&v.startsWith('ahp-session:/')&&v.length<=1000&&!/[\x00-\x1f]/.test(v);
const marker=v=>v?.version===1&&v.method==='_amplifier/naming'&&v.passiveMetadata===true&&v.commandIds===true&&v.replayUnknown===false;
function snapshot(value){
 if(typeof value?.title!=='string'||[...value.title].length>200||typeof value.naming?.automatic!=='boolean'||!Number.isSafeInteger(value.naming.nameRevision)||value.naming.nameRevision<0||!Number.isSafeInteger(value.naming.policyRevision)||value.naming.policyRevision<0)throw Error('Canonical Native naming snapshot is incomplete');
 return {title:value.title,titleRevision:String(value.naming.nameRevision),metadata:{naming:structuredClone(value.naming)}};
}
/** One existing admin authority, canonical history binding and Host title lock. */
export async function composeNaming({admin,engineId,host,inspectSession,hostPortsSupported,ownerId='native-admin',onFailure=()=>{}}){
 if(!hostPortsSupported||typeof admin?.namingCapabilities!=='function'||typeof admin?.performNaming!=='function')return {available:false,close:async()=>{}};
 const negotiated=await admin.namingCapabilities();if(!marker(negotiated))return {available:false,close:async()=>{}};
 function binding(context){
  if(!scope(context?.session)||context.engineId!==engineId||!token(context.nativeSessionId)||typeof context.workingDirectory!=='string'||!isAbsolute(context.workingDirectory)||context.workingDirectory.length>8192)throw Error('Canonical selected Native naming binding required');
  return {sessionId:context.nativeSessionId,cwd:context.workingDirectory};
 }
 async function selected(session){const value=await inspectSession(session);if((value.session??value.uri)!==session)throw Error('Naming session mapping differs from the selected channel');return {...value,session};}
 const nativeNaming=async(session,operation,args)=>{if(!scope(session))throw Error('Selected naming session required');return admin.performNaming({...binding(await selected(session)),operation,args});};
 const sessionMetadata={readTitle:async context=>snapshot(await admin.performNaming({...binding(context),operation:'read',args:{}})),writeTitle:async(context,input)=>admin.performNaming({...binding(context),operation:'rename',args:{commandId:context.commandId,title:input.title}})};
 const ports={nativeNaming,nativeNamingCapabilities:async()=>structuredClone(negotiated),readSessionTitle:session=>host().readSessionTitle(session),commitSessionTitle:(...args)=>host().commitSessionTitle(...args)};
 const scheduler=new AsyncResource('native-naming-projection'),pending=new Map();let closed=false,failures=0,dropped=0,lastFailure;
 const diagnostics=()=>({pending:pending.size,failures,dropped,lastFailure});
 function event(context,params){
  const e=params?.event;
  if(closed||e?.type!=='session.naming')return false;
  if(!scope(context?.session)||!token(context.nativeSessionId)||params.sessionId!==context.nativeSessionId||!Number.isSafeInteger(e.nameRevision)||e.nameRevision<0||typeof e.name!=='string'||[...e.name].length>200||e.description!=null&&(typeof e.description!=='string'||[...e.description].length>1000)){dropped++;return false;}
  const key=JSON.stringify([context.session,context.nativeSessionId]),old=pending.get(key);
  if(old){old.revision=Math.max(old.revision,e.nameRevision);return true;}
  if(pending.size>=32){dropped++;try{onFailure(Error('Naming projection capacity exceeded'));}catch{}return false;}
  const row={session:context.session,nativeId:context.nativeSessionId,revision:e.nameRevision};pending.set(key,row);
  // Reserve Host admission synchronously, then leave the awaited ACP callback
  // before trying its selected-session lock. Root drains every retained promise.
  row.work=scheduler.runInAsyncScope(()=>host().withExternalMutation(ownerId,async()=>{
   await new Promise(resolve=>setImmediate(resolve));
   while(true){const revision=row.revision,record=await selected(row.session);if(record.engineId!==engineId||record.nativeSessionId!==row.nativeId){dropped++;return;}
    const commandId='native-name-'+createHash('sha256').update(JSON.stringify([row.session,row.nativeId,revision])).digest('hex');
    await host().commitSessionTitle(row.session,{commandId},async current=>{
     if(current.engineId!==engineId||current.nativeSessionId!==row.nativeId)return {applied:false};
     // Native event text is advisory; current canonical snapshot is authority.
     return {applied:true,title:current.title,metadata:current.metadata};
    });if(row.revision===revision)return;
   }
  })).catch(error=>{failures++;lastFailure=String(error.message??error).slice(0,512);try{onFailure(error);}catch{/* Diagnostic callback cannot change native naming. */}}).finally(()=>pending.delete(key));
  return true;
 }
 async function drain(){while(pending.size)await Promise.all([...pending.values()].map(row=>row.work));return diagnostics();}
 return {available:true,sessionMetadata,ports,event,diagnostics,drain,close:async()=>{closed=true;const result=await drain();scheduler.emitDestroy();return result;}};
}
