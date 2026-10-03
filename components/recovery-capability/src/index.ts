import {createHash,randomUUID} from 'node:crypto';
import {Store} from './store.js';
import {definitions,quiescenceAccess,validate} from './schemas.js';
import type {Context,FenceContext,Job,Json,Options,ReleaseProof} from './types.js';
export type * from './types.js';
const digest=(value:unknown):string=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const bounded=(value:any,bytes:number)=>{if(Buffer.byteLength(JSON.stringify(value))>bytes)throw Error('Public recovery value exceeds its bound');return value;};
const knownRefusal=(error:any)=>error?.data?.executed===false;
const child=(job:Job,kind:string)=>'recovery:'+digest([job.accountId,job.commandId,kind]);
const token=(value:unknown,name:string)=>{if(typeof value!=='string'||!value||value.length>200||/[\x00-\x1f]/.test(value))throw Error(`Invalid ${name}`);return value;};
const encode=(row:Json)=>Buffer.from(JSON.stringify(row)).toString('base64url');
function decode(value:string):Json{if(!value||value.length>200)throw Error('Invalid recovery cursor');try{return JSON.parse(Buffer.from(value,'base64url').toString());}catch{throw Error('Invalid recovery cursor');}}
/** A single configured native authority, with private durable job and account ownership. */
export class RecoveryCapabilities {
 readonly manifest={version:1,topics:{recovery:{version:1,uri:'amplifier-capability://recovery/recovery',watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(definitions).map(operation=>[operation,{topic:'recovery',operation,method:'x-amplifier/capabilityAction'}]))};
 readonly quiescenceAccess=quiescenceAccess;
 private store:Store;private closed=false;private tasks=new Set<Promise<void>>();
 constructor(private options:Options){
  token(options.nativeAuthority,'configured native authority');
  if(options.leaseSeconds!==undefined&&(!Number.isSafeInteger(options.leaseSeconds)||options.leaseSeconds<1||options.leaseSeconds>300))throw Error('Native lease duration must be1..300 seconds');
  this.store=new Store(options.directory);
 }
 actionSchemas(){return definitions;}
 private notify(){try{this.options.onInvalidate?.('recovery','host');}catch{/* Advisory invalidation cannot change durable operation truth. */}}
 private changed(job?:Job){if(job)this.store.save(job);this.notify();}
 private async account(context:Context,operation:string,args:Json){
  if(this.closed)throw Error('Recovery owner is closed');
  const value=await this.options.authorize(context,operation,args);if(context.origin==='agent'&&operation==='recovery.list')throw Error('Agents must inspect an exact own-session recovery job');return token(value?.accountId,'authenticated account');
 }
 private own(account:string,id:string,context?:Context){const job=this.store.get(id);if(!job||job.accountId!==account)throw Error('Recovery job is unavailable to this account');if(context?.origin==='agent'){const selected=typeof context.session==='string'?context.session:context.session?.uri;if(job.sessions.length!==1||job.sessions[0].session!==selected)throw Error('Agent recovery cannot read another conversation');}return job;}
 private descriptor(job:Job){
  const result=job.result?structuredClone(job.result):undefined;
  if(result?.artifactId)result.resourceUri=this.artifactUri(job.id,result.sha256,0);
  return {id:job.id,commandId:job.commandId,operation:job.operation,state:job.state,revision:job.revision,createdAt:job.createdAt,updatedAt:job.updatedAt,sessions:job.sessions.map(row=>row.session),coverage:'explicit-selected-native-only',automaticResume:false,replayed:false,...(job.reason?{reason:job.reason}:{}),...(job.preview?{previewHash:job.preview.previewHash,reviewAvailable:true}:{}),...(result?{result}:{}),...(job.fence?{intakeFence:{fenceId:job.fence.fenceId,commandId:job.fence.commandId}}:{})};
 }
 private list(account:string,args:Json){
  const before=args.cursor?decode(args.cursor):undefined;
  if(before&&(!Number.isSafeInteger(before.created)||typeof before.id!=='string'))throw Error('Invalid recovery page cursor');
  const limit=args.limit??25,rows=this.store.page(account,limit+1,before as any),more=rows.length>limit,items=rows.slice(0,limit).map(row=>({id:row.id,commandId:row.command,state:row.state,createdAt:row.created,revision:row.revision}));
  const last=items.at(-1);return {items,nextCursor:more&&last?encode({created:last.createdAt,id:last.id}):null,coverage:'explicit-selected-native-only',fullProductBackup:false};
 }
 private preview(job:Job,args:Json){
  if(!job.preview)throw Error('No immutable review is available for this job');
  const value=job.preview,cursor=args.cursor?decode(args.cursor):{offset:0,hash:value.previewHash};
  if(cursor.hash!==value.previewHash||!Number.isSafeInteger(cursor.offset)||cursor.offset<0)throw Error('Preview cursor does not match this immutable review');
  const entries=[...(value.inventory.files??[]).map((row:Json)=>({kind:'included',...row})),...(value.inventory.missing??[]).map((path:string)=>({kind:'missing',path})),...(value.inventory.excluded??[]).map((row:Json)=>({kind:'excluded',...row}))];
  const end=cursor.offset+(args.limit??25);
  return bounded({jobId:job.id,previewHash:value.previewHash,coverage:value.coverage,containsPrivateContent:true,credentialCoverage:value.credentialCoverage,sessions:job.sessions.map(row=>row.session),parts:value.spec.parts,bytes:value.inventory.bytes,totalEntries:entries.length,items:entries.slice(cursor.offset,end),nextCursor:end<entries.length?encode({offset:end,hash:value.previewHash}):null,omissions:value.omissions,reset:value.reset??null},128*1024);
 }
 async read(params:Json,context:Context={clientId:params.clientId}){
  const identity=new URL(params.uri);identity.search='';identity.hash='';
  if(identity.href!==this.manifest.topics.recovery.uri||params.topic!=='recovery'||params.scope!=='host')throw Error('Recovery topic requires host scope');
  const account=await this.account(context,'recovery.list',{});
  return {topic:'recovery',scope:'host',revision:this.store.revision(),data:{recovery:this.list(account,{limit:25})}};
 }
 async action(params:Json,context:Context){
  if(params.version!==1||params.topic!=='recovery'||!Object.hasOwn(definitions,params.operation))throw Error('Unadvertised recovery action or scope');
  const selected=typeof context.session==='string'?context.session:context.session?.uri;
  if(!['host','ahp-root://'].includes(params.channel)&&(params.channel!==selected||!/^ahp-session:\/[^/?#]+$/.test(params.channel)))throw Error('Recovery action requires authenticated host or exact selected session scope');
  const operation=String(params.operation),args=bounded(params.args??{},32768);validate(definitions[operation].schema,args);
  if(selected&&args.sessionId&&selected!==args.sessionId)throw Error('Recovery routing selector differs from authenticated session');
  const account=await this.account(context,operation,args);
  let result:Json;
  if(operation==='recovery.list')result=this.list(account,args);
  else if(operation==='recovery.job')result=this.descriptor(this.own(account,args.jobId,context));
  else if(operation==='recovery.command'){const row=this.store.command(account,args.commandId);result=row?this.descriptor(this.own(account,row.id,context)):{available:false,commandId:args.commandId};}
  else if(operation==='recovery.preview')result=this.preview(this.own(account,args.jobId,context),args);
  else if(operation==='recovery.reconcile')result=await this.reconcile(this.own(account,args.jobId,context),context);
  else{try{result=await this.enqueue(account,operation,args,token(params.commandId,'durable command identity'),context);}catch(error){if(!knownRefusal(error))throw error;return {accepted:false,result:{accepted:false,executed:false,reason:'recovery-intake-refused'},updates:[]};}}
  return {accepted:result.accepted!==false,result,updates:[]};
 }
 private assertIntake(){if(this.store.fence())throw Object.assign(Error('Recovery intake is fenced'),{data:{executed:false}});if(this.store.unsettled())throw Object.assign(Error('Another recovery job is pending or uncertain; inspect it before new work'),{data:{executed:false}});}
 private async enqueue(account:string,operation:string,args:Json,commandId:string,context:Context){
  const signature=digest(canonical({operation,args})),prior=this.store.command(account,commandId);
  if(prior){if(prior.signature!==signature)throw Error('Recovery command identity has different exact arguments');return this.descriptor(prior);}
  this.assertIntake();
  let sessions:Job['sessions'],prepared:Job|undefined;
  if(operation==='recovery.prepare'||operation==='recovery.reset.prepare'){
   const uris:string[]=operation==='recovery.prepare'?args.sessions:[args.sessionId],own=typeof context.session==='string'?context.session:context.session?.uri;
   if(args.sessionId&&(uris.length!==1||uris[0]!==args.sessionId))throw Error('Recovery selector must match the exact single selected session');
   if(context.origin==='agent'&&(uris.length!==1||uris[0]!==own))throw Error('Agent recovery cannot select another conversation');
   sessions=[];
   for(const session of uris){const resolved=await this.options.resolveSession(session,context);token(resolved.nativeSessionId,'native session identity');if(resolved.nativeAuthority!==this.options.nativeAuthority||typeof resolved.historyCwd!=='string'||!resolved.historyCwd)throw Error('Recovery selection is outside the configured native authority');sessions.push({session,...resolved});}
   if(new Set(sessions.map(row=>row.nativeSessionId)).size!==sessions.length)throw Error('Native recovery selection contains aliases for the same session');
   if(args.includeCredentials===true&&args.credentialsReviewed!==true)throw Error('Credential inclusion requires explicit review and authorization');
  }else{
   prepared=this.own(account,args.preparedJobId);
   if(prepared.state!=='prepared'||!prepared.preview||prepared.preview.previewHash!==args.previewHash)throw Error('An exact completed immutable review is required');
   const reset=operation.startsWith('recovery.reset.');
   if(prepared.operation!==(reset?'recovery.reset.prepare':'recovery.prepare'))throw Error('Review is for a different recovery operation');
   sessions=structuredClone(prepared.sessions);
   if(args.sessionId&&(sessions.length!==1||sessions[0].session!==args.sessionId))throw Error('Recovery selector must match the reviewed session');
   const own=typeof context.session==='string'?context.session:context.session?.uri;
   if(context.origin==='agent'&&(sessions.length!==1||sessions[0].session!==own))throw Error('Agent recovery cannot use another conversation’s review');
   for(const row of sessions){const current=await this.options.resolveSession(row.session,context);if(digest(canonical(current))!==digest(canonical({nativeSessionId:row.nativeSessionId,historyCwd:row.historyCwd,nativeAuthority:row.nativeAuthority})))throw Error('Native recovery identity changed after review');}
   if(operation==='recovery.reset.restore'){
    const original=this.own(account,args.resetJobId);
    if(original.state!=='succeeded'||original.operation!=='recovery.reset.apply'||original.result?.postResetHash!==args.expectedPostResetHash||original.sessions[0].nativeSessionId!==sessions[0].nativeSessionId)throw Error('Restore requires this session’s exact successful reset receipt and post-reset hash');
   }
  }
  this.assertIntake();
  const now=Date.now(),job:Job={id:randomUUID(),accountId:account,commandId,operation,args:structuredClone(args),signature,state:'queued',createdAt:now,updatedAt:now,revision:0,context:structuredClone(context),sessions,fenceCommandId:'',...(prepared?{preparedJobId:prepared.id,preview:structuredClone(prepared.preview)}:{})};
  job.fenceCommandId=child(job,'quiescence');this.store.insert(job);this.notify();
  // Return the host capability admission before attempting to close host intake.
  setImmediate(()=>{if(this.closed)return;const task=this.run(job).catch(()=>{job.state='unknown';job.reason='owner-job-failed-no-replay';this.changed(job);}).finally(()=>this.tasks.delete(task));this.tasks.add(task);});
  return this.descriptor(job);
 }
 private async native(job:Job,operation:string,args:Json){return bounded(await this.options.nativeAdmin(operation,args,job.context),2*1024*1024);}
 private saveResult(job:Job,result:Json){
  const {receipt,manifest,workspace,...value}=result;job.nativeResult={state:receipt?.state??'succeeded',commandId:job.nativeCommandId,operation:job.nativeOperation,result:value};job.result=bounded(value,64*1024);
 }
 private async run(job:Job){
  job.state='quiescing';this.changed(job);
  let admission:Json;
  try{admission=await this.options.quiescence.admitQuiescence({commandId:job.fenceCommandId,purpose:'recovery'});}catch(error){job.state=knownRefusal(error)?'refused':'unknown';job.reason=knownRefusal(error)?'quiescence-refused':'quiescence-outcome-unknown';this.changed(job);return;}
  if(admission.admitted!==true){job.state=admission.executed===false&&admission.intakeClosed===false?'refused':'unknown';job.reason=job.state==='refused'?'quiescence-refused':'quiescence-outcome-unknown';this.changed(job);return;}
  const evidence=admission.evidence;
  if(admission.commandId!==job.fenceCommandId||!evidence?.instanceId||!evidence?.dataScope||!admission.fenceId){job.state='unknown';job.reason='invalid-quiescence-proof';this.changed(job);return;}
  job.fence={fenceId:admission.fenceId,commandId:job.fenceCommandId,purpose:'recovery',instanceId:evidence.instanceId,dataScope:evidence.dataScope};job.state='running';this.changed(job);
  try{
   await this.options.quiescence.withQuiescenceMaintenance(job.fence,async()=>{
    if(job.operation==='recovery.prepare'||job.operation==='recovery.reset.prepare'){
     try{
      const {sessionId:_routing,...previewArgs}=job.args;
      const spec=job.operation==='recovery.prepare'?{...previewArgs,sessions:job.sessions.map(row=>({nativeSessionId:row.nativeSessionId,cwd:row.historyCwd}))}:{nativeSessionId:job.sessions[0].nativeSessionId,cwd:job.sessions[0].historyCwd,scope:'session-configuration',privateContentReviewed:true};
      const preview=await this.native(job,job.operation==='recovery.prepare'?'maintenance.preview':'maintenance.reset.preview',spec);job.preview=preview;
      if(!/^[a-f0-9]{64}$/.test(preview.previewHash)||!preview.inventory||preview.coverage!=='explicit-selected-native-only')throw Error('Invalid selected preview');
      job.terminalState='prepared';
     }catch{job.terminalState='refused';job.reason='preview-unavailable-no-canonical-change';}
     job.nativeLeaseReleased=true;this.changed(job);return;
    }
    try{
     job.nativeOperation='maintenance.acquire';job.nativeCommandId=child(job,'acquire');this.changed(job);
     const lease=await this.native(job,job.nativeOperation,{commandId:job.nativeCommandId,previewHash:job.args.previewHash,ttlSeconds:this.options.leaseSeconds??60});
     if(lease.active!==true||typeof lease.leaseId!=='string')throw Error('Native acquisition is unconfirmed');
     job.leaseId=lease.leaseId;this.changed(job);
     job.nativeOperation=job.operation==='recovery.snapshot'?'maintenance.snapshot':job.operation==='recovery.reset.apply'?'maintenance.reset.apply':'maintenance.reset.restore';job.nativeCommandId=child(job,'effect');this.changed(job);
     const args:Json={commandId:job.nativeCommandId,leaseId:job.leaseId,previewHash:job.args.previewHash};
     if(job.operation==='recovery.reset.restore'){const prior=this.store.get(job.args.resetJobId)!;args.resetCommandId=prior.nativeCommandId;args.expectedPostResetHash=job.args.expectedPostResetHash;}
     const result=await this.native(job,job.nativeOperation,args);
     if(result.receipt?.state!=='succeeded')throw Error('Native effect has no conclusive successful receipt');
     this.saveResult(job,result);job.terminalState='succeeded';this.changed(job);
    }catch(error){
     if(job.nativeOperation==='maintenance.acquire'&&knownRefusal(error)){job.terminalState='refused';job.nativeLeaseReleased=true;job.reason='native-acquisition-refused-review-again';}
     else{job.reason='native-effect-outcome-unknown';job.terminalState=undefined;}
     this.changed(job);
    }finally{
     if(job.leaseId){
      try{const release=await this.native(job,'maintenance.release',{leaseId:job.leaseId});if(release.released!==true||release.active!==false||release.leaseId!==job.leaseId)throw Error('Native release is unconfirmed');job.nativeLeaseReleased=true;}
      catch{job.nativeLeaseReleased=false;job.reason='native-lease-release-unknown';job.terminalState=undefined;}
      this.changed(job);
     }
    }
   });
  }catch{job.reason='held-maintenance-outcome-unknown';job.terminalState=undefined;this.changed(job);}
  if(job.terminalState&&job.nativeLeaseReleased){this.recordReleaseEvidence(job);await this.releaseHost(job);}
  else{job.state='unknown';this.changed(job);try{await this.options.quiescence.releaseQuiescence({...job.fence,outcome:'unknown',evidence:{owner:'recovery',jobId:job.id}});}catch{/* Uncertain work remains fenced. */}}
 }
 private recordReleaseEvidence(job:Job){
  job.releaseEvidence={owner:'recovery',receiptId:'recovery:'+job.id,jobId:job.id,...job.fence,outcome:'unchanged',terminalState:job.terminalState,nativeLeaseReleased:true,nativeLeaseDisposition:job.leaseId?'released':'not-acquired',nativeAuthority:this.options.nativeAuthority,operation:job.operation,nativeCommandId:job.nativeCommandId??null,nativeResultHash:job.nativeResult?digest(job.nativeResult):null,settledAt:Date.now()};this.changed(job);
 }
 /** Trusted coordinator-only proof read, not a client action or caller assertion. */
 readReleaseEvidence(input:{fenceId:string;commandId:string}):Json|undefined{
  const fence=this.store.fence();if(!fence||fence.fenceId!==input.fenceId||fence.commandId!==input.commandId||!fence.jobId){const released=this.store.releaseReceipt(input.fenceId);return released?.commandId===input.commandId&&released.evidence?structuredClone(released.evidence):undefined;}
  const job=this.store.get(fence.jobId);return job?.releaseEvidence&&job.nativeLeaseReleased&&job.terminalState?structuredClone(job.releaseEvidence):undefined;
 }
 private async releaseHost(job:Job){
  job.state='releasing';this.changed(job);
  try{const result=await this.options.quiescence.releaseQuiescence({...job.fence!,outcome:'unchanged',evidence:{owner:'recovery',jobId:job.id,receiptId:job.releaseEvidence!.receiptId}});if(result.released!==true||result.intakeClosed!==false)throw Error('Host release is unconfirmed');job.state=job.terminalState!;job.reason=job.state==='refused'?job.reason:undefined;}
  catch{job.state='unknown';job.reason='host-release-outcome-unknown';}
  this.changed(job);
 }
 private async reconcile(job:Job,context:Context){
  if(job.state!=='unknown')return this.descriptor(job);
  if(job.nativeCommandId){
   const inspected=await this.options.nativeAdmin('maintenance.receipt',{commandId:job.nativeCommandId},context);
   // A canonical reset receipt alone cannot prove its pending finalization marker was removed.
   if(job.operation==='recovery.snapshot'&&job.nativeOperation==='maintenance.snapshot'&&inspected.receipt?.state==='succeeded'&&job.nativeLeaseReleased){this.saveResult(job,{...inspected.receipt.result,receipt:inspected.receipt});job.terminalState='succeeded';this.recordReleaseEvidence(job);}
   else{job.nativeResult={...(job.nativeResult??{}),inspectedReceiptState:inspected.receipt?.state??'unavailable'};this.changed(job);}
  }
  const receipt=await this.options.quiescence.quiescenceReceipt(job.fenceCommandId);
  if(job.releaseEvidence&&job.terminalState&&job.nativeLeaseReleased){
   if(receipt?.released===true&&receipt.intakeClosed===false&&receipt.fenceId===job.fence?.fenceId){job.state=job.terminalState;job.reason=undefined;this.changed(job);}
   else if(job.fence)await this.releaseHost(job);
  }
  return this.descriptor(job);
 }
 /** Exempts only this owner's exact persisted pre-effect job, never unrelated work. */
 readonly quiescenceParticipant={
  id:'recovery',
  acquire:async(context:Readonly<FenceContext>)=>{
   if(this.closed||this.store.fence())return null;
   const own=this.store.db.prepare("SELECT id FROM jobs WHERE state='quiescing' AND json_extract(payload,'$.fenceCommandId')=? LIMIT 1").get(context.commandId);
   const job=own?this.store.get(String(own.id)):undefined;
   if(job&&context.purpose!=='recovery'||this.store.unsettled(job?.id))return null;
   this.store.setFence({...context,...(job?{jobId:job.id}:{})});
   return {ownerId:'recovery',fenceId:context.fenceId,release:async(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.releaseParticipant(context,outcome,proof)};
  },
  reconcileRelease:async(context:Readonly<FenceContext>&{outcome:'unchanged'|'ready';proof:ReleaseProof})=>this.releaseParticipant(context,context.outcome,context.proof),
 };
 private async releaseParticipant(context:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'}){
  const exact={fenceId:context.fenceId,commandId:context.commandId,purpose:context.purpose,instanceId:context.instanceId,dataScope:context.dataScope};
  const signature=digest(canonical({context:exact,outcome,proof})),prior=this.store.releaseReceipt(context.fenceId);
  if(prior){if(prior.commandId!==context.commandId||prior.signature!==signature)throw Error('Recovery release identity has different exact proof');return;}
  const fence=this.store.fence();if(!fence||fence.fenceId!==context.fenceId||fence.commandId!==context.commandId)throw Error('Exact recovery participant fence required');
  if(outcome==='unknown')return;
  if(proof&&'kind' in proof&&proof.kind==='admission-refused'){
   const job=fence.jobId?this.store.get(fence.jobId):undefined;
   if(job&&(job.state!=='quiescing'||job.nativeCommandId||job.leaseId))throw Error('Recovery effect has already started');
  }else{
   const p=proof as ReleaseProof|undefined;
   if(!p||p.verified!==true||p.fenceId!==context.fenceId||p.commandId!==context.commandId||p.outcome!==outcome||p.dataScope!==context.dataScope||!p.instanceId||!p.receiptId)throw Error('Authenticated coordinator release proof required');
   if(fence.jobId){const job=this.store.get(fence.jobId);if(outcome!=='unchanged'||p.instanceId!==context.instanceId||!job?.releaseEvidence||!job.nativeLeaseReleased||!job.terminalState||p.receiptId!==job.releaseEvidence.receiptId)throw Error('Recovery job has no conclusive exact release receipt');}
  }
  const evidence=fence.jobId?this.store.get(fence.jobId)?.releaseEvidence:undefined;
  this.store.completeRelease(exact,signature,evidence);
 }
 private artifactUri(id:string,hash:string,offset:number){return `amplifier-recovery://archive/${id}?sha256=${hash}&offset=${offset}`;}
 async resourceRead(params:Json,context:Context){
  if(params.channel!=='ahp-root://')throw Error('Private recovery artifacts require root account scope');
  const url=new URL(params.uri);if(url.protocol!=='amplifier-recovery:'||url.hostname!=='archive'||url.hash||[...url.searchParams.keys()].some(key=>!['sha256','offset','maxBytes'].includes(key)))throw Error('Unknown recovery artifact resource');
  const id=url.pathname.slice(1),account=await this.account(context,'recovery.artifact.read',{jobId:id}),job=this.own(account,id,context),result=job.result;
  if(job.state!=='succeeded'||job.operation!=='recovery.snapshot'||!result?.artifactId||url.searchParams.get('sha256')!==result.sha256)throw Error('Exact successful archive identity required');
  const offset=Number(url.searchParams.get('offset')??0),maxBytes=Number(url.searchParams.get('maxBytes')??262144);
  if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>262144)throw Error('Bounded archive chunk required');
  const page=await this.options.nativeAdmin('maintenance.artifact.read',{artifactId:result.artifactId,sha256:result.sha256,offset,maxBytes},context);
  if(typeof page.data!=='string'||page.data.length>4*Math.ceil(maxBytes/3))throw Error('Native archive chunk exceeds its bound');
  const bytes=Buffer.from(page.data,'base64');
  if(page.sha256!==result.sha256||page.artifactId!==result.artifactId||page.offset!==offset||page.bytes!==result.bytes||page.encoding!=='base64'||bytes.length>maxBytes||createHash('sha256').update(bytes).digest('hex')!==page.chunkSha256)throw Error('Native archive chunk failed exact identity/integrity validation');
  if(params.encoding==='utf-8')throw Error('Archive bytes require base64 encoding');
  return {data:page.data,encoding:'base64',contentType:'application/x-tar'};
 }
 async close(){if(this.closed)return;this.closed=true;await Promise.allSettled([...this.tasks]);this.store.close();}
}
export function createRecoveryCapabilities(options:Options){return new RecoveryCapabilities(options);}
