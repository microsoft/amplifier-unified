import {Connection} from './connection.js';

export const feedbackUploadScope='ahp-session:/feedback-uploads';
const names=['list','receipt','diagnostics','submit','attachment.add','excerpt.review','excerpt.stage','get','reconcile','comment','update','close','reopen'].map(name=>'feedback.'+name);
const uploadNames=['create','inspect','commit'].map(name=>'feedback.upload.'+name);
const publication=new Set(['feedback.submit','feedback.comment','feedback.update','feedback.close','feedback.reopen','feedback.excerpt.stage']);
const internalScheme='amplifier-attachment:',externalScheme='amplifier-feedback-attachment:';

/** The upload owner is a separately instantiated public resources package.
 * Its private partition never becomes a host session or starts an agent. */
export function createFeedbackCapability(options){
 const manifest={version:1,topics:{feedback:{uri:'amplifier-capability://feedback',scope:'host',version:1,watch:true}},actions:Object.fromEntries([...names,...uploadNames].map(operation=>[operation,{topic:'feedback',operation,method:'x-amplifier/capabilityAction'}]))};
 const uploads=options.uploadOwner,provider=uploads.resourceProviders.find(row=>row.scheme==='amplifier-attachment');
 if(!provider)throw Error('Feedback requires the public immutable attachment provider');
 let revision=0;
 const internal=uri=>{const parsed=new URL(uri);if(parsed.protocol!==externalScheme||parsed.searchParams.get('session')!==feedbackUploadScope)throw Error('Not an explicitly shared feedback upload');parsed.protocol=internalScheme;return parsed.href;};
 const external=uri=>{const parsed=new URL(uri);parsed.protocol=externalScheme;return parsed.href;};
 const owner=new Connection(options.owner,async(method,args)=>{
  if(method==='attachmentMetadata'){
   const value=await provider.resolve({uri:internal(args.uri)}),match=/^"sha256:([a-f0-9]{64})"$/.exec(value.etag??'');
   if(!match)throw Error('Feedback upload lacks immutable hash');
   return {size:value.size,contentType:value.contentType,sha256:match[1]};
  }
  if(method==='attachmentPage'){
   if(!Number.isSafeInteger(args.offset)||args.offset<0||!Number.isSafeInteger(args.limit)||args.limit<1||args.limit>262144)throw Error('Bounded feedback attachment page required');
   const uri=new URL(internal(args.uri));uri.searchParams.set('offset',String(args.offset));uri.searchParams.set('limit',String(args.limit));
   return provider.read({uri:uri.href,encoding:'base64'});
  }
  if(method==='readExport'){await options.inspectSession(args.session);return options.readExport(args);}
  throw Error('Unknown feedback callback');
 },()=>{revision++;options.onInvalidate?.('feedback','host');});
 const actionSchemas=async()=>({
  ...Object.fromEntries(Object.entries(await owner.request('actions',{})).map(([name,row])=>[name,{description:row.description,schema:row.parameters}])),
  ...Object.fromEntries(uploadNames.map(name=>{
   const original=uploads.actionSchemas['attachments.'+name.split('.').at(-1)],schema=structuredClone(original.inputSchema);
   if(schema.properties.size)schema.properties.size.maximum=8*1024*1024;
   return [name,{description:original.description+' Only explicit feedback Send shares these files; private file choices stay on the client.',schema}];
  })),
 });
 return {
  manifest,actionSchemas,
  resourceProviders:[{scheme:'amplifier-feedback-attachment',
   read:params=>provider.read({...params,uri:internal(params.uri)}),
   write:params=>provider.write({...params,uri:internal(params.uri)}),
   resolve:async params=>({...await provider.resolve({...params,uri:internal(params.uri)}),uri:params.uri}),
  }],
  read:async request=>{const uri=new URL(request.uri);uri.search='';uri.hash='';if(request.topic!=='feedback'||uri.href!==manifest.topics.feedback.uri)throw Error('Unknown feedback topic');
   return {topic:'feedback',scope:request.scope,revision,data:await owner.request('snapshot',{})};
  },
  action:async(request,context={})=>{
   if(request.version!==1||request.topic!=='feedback'||!manifest.actions[request.operation])throw Error('Unadvertised feedback action');
   const args=request.args||{},selected=typeof context.session==='string'?context.session:context.session?.uri;
   if(args.sessionId){if(selected&&selected!==args.sessionId)throw Error('Feedback conversation scope mismatch');await options.inspectSession(args.sessionId,{clientId:context.clientId});}
   if(context.origin==='agent'&&publication.has(request.operation)){
    if(!options.authorizeFeedback)throw Error('Explicit feedback publication/disclosure authorization required');
    await options.authorizeFeedback({operation:request.operation,args,context});
   }
   if(uploadNames.includes(request.operation)){
    if(request.operation==='feedback.upload.create'&&(!Number.isSafeInteger(args.size)||args.size<1||args.size>8*1024*1024))throw Error('Feedback files must be nonempty and at most 8 MiB');
    const result=await uploads.action({...request,channel:feedbackUploadScope,topic:'attachments',operation:'attachments.'+request.operation.split('.').at(-1)}),attachment=result.result.attachment;
    return {accepted:true,result:{attachment:attachment?{...attachment,uploadUri:external(attachment.uploadUri),resourceUri:external(attachment.resourceUri)}:null},updates:[],invalidate:[]};
   }
   const result=await owner.request('action',{operation:request.operation,args,session:selected,origin:context.origin||'ui',commandId:request.commandId});
   return {accepted:true,result,updates:[],invalidate:[]};
  },
  close:async()=>{await owner.close();await uploads.close();},
 };
}
