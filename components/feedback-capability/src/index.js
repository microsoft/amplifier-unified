import {managedParticipant} from './managed-files.js';
import {retentionParticipant} from './retention.js';
import {validateServiceRelease} from './service-lifecycle.js';
import {Connection} from './connection.js';
import {AggregateAdmission} from './aggregate-admission.js';

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
 let revision=0,activeCalls=0,admissionTransition=false;
 const participants=new Map();
 const tracked=callback=>async(...args)=>{if(admissionTransition)throw Error('Feedback admission transition is in progress');activeCalls++;try{return await callback(...args);}finally{activeCalls--;}};
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
 },()=>{revision++;options.onInvalidate?.('feedback','host');},()=>options.onMayBeIdle?.());
 const actionSchemas=async()=>({
  ...Object.fromEntries(Object.entries(await owner.request('actions',{})).map(([name,row])=>[name,{description:row.description,schema:row.parameters}])),
  ...Object.fromEntries(uploadNames.map(name=>{
   const original=uploads.actionSchemas['attachments.'+name.split('.').at(-1)],schema=structuredClone(original.inputSchema);
   if(schema.properties.size)schema.properties.size.maximum=8*1024*1024;
   return [name,{description:original.description+' Only explicit feedback Send shares these files; private file choices stay on the client.',schema}];
  })),
 });
 return {
  manifest,actionSchemas:tracked(actionSchemas),
  quiescenceAccess:Object.fromEntries(['feedback.list','feedback.receipt','feedback.diagnostics','feedback.upload.inspect'].map(name=>[name,'read'])),
  quiescenceParticipant:typeof uploads.quiescenceParticipant==='function'?(ownerId)=>{
   if(participants.has(ownerId))return participants.get(ownerId);
   const uploadParticipant=uploads.quiescenceParticipant(ownerId+':uploads');let heldUploads;
   const releaseOwner=async(context,outcome,proof,liveRollback=false)=>{const rollback=liveRollback&&outcome==='unchanged'&&proof?.kind==='admission-refused'&&Object.keys(proof).length===1;if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context,outcome,proof);const result=await owner.request('quiescence.release',{...context,outcome,proof});if(outcome!=='unknown'&&result.released!==true)throw Error('Feedback owner release unconfirmed');};
   const admission=new AggregateAdmission({ownerId,owner,uploads:uploadParticipant,releaseOwner,active:()=>activeCalls,enter:()=>{if(admissionTransition)throw Error('Feedback admission transition is already in progress');admissionTransition=true;},leave:()=>{admissionTransition=false;}});
   const participant=managedParticipant(retentionParticipant({id:ownerId,...(uploadParticipant.serviceStop?.version===1?{serviceStop:{version:1}}:{}),acquire:async context=>{
    if(context.purpose==='distribution-update'){const lease=await admission.acquire(context);if(lease){heldUploads=lease.uploadLease;const {uploadLease,...outer}=lease;return outer;}return null;}
    if(context.purpose==='retention-hide'&&uploadParticipant.retentionHide?.version!==1)return null;
    if(context.purpose==='service-stop'&&(uploadParticipant.serviceStop?.version!==1||(await owner.request('initialize',{})).quiescence?.serviceStop?.version!==1))return null;
    const exact=structuredClone(context),uploadLease=await uploadParticipant.acquire(exact);if(!uploadLease)return null;
    let result;try{result=await owner.request('quiescence.acquire',exact);}catch(error){await uploadLease.release('unknown');throw error;}
    if(result.acquired!==true){await uploadLease.release('unchanged',{kind:'admission-refused'});return null;}
    if(result.fenceId!==exact.fenceId||result.intakeClosed!==true)throw Error('Feedback owner acquisition unconfirmed');heldUploads=uploadLease;
    return {ownerId,fenceId:exact.fenceId,release:async(outcome,proof)=>{await releaseOwner(exact,outcome,proof,true);await uploadLease.release(outcome,proof);}};
   },abortAdmission:input=>admission.abort(input),reconcileRelease:async context=>{if(context.purpose==='distribution-update')return admission.release(context,context.outcome,context.proof);await releaseOwner(context,context.outcome,context.proof);await uploadParticipant.reconcileRelease(context);}},async args=>{const own=await owner.request('quiescence.retention',args),uploads=await heldUploads.inspectRetentionReferences({sessions:[feedbackUploadScope]});if(uploads.coverage!=='complete'||uploads.protected.length||uploads.omissions.length)return {...own,coverage:'partial',omissions:[...own.omissions,{reason:'feedback-upload-unattributed',scope:'owner'}]};return own;}),async args=>{const own=await owner.request('quiescence.managedFiles',args),uploads=await heldUploads.inspectManagedFilesReferences({...args,sessions:[feedbackUploadScope]});if(uploads.coverage!=='complete'||uploads.protected.length||uploads.omissions.length)return {...own,coverage:'partial',omissions:[...own.omissions,{reason:'feedback-upload-unattributed',scope:'owner'}]};return own;},async()=>uploadParticipant.managedFiles?.version===1&&(await owner.request('initialize',{})).quiescence?.managedFiles?.version===1);
   participants.set(ownerId,participant);return participant;
  }:undefined,
  resourceProviders:[{scheme:'amplifier-feedback-attachment',
   read:tracked(params=>provider.read({...params,uri:internal(params.uri)})),
   write:tracked(params=>provider.write({...params,uri:internal(params.uri)})),
   resolve:tracked(async params=>({...await provider.resolve({...params,uri:internal(params.uri)}),uri:params.uri})),
  }],
  read:tracked(async request=>{const uri=new URL(request.uri);uri.search='';uri.hash='';if(request.topic!=='feedback'||uri.href!==manifest.topics.feedback.uri)throw Error('Unknown feedback topic');
   return {topic:'feedback',scope:request.scope,revision,data:{feedback:await owner.request('snapshot',{})}};
  }),
  action:tracked(async(request,context={})=>{
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
  }),
  close:async()=>{await owner.close();await uploads.close();},
 };
}
