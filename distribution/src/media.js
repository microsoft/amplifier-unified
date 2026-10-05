import {voiceContext} from './voice-context.js';
import {createMediaCapability} from '@amplifier/unified-media-capability';
import {join} from 'node:path';

/** Media receives narrow selected-session operations, never the host store. */
export async function composeMedia(config,context,{nativeAdmin}={}){
 const adminContext=session=>({clientId:'',...(session?{session:{uri:session}}:{})});
 const native=async(operation,args,session)=>nativeAdmin.perform(operation,args,adminContext(session));
 return createMediaCapability({
  directory:join(context.directory,'media'),python:config.python,pythonMode:config.pythonMode,enableNative:config.enableNative===true,
  inspectSession:session=>voiceContext(context,session),delegate:context.delegate,recordTranscript:context.recordTranscript,recordDelivery:context.recordDelivery,
  onMayBeIdle:context.onMayBeIdle,clientPresent:context.clientPresent,invokeClientTool:context.invokeClientTool,onChanged:context.onInvalidate,
  subscribeSession:(session,listener)=>context.subscribeSession(session,async event=>{
   // The active voice observer refreshes context after meaningful lifecycle
   // changes. Token deltas must never trigger transcript scans or new ACP readers.
   if(['chat/turnStarted','chat/steeringMessageChanged','chat/turnComplete','chat/turnCancelled','chat/error','session/titleChanged','session/configChanged'].includes(event.action.type))await listener(await voiceContext(context,session));
  }),
  resolveCredential:nativeAdmin?async session=>(await native('voice.credential',{},session)).apiKey??'':async()=>process.env[config.credentialEnvironment??'OPENAI_API_KEY']??'',
  settings:nativeAdmin?session=>native('voice.configuration',{},session):async()=>({
   ...config.settings,available:Boolean(process.env[config.credentialEnvironment??'OPENAI_API_KEY']),
   configurable:false,source:'environment',
  }),
  configure:nativeAdmin?({session,args})=>native('voice.configure',args,session):undefined,
  voicePreferences:nativeAdmin?(operation,args)=>native(operation,args):undefined,
 });
}
