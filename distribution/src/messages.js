import {isAbsolute} from 'node:path';

/** The launcher account is the sole principal; engine/client assertions cannot override it. */
export function messagePrincipalEngines(engines,engineId,account){
 return engines.map(({messagePrincipal,...engine})=>({...engine,...(engine.id===engineId?{messagePrincipal:account}:{})}));
}

/** Resolve passive message authority through the host's canonical history identity. */
export async function composeMessages(createMessageCapabilities,engine,{account,cwd,admissionDirectory,inspectSession,onInvalidate,onMayBeIdle}){
 // Only the compositor may supply this owner directory. Never inherit one from
 // an engine or a request; the bridge owns its admission journal and lock.
 if(admissionDirectory!==undefined&&(typeof admissionDirectory!=='string'||!isAbsolute(admissionDirectory)))throw Error('Message admission directory must be absolute');
 const owner=createMessageCapabilities({...engine,cwd,account,admissionDirectory,onInvalidate,onMayBeIdle,resolveSession:async context=>{
  if(context?.account!==account)throw Object.assign(Error('Message account mismatch'),{data:{executed:false,replayed:false,reason:'message-authority'}});
  const scope=context.session?.uri;
  if(typeof scope!=='string'||!scope.startsWith('ahp-session:/'))throw Object.assign(Error('Authenticated message session required'),{data:{executed:false,replayed:false,reason:'message-authority'}});
  const selected=await inspectSession(scope);
  if(selected.engineId!==engine.id||typeof selected.nativeSessionId!=='string'||!selected.nativeSessionId||typeof selected.workingDirectory!=='string'||!isAbsolute(selected.workingDirectory))throw Object.assign(Error('Messages require the admitted native engine and original history workspace'),{data:{executed:false,replayed:false,reason:'message-authority'}});
  return selected;
 }});
 try{owner.ready=await owner.capabilities.negotiate();}catch(error){await owner.connection.close();throw error;}
 if(!owner.ready)await owner.connection.close();
 return owner;
}
