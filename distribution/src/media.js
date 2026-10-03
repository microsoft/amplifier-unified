import {createMediaCapability} from '@amplifier/unified-media-capability';
import {join} from 'node:path';

/** Media receives narrow selected-session operations, never the host store. */
export async function composeMedia(config,context,{nativeAdmin}={}){
 const adminContext=session=>({clientId:'',...(session?{session:{uri:session}}:{})});
 const native=async(operation,args,session)=>nativeAdmin.perform(operation,args,adminContext(session));
 return createMediaCapability({
  directory:join(context.directory,'media'),python:config.python,enableNative:config.enableNative===true,
  inspectSession:context.readSessionContext,delegate:context.delegate,recordTranscript:context.recordTranscript,
  onMayBeIdle:context.onMayBeIdle,clientPresent:context.clientPresent,invokeClientTool:context.invokeClientTool,onChanged:context.onInvalidate,
  subscribeSession:(session,listener)=>context.subscribeSession(session,async event=>{
   // The active voice observer only refreshes context after meaningful completed
   // changes. Token deltas must never trigger transcript scans or new ACP readers.
   if(['chat/turnComplete','chat/turnCancelled','chat/error','session/titleChanged'].includes(event.action.type))await listener(await context.readSessionContext(session));
  }),
  resolveCredential:nativeAdmin?async session=>(await native('voice.credential',{},session)).apiKey??'':async()=>process.env[config.credentialEnvironment??'OPENAI_API_KEY']??'',
  settings:nativeAdmin?session=>native('voice.configuration',{},session):async()=>config.settings??{},
  configure:nativeAdmin?({session,args})=>native('voice.configure',args,session):undefined,
 });
}
