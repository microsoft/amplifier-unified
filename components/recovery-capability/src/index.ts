import {AppResets,resetOwners} from './app-reset.js';
import {managedParticipant,emptyManaged} from './managed-files.js';
import {emptyRetention,retentionParticipant} from './retention.js';
import {serviceIdentity,validateServiceRelease,evidenceKey} from './service-lifecycle.js';
import {createHash,randomUUID} from 'node:crypto';
import {Store} from './store.js';
import {definitions,negotiatedDefinitions,quiescenceAccess,validate} from './schemas.js';
import type {Context,FenceContext,Job,Json,Options,ReleaseProof} from './types.js';
export type * from './types.js';
const digest=(value:unknown):string=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const bounded=(value:any,bytes:number)=>{if(Buffer.byteLength(JSON.stringify(value))>bytes)throw Error('Public recovery value exceeds its bound');return value;};
const knownRefusal=(error:any)=>error?.data?.executed===false;
const child=(job:Job,kind:string)=>'recovery:'+digest([job.accountId,job.commandId,kind]);
const token=(value:unknown,name:string)=>{if(typeof value!=='string'||!value||value.length>200||/[\x00-\x1f]/.test(value))throw Error(`Invalid ${name}`);return value;};
const encode=(row:Json)=>Buffer.from(JSON.stringify(row)).toString('base64url');
function decode(value:string,maximum=200):Json{if(!value||value.length>maximum)throw Error('Invalid recovery cursor');try{return JSON.parse(Buffer.from(value,'base64url').toString());}catch{throw Error('Invalid recovery cursor');}}
/** A single configured native authority, with private durable job and account ownership. */
export class RecoveryCapabilities {
 readonly manifest:Json;private definitions:typeof definitions;private restoreDestinations:{id:string;label:string}[];
 readonly quiescenceAccess:Record<string,'read'|'reconcile'>;
 private appResets:AppResets;private store:Store;private closed=false;private tasks=new Set<Promise<void>>();
 constructor(private options:Options){
  token(options.nativeAuthority,'configured native authority');
  this.restoreDestinations=structuredClone(options.restoreDestinationChoices??[]);
  if(this.restoreDestinations.length>32||new Set(this.restoreDestinations.map(row=>row.id)).size!==this.restoreDestinations.length)throw Error('Bounded unique restore destination choices required');
  for(const row of this.restoreDestinations){if(Object.keys(row).some(key=>!['id','label'].includes(key)))throw Error('Restore destinations expose opaque IDs and labels only');token(row.id,'restore root identity');if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(row.id))throw Error('Bounded opaque restore root identity required');token(row.label,'restore root label');}
  this.appResets=new AppResets(resetOwners(options.nativeMaintenance,options.nativeAdmin,options.appResetOwners));
  this.definitions=negotiatedDefinitions(options.nativeMaintenance?bounded(structuredClone(options.nativeMaintenance),16384):undefined,this.restoreDestinations,this.appResets.parts);
  this.quiescenceAccess=Object.fromEntries(Object.entries(quiescenceAccess).filter(([name])=>Object.hasOwn(this.definitions,name)));
  this.manifest={version:1,topics:{recovery:{version:1,uri:'amplifier-capability://recovery/recovery',watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(this.definitions).map(operation=>[operation,{topic:'recovery',operation,method:'x-amplifier/capabilityAction'}]))};
  if(options.leaseSeconds!==undefined&&(!Number.isSafeInteger(options.leaseSeconds)||options.leaseSeconds<1||options.leaseSeconds>300))throw Error('Native lease duration must be1..300 seconds');
  this.store=new Store(options.directory);
 }
 actionSchemas(){return structuredClone(this.definitions);}
 private notify(){try{this.options.onInvalidate?.('recovery','host');}catch{/* Advisory invalidation cannot change durable operation truth. */}}
 private changed(job?:Job){if(job)this.store.save(job);this.notify();}
 private async account(context:Context,operation:string,args:Json){
  if(this.closed)throw Error('Recovery owner is closed');
  const value=await this.options.authorize(context,operation,args);if(context.origin==='agent'&&operation==='recovery.list')throw Error('Agents must inspect an exact own-session recovery job');return token(value?.accountId,'authenticated account');
 }
 private own(account:string,id:string,context?:Context){const job=this.store.get(id);if(!job||job.accountId!==account)throw Error('Recovery job is unavailable to this account');if(context?.origin==='agent'){const selected=typeof context.session==='string'?context.session:context.session?.uri;if(job.sessions.length!==1||job.sessions[0].session!==selected)throw Error('Agent recovery cannot read another conversation');}return job;}
 private descriptor(job:Job){
  const result=job.result?structuredClone(job.result):undefined;
  if(job.operation==='recovery.snapshot'&&result?.artifactId)result.resourceUri=this.artifactUri(job.id,result.sha256,0);
  return {id:job.id,commandId:job.commandId,operation:job.operation,state:job.state,revision:job.revision,createdAt:job.createdAt,updatedAt:job.updatedAt,sessions:job.sessions.map(row=>row.session),coverage:job.preview?.coverage??job.result?.coverage??'explicit-selected-native-only',automaticResume:false,replayed:false,...(job.reason?{reason:job.reason}:{}),...(job.appResetCommands?{ownerProgress:Object.entries(job.appResetCommands).map(([ownerId,commandId])=>({ownerId,commandId,state:job.appResetReceipts?.[ownerId]?.state??'unknown'}))}:{}),...(job.preview?{previewHash:job.preview.previewHash,reviewAvailable:true}:{}),...(result?{result}:{}),...(job.fence?{intakeFence:{fenceId:job.fence.fenceId,commandId:job.fence.commandId}}:{})};
 }
 private list(account:string,args:Json){
  const before=args.cursor?decode(args.cursor):undefined;
  if(before&&(!Number.isSafeInteger(before.created)||typeof before.id!=='string'))throw Error('Invalid recovery page cursor');
  const limit=args.limit??25,rows=this.store.page(account,limit+1,before as any),more=rows.length>limit,items=rows.slice(0,limit).map(row=>({id:row.id,commandId:row.command,state:row.state,createdAt:row.created,revision:row.revision}));
  const last=items.at(-1);return {items,nextCursor:more&&last?encode({created:last.createdAt,id:last.id}):null,coverage:'native-only',fullProductBackup:false,capabilities:{appReset:this.appResets.parts.length?{version:1,parts:this.appResets.parts,retainedUndo:true,completeAppReset:false}:undefined,archiveParts:[...this.definitions['recovery.archive.prepare'].schema.properties.parts.items.enum],cacheInventory:this.definitions['recovery.cache.scan']?{version:1,cold:true,pageSize:50,selectionSize:50,clearKinds:['python-bytecode'],sourceRetirement:false}:undefined,restoreAvailable:!!this.definitions['recovery.restore.prepare'],restoreDestinations:this.definitions['recovery.restore.prepare']?structuredClone(this.restoreDestinations):[]}};
 }
 private async preview(job:Job,args:Json){
  if(!job.preview)throw Error('No immutable review is available for this job');
  const value=job.preview;
  if(value.kind==='app-local-reset'){const offset=args.cursor?decode(args.cursor):{offset:0,hash:value.previewHash};if(offset.hash!==value.previewHash||!Number.isSafeInteger(offset.offset)||offset.offset<0)throw Error('Exact app reset preview cursor required');const end=offset.offset+(args.limit??25);return bounded({jobId:job.id,...structuredClone(value),owners:value.owners.slice(offset.offset,end),nextCursor:end<value.owners.length?encode({offset:end,hash:value.previewHash}):null},128*1024);}
  if(job.operation==='recovery.cache.preview')return bounded({jobId:job.id,...structuredClone(value),nextCursor:null},128*1024);
  if(value.archivePlanId||value.kind==='native-new-destination-restore'){
   const cursor=args.cursor?decode(args.cursor,16384):{path:'',hash:value.previewHash};
   if(cursor.hash!==value.previewHash||typeof cursor.path!=='string'||cursor.path.length>8192)throw Error('Preview cursor does not match this immutable review');
   const page=await this.native(job,value.kind==='native-new-destination-restore'?'maintenance.restore.manifest':'maintenance.archive.manifest',{previewHash:value.previewHash,cursor:cursor.path,limit:args.limit??25});
   if(page.previewHash!==value.previewHash||(value.archivePlanId&&!['explicit-selected-native-only','full-configured-native-authority'].includes(page.coverage))||!Array.isArray(page.items)||page.items.length>(args.limit??25))throw Error('Invalid native manifest page');
   return bounded({jobId:job.id,previewHash:value.previewHash,coverage:value.coverage,containsPrivateContent:true,credentialCoverage:value.credentialCoverage,sessions:job.sessions.map(row=>row.session),parts:value.spec?.parts??['full-native-authority'],bytes:value.inventory.bytes,totalEntries:value.inventory.entries,ownerCoverage:value.ownerCoverage,restore:value.kind==='native-new-destination-restore'?{destination:value.destination,requiresRuntimeQualification:true,requiresConfigurationReview:true,overwritesExisting:false,startsWorker:false}:undefined,items:page.items.map((row:Json)=>({kind:row.status,path:row.path,...(row.reason?{reason:row.reason,classification:row.classification}:{}),...(row.bytes!==undefined?{bytes:row.bytes,sha256:row.sha256}:{})})),nextCursor:page.nextCursor?encode({path:page.nextCursor,hash:value.previewHash}):null,omissions:value.omissions,exclusions:value.exclusions,reset:null},128*1024);
  }
  const cursor=args.cursor?decode(args.cursor):{offset:0,hash:value.previewHash};
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
  if(params.version!==1||params.topic!=='recovery'||!Object.hasOwn(this.definitions,params.operation))throw Error('Unadvertised recovery action or scope');
  const selected=typeof context.session==='string'?context.session:context.session?.uri;
  if(!['host','ahp-root://'].includes(params.channel)&&(params.channel!==selected||!/^ahp-session:\/[^/?#]+$/.test(params.channel)))throw Error('Recovery action requires authenticated host or exact selected session scope');
  const operation=String(params.operation),args=bounded(params.args??{},32768);validate(this.definitions[operation].schema,args);
  if(selected&&args.sessionId&&selected!==args.sessionId)throw Error('Recovery routing selector differs from authenticated session');
  const account=await this.account(context,operation,args);
  if(['recovery.appReset.apply','recovery.appReset.restore'].includes(operation)){
   const prepared=this.own(account,args.preparedJobId,context);
   if(prepared.operation!=='recovery.appReset.prepare'||!prepared.preview)throw Error('Exact account-owned app reset review required');
   const granted=await this.options.authorize(context,operation,Object.freeze({...structuredClone(args),parts:structuredClone(prepared.preview.parts),credentialsReviewed:prepared.args.credentialsReviewed===true,privateContentReviewed:prepared.args.privateContentReviewed===true}));
   if(granted?.accountId!==account)throw Error('Reset authorization identity changed');
  }
  let result:Json;
  if(operation==='recovery.list')result=this.list(account,args);
  else if(operation==='recovery.job')result=this.descriptor(this.own(account,args.jobId,context));
  else if(operation==='recovery.command'){const row=this.store.command(account,args.commandId);result=row?this.descriptor(this.own(account,row.id,context)):{available:false,commandId:args.commandId};}
  else if(operation==='recovery.cache.page')result=await this.cachePage(this.own(account,args.jobId,context),args);
  else if(operation==='recovery.preview')result=await this.preview(this.own(account,args.jobId,context),args);
  else if(operation==='recovery.reconcile')result=await this.reconcile(this.own(account,args.jobId,context),context);
  else{try{result=await this.enqueue(account,operation,args,token(params.commandId,'durable command identity'),context);}catch(error){if(!knownRefusal(error))throw error;return {accepted:false,result:{accepted:false,executed:false,reason:'recovery-intake-refused'},updates:[]};}}
  return {accepted:result.accepted!==false,result,updates:[]};
 }
 private assertIntake(){if(this.store.fence())throw Object.assign(Error('Recovery intake is fenced'),{data:{executed:false}});if(this.store.unsettled())throw Object.assign(Error('Another recovery job is pending or uncertain; inspect it before new work'),{data:{executed:false}});}
 private async enqueue(account:string,operation:string,args:Json,commandId:string,context:Context){
  const signature=digest(canonical({operation,args})),prior=this.store.command(account,commandId);
  if(prior){if(prior.signature!==signature)throw Error('Recovery command identity has different exact arguments');return this.descriptor(this.own(account,prior.id,context));}
  this.assertIntake();
  if(operation.startsWith('recovery.restore.')&&context.origin==='agent')throw Error('Full native restore requires explicit account-level user review');
  let sessions:Job['sessions'],prepared:Job|undefined,snapshot:Job|undefined;
  if(operation.startsWith('recovery.appReset.')){
   if(context.origin==='agent')throw Error('App-local reset requires explicit account-level user authorization');
   sessions=[];
   if(operation==='recovery.appReset.prepare'){
    if(args.parts.includes('notifications.credentials')&&args.credentialsReviewed!==true)throw Error('Explicit private credential retention review required');
    if(args.restoreResetJobId){const original=this.own(account,args.restoreResetJobId);if(original.state!=='succeeded'||original.operation!=='recovery.appReset.apply'||JSON.stringify(original.result?.parts)!==JSON.stringify(args.parts))throw Error('Exact original app reset parts required');}
   }else{
    prepared=this.own(account,args.preparedJobId);
    if(prepared.operation!=='recovery.appReset.prepare'||prepared.state!=='prepared'||prepared.preview?.previewHash!==args.previewHash)throw Error('Exact completed app reset review required');
    if(operation==='recovery.appReset.restore'){const original=this.own(account,args.resetJobId);if(original.state!=='succeeded'||original.operation!=='recovery.appReset.apply'||original.result?.postResetRevision!==args.expectedPostResetRevision||prepared.args.restoreResetJobId!==original.id)throw Error('Fresh restore review and exact original app reset required');}
    else if(prepared.args.restoreResetJobId)throw Error('Restore review cannot authorize reset');
   }
  }else if(operation==='recovery.cache.scan'){
   const own=typeof context.session==='string'?context.session:context.session?.uri;
   if(args.sessionId&&own&&args.sessionId!==own)throw Error('Cache selector differs from authenticated session');
   const selected=args.sessionId??own;sessions=[];
   if(selected){const native=await this.options.resolveSession(selected,context);if(native.nativeAuthority!==this.options.nativeAuthority)throw Error('Cache scan belongs to another native authority');sessions=[{session:selected,...native}];}
  }else if(operation==='recovery.cache.preview'){
   const scan=this.own(account,args.scanJobId,context);this.cacheScan(scan,args.revision);sessions=structuredClone(scan.sessions);
   if(args.sessionId&&(sessions.length!==1||sessions[0].session!==args.sessionId))throw Error('Cache selector changed');
  }else if(operation==='recovery.restore.prepare'){
   snapshot=this.own(account,args.snapshotJobId,context);
   if(snapshot.state!=='succeeded'||snapshot.operation!=='recovery.snapshot'||snapshot.result?.sha256!==args.sha256||snapshot.result?.format!=='amplifier-native-authority'||snapshot.result?.version!==1)throw Error('Exact successful full-native archive required');
   sessions=structuredClone(snapshot.sessions);
   if(args.sessionId&&(sessions.length!==1||sessions[0].session!==args.sessionId))throw Error('Recovery selector must match the exact snapshot session');
  }else if(['recovery.prepare','recovery.archive.prepare','recovery.reset.prepare'].includes(operation)){
   const uris:string[]=operation!=='recovery.reset.prepare'?args.sessions:[args.sessionId],own=typeof context.session==='string'?context.session:context.session?.uri;
   if(args.sessionId&&(uris.length!==1||uris[0]!==args.sessionId))throw Error('Recovery selector must match the exact single selected session');
   if(context.origin==='agent'&&(uris.length!==1||uris[0]!==own))throw Error('Agent recovery cannot select another conversation');
   if(operation==='recovery.archive.prepare'){
    const selected=args.workspaceConfigurationFor??[];
    if(selected.some((uri:string)=>!uris.includes(uri)))throw Error('Workspace configuration must belong to an explicitly selected conversation');
    if(args.parts.includes('workspace-configuration')&&!selected.length||!args.parts.includes('workspace-configuration')&&selected.length)throw Error('Explicit workspace configuration selection must match requested parts');
    if(args.parts.includes('full-native-authority')&&(args.parts.length!==1||uris.length||selected.length||context.origin==='agent'))throw Error('Full native authority requires a separate explicit account-level selection with no session aliases');
    if(args.parts.some((part:string)=>part.startsWith('session-'))&&!uris.length)throw Error('Native session parts require explicit sessions');
    if(args.parts.includes('native-retained-archives')&&!args.parts.includes('native-maintenance-records'))throw Error('Retained archives require their native maintenance records');
    if(args.includeCredentials&&!args.parts.some((part:string)=>['shared-configuration','full-native-authority'].includes(part)))throw Error('Credential inclusion requires shared configuration');
   }
   sessions=[];
   for(const session of uris){const resolved=await this.options.resolveSession(session,context);token(resolved.nativeSessionId,'native session identity');if(resolved.nativeAuthority!==this.options.nativeAuthority||typeof resolved.historyCwd!=='string'||!resolved.historyCwd)throw Error('Recovery selection is outside the configured native authority');sessions.push({session,...resolved});}
   if(new Set(sessions.map(row=>row.nativeSessionId)).size!==sessions.length)throw Error('Native recovery selection contains aliases for the same session');
   if(args.includeCredentials===true&&args.credentialsReviewed!==true)throw Error('Credential inclusion requires explicit review and authorization');
  }else{
   prepared=this.own(account,args.preparedJobId);
   if(prepared.state!=='prepared'||!prepared.preview||prepared.preview.previewHash!==args.previewHash)throw Error('An exact completed immutable review is required');
   const reset=operation.startsWith('recovery.reset.'),restore=operation==='recovery.restore.apply',cache=operation==='recovery.cache.clear';
   if(!(cache?['recovery.cache.preview']:restore?['recovery.restore.prepare']:reset?['recovery.reset.prepare']:['recovery.prepare','recovery.archive.prepare']).includes(prepared.operation))throw Error('Review is for a different recovery operation');
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
  const now=Date.now(),job:Job={id:randomUUID(),accountId:account,commandId,operation,args:structuredClone(args),signature,state:'queued',createdAt:now,updatedAt:now,revision:0,context:structuredClone(context),sessions,fenceCommandId:'',...(snapshot?{snapshotJobId:snapshot.id}:{}),...(prepared?{preparedJobId:prepared.id,preview:structuredClone(prepared.preview)}:{})};
  if(operation==='recovery.cache.preview')job.context=structuredClone(this.own(account,args.scanJobId,context).context);
  if(operation==='recovery.cache.clear')job.context=structuredClone(prepared!.context);
  job.fenceCommandId=child(job,'quiescence');this.store.insert(job);this.notify();
  // Return the host capability admission before attempting to close host intake.
  setImmediate(()=>{if(this.closed)return;const task=this.run(job).catch(()=>{job.state='unknown';job.reason='owner-job-failed-no-replay';this.changed(job);}).finally(()=>{this.tasks.delete(task);try{this.options.onMayBeIdle?.();}catch{/* Advisory only. */}});this.tasks.add(task);});
  return this.descriptor(job);
 }
 private cacheScan(job:Job,revision:string){
  if(job.operation!=='recovery.cache.scan'||job.state!=='succeeded'||job.result?.revision!==revision||!/^[a-f0-9]{32}$/.test(job.result.scanId))throw Error('Exact completed cache scan revision required');
  return job.result;
 }
 private async cachePage(job:Job,args:Json){
  const scan=this.cacheScan(job,args.revision),page=await this.native(job,'maintenance.cache.page',{scanId:scan.scanId,revision:args.revision,...(args.cursor?{cursor:args.cursor}:{}),limit:args.limit??25});
  if(page.scanId!==scan.scanId||page.revision!==args.revision||!Array.isArray(page.items)||page.items.length>(args.limit??25)||page.coverage?.sourceRetirement!==false)throw Error('Invalid native cache page');
  return bounded({jobId:job.id,...page},128*1024);
 }
 private cacheResult(job:Job,result:Json){
  if(job.operation==='recovery.cache.scan'){
   if(!/^[a-f0-9]{32}$/.test(result.scanId)||!/^[a-f0-9]{64}$/.test(result.revision)||!Number.isFinite(result.expiresAt)||result.coverage?.sourceRetirement!==false||result.coverage?.canonicalHistoryScanned!==false)throw Error('Invalid native cache inventory result');
  }else if(job.operation==='recovery.cache.clear'){
   if(result.coverage!=='verified-native-owned-bytecode-only'||result.sourceDirectoriesRemoved!==0||result.canonicalFilesChanged!==0||result.replayed!==false||!Array.isArray(result.removedIds)||result.removedIds.length!==job.preview?.items?.length||new Set(result.removedIds).size!==result.removedIds.length||result.removedBytes!==job.preview?.bytes||result.removedIds.some((id:any)=>typeof id!=='string'||!job.preview?.items?.some((row:Json)=>row.id===id)))throw Error('Invalid native derived-cache clearing proof');
  }
  this.saveResult(job,result);job.terminalState='succeeded';
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
    if(job.operation.startsWith('recovery.appReset.')){
     const prior=job.preparedJobId?this.own(job.accountId,job.preparedJobId):undefined,originalId=job.args.resetJobId??job.args.restoreResetJobId;
     try{await this.appResets.perform(job,()=>this.changed(job),prior,originalId?this.own(job.accountId,originalId):undefined);}catch(error){job.terminalState=undefined;job.reason='app-reset-owner-outcome-unknown-no-replay';this.changed(job);}
     return;
    }
    if(job.operation.startsWith('recovery.cache.')){
     // These native operations retain and join their own OS writer admission;
     // they do not create a selected-session snapshot lease.
     job.nativeLeaseReleased=true;job.nativeOperation=job.operation.replace('recovery.','maintenance.');
     if(job.operation!=='recovery.cache.preview')job.nativeCommandId=child(job,'effect');this.changed(job);
     try{
      if(job.operation==='recovery.cache.preview'){
       const scan=this.cacheScan(this.own(job.accountId,job.args.scanJobId),job.args.revision);
       const preview=await this.native(job,job.nativeOperation,{scanId:scan.scanId,revision:job.args.revision,ids:job.args.ids,reviewed:true});
       if(!/^[a-f0-9]{64}$/.test(preview.previewHash)||preview.scanId!==scan.scanId||preview.revision!==scan.revision||preview.coverage!=='verified-native-owned-bytecode-only'||preview.sourceDirectoriesRemoved!==0||preview.canonicalFilesChanged!==0||!Array.isArray(preview.items)||preview.items.length!==job.args.ids.length||preview.items.some((row:Json)=>!job.args.ids.includes(row.id)||row.clearable!==true||row.kind!=='python-bytecode'))throw Error('Invalid native bytecode review');
       job.preview=bounded(preview,128*1024);job.nativeResult={state:'prepared',operation:job.nativeOperation,previewHash:preview.previewHash};job.terminalState='prepared';
      }else{
       const result=await this.native(job,job.nativeOperation,{commandId:job.nativeCommandId,...(job.operation==='recovery.cache.clear'?{previewHash:job.args.previewHash}:{})});
       const receipt=result.receipt;if(receipt?.commandId!==job.nativeCommandId||receipt?.operation!==job.nativeOperation)throw Error('Exact native cache receipt required');
       if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult=receipt;job.terminalState='refused';job.reason='cache-refused-no-files-changed';}
       else{if(receipt.state!=='succeeded')throw Error('Cache effect is not conclusively settled');this.cacheResult(job,result);}
      }
     }catch(error){
      if(job.operation==='recovery.cache.preview'||knownRefusal(error)){job.terminalState='refused';job.reason=job.operation==='recovery.cache.preview'?'cache-review-unavailable-no-files-changed':'cache-refused-no-files-changed';job.nativeResult={state:'refused',operation:job.nativeOperation,commandId:job.nativeCommandId,executed:false};}
      else job.reason='cache-outcome-unknown-no-replay';
     }
     this.changed(job);return;
    }
    if(job.operation==='recovery.restore.prepare'){
     job.nativeLeaseReleased=true;job.nativeOperation='maintenance.restore.preview';this.changed(job);
     try{
      const snapshot=this.own(job.accountId,job.snapshotJobId!);
      const preview=await this.native(job,'maintenance.restore.preview',{artifactId:snapshot.result!.artifactId,sha256:job.args.sha256,destination:job.args.destination,privateContentReviewed:true,credentialsReviewed:job.args.credentialsReviewed??false});
      if(preview.kind!=='native-new-destination-restore'||!/^[a-f0-9]{64}$/.test(preview.previewHash)||!preview.inventory||preview.startsWorker!==false||preview.overwritesExisting!==false||preview.coverage?.completeProductBackup!==false)throw Error('Invalid native restore review');
      job.preview={...preview,ownerCoverage:preview.coverage,coverage:'full-configured-native-authority'};job.nativeResult={state:'prepared',operation:job.nativeOperation,previewHash:preview.previewHash};job.terminalState='prepared';
     }catch(error){if(knownRefusal(error)){job.terminalState='refused';job.reason='restore-preview-refused-no-destination-change';}else job.reason='restore-preview-outcome-unknown-no-replay';}
     this.changed(job);return;
    }
    if(job.operation==='recovery.restore.apply'){
     job.nativeLeaseReleased=true;job.nativeOperation='maintenance.restore.apply';job.nativeCommandId=child(job,'effect');this.changed(job);
     try{
      const result=await this.native(job,job.nativeOperation,{commandId:job.nativeCommandId,previewHash:job.args.previewHash});
      if(result.receipt?.state!=='succeeded'||result.restored!==true||result.previewHash!==job.args.previewHash||result.startsWorker!==false||result.overwritesExisting!==false)throw Error('Native restore effect lacks a conclusive exact result');
      this.saveResult(job,result);job.terminalState='succeeded';
     }catch(error){if(knownRefusal(error)){job.terminalState='refused';job.reason='restore-refused-no-destination-change';}else job.reason='restore-outcome-unknown-no-replay';}
     this.changed(job);return;
    }
    if(job.operation==='recovery.archive.prepare'){
     job.nativeLeaseReleased=true; // Plan operations never acquire a snapshot lease or change canonical sources.
     const perform=async(operation:string,args:Json)=>{
      job.nativeOperation=operation;job.nativeCommandId=child(job,operation);this.changed(job);
      const result=await this.native(job,operation,{...args,commandId:job.nativeCommandId});
      if(result.receipt?.state!=='succeeded')throw Error('Native archive plan has no conclusive successful receipt');
      return result;
     };
     try{
      const created=await perform('maintenance.archive.create',{parts:job.args.parts,privateContentReviewed:true,includeCredentials:job.args.includeCredentials??false,credentialsReviewed:job.args.credentialsReviewed??false});
      const selected=job.sessions.filter(row=>(job.args.workspaceConfigurationFor??[]).includes(row.session));
      const added=job.sessions.length||selected.length?await perform('maintenance.archive.add',{planId:created.planId,expectedRevision:created.revision,sessions:job.sessions.map(row=>({nativeSessionId:row.nativeSessionId,cwd:row.historyCwd})),workspaces:[...new Set(selected.map(row=>row.historyCwd))]}):{revision:created.revision};
      const prepared=await perform('maintenance.archive.prepare',{planId:created.planId,expectedRevision:added.revision});
      const {receipt,...preview}=prepared;
      if(!/^[a-f0-9]{64}$/.test(preview.previewHash)||!preview.archivePlanId||!preview.inventory||!['explicit-selected-native-only','full-configured-native-authority'].includes(preview.coverage))throw Error('Invalid native archive review');
      job.preview=preview;job.nativeResult={state:'succeeded',commandId:job.nativeCommandId,operation:job.nativeOperation};job.terminalState='prepared';
     }catch(error:any){
      if(error?.data?.reason==='native-archive-plan-failed'&&error.data.settled===true&&error.data.canonicalFilesChanged===0&&error.data.commandId===job.nativeCommandId){job.terminalState='refused';job.reason='archive-plan-failed-no-canonical-change';}
      else job.reason='archive-plan-outcome-unknown-no-replay';
     }
     this.changed(job);return;
    }
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
  job.releaseEvidence={owner:'recovery',receiptId:'recovery:'+job.id,jobId:job.id,...job.fence,outcome:'unchanged',terminalState:job.terminalState,nativeLeaseReleased:true,nativeLeaseDisposition:job.leaseId?'released':'not-acquired',nativeAuthority:this.options.nativeAuthority,operation:job.operation,...(job.appResetCommands?{appResetOwners:Object.entries(job.appResetCommands).map(([ownerId,commandId])=>({ownerId,commandId,receiptHash:job.appResetReceipts?.[ownerId]?digest(job.appResetReceipts[ownerId]):null,state:job.appResetReceipts?.[ownerId]?.state??'unavailable'}))}:{}),nativeCommandId:job.nativeCommandId??null,nativeResultHash:job.nativeResult?digest(job.nativeResult):null,settledAt:Date.now()};this.changed(job);
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
  if(job.operation.startsWith('recovery.appReset.')){await this.appResets.inspect(job,()=>this.changed(job));if(job.terminalState&&job.nativeLeaseReleased)this.recordReleaseEvidence(job);}
  if(job.nativeCommandId){
   const inspected=await this.options.nativeAdmin('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
   if(['recovery.cache.scan','recovery.cache.clear'].includes(job.operation)&&job.nativeLeaseReleased){
    const receipt=inspected.receipt;
    if(receipt?.commandId===job.nativeCommandId&&receipt.operation===job.nativeOperation){
     if(receipt.state==='succeeded'){this.cacheResult(job,{...receipt.result,receipt});this.recordReleaseEvidence(job);}
     else if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult=receipt;job.terminalState='refused';job.reason='cache-refused-no-files-changed';this.recordReleaseEvidence(job);}
    }
   }
   if(job.operation==='recovery.archive.prepare'&&job.nativeOperation?.startsWith('maintenance.archive.')&&job.nativeLeaseReleased){
    const receipt=inspected.receipt;
    if(receipt?.commandId===job.nativeCommandId&&receipt?.operation===job.nativeOperation){
     if(receipt.state==='succeeded'){
      if(job.nativeOperation==='maintenance.archive.prepare'){
       const result=receipt.result;
       if(result?.archivePlanId&&/^[a-f0-9]{64}$/.test(result.previewHash)&&['explicit-selected-native-only','full-configured-native-authority'].includes(result.coverage)){job.preview=result;job.terminalState='prepared';}
      }else{job.terminalState='refused';job.reason='incomplete-private-plan-retained-no-replay';}
     }else if(receipt.state==='failed'&&receipt.settled===true&&receipt.canonicalFilesChanged===0){job.terminalState='refused';job.reason='archive-plan-failed-no-canonical-change';}
     if(job.terminalState){job.nativeResult={state:receipt.state,commandId:receipt.commandId,operation:receipt.operation};this.recordReleaseEvidence(job);}
    }
   }
   if(job.operation==='recovery.restore.apply'&&job.nativeOperation==='maintenance.restore.apply'&&job.nativeLeaseReleased){
    const proof=await this.options.nativeAdmin('maintenance.restore.inspect',{restoreCommandId:job.nativeCommandId},context),receipt=proof.receipt;
    if(proof.restoreCommandId===job.nativeCommandId&&receipt?.commandId===job.nativeCommandId&&receipt.operation==='maintenance.restore.apply'){
     if(proof.finalized===true&&proof.pending===false&&receipt.state==='succeeded'&&receipt.result?.previewHash===job.args.previewHash){this.saveResult(job,{...receipt.result,receipt});job.terminalState='succeeded';this.recordReleaseEvidence(job);}
     else if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult={state:'refused',commandId:receipt.commandId,operation:receipt.operation};job.terminalState='refused';this.recordReleaseEvidence(job);}
    }
   }
   // A canonical reset receipt alone cannot prove its pending finalization marker was removed.
   if(job.operation==='recovery.snapshot'&&job.nativeOperation==='maintenance.snapshot'&&inspected.receipt?.state==='succeeded'&&job.nativeLeaseReleased){this.saveResult(job,{...inspected.receipt.result,receipt:inspected.receipt});job.terminalState='succeeded';this.recordReleaseEvidence(job);}
   else if(!job.releaseEvidence){job.nativeResult={...(job.nativeResult??{}),inspectedReceiptState:inspected.receipt?.state??'unavailable'};this.changed(job);}
  }
  const receipt=await this.options.quiescence.quiescenceReceipt(job.fenceCommandId);
  if(job.releaseEvidence&&job.terminalState&&job.nativeLeaseReleased){
   if(receipt?.released===true&&receipt.intakeClosed===false&&receipt.fenceId===job.fence?.fenceId){job.state=job.terminalState;job.reason=undefined;this.changed(job);}
   else if(job.fence)await this.releaseHost(job);
  }
  return this.descriptor(job);
 }
 /** Exempts only this owner's exact persisted pre-effect job, never unrelated work. */
 private liveParticipant?:FenceContext;
 readonly quiescenceParticipant=managedParticipant(retentionParticipant({
  id:'recovery',serviceStop:{version:1 as const},
  acquire:async(context:Readonly<FenceContext>)=>{
   if(this.closed||this.store.fence())return null;
   context=this.participantContext(context);
   const own=this.store.db.prepare("SELECT id FROM jobs WHERE state='quiescing' AND json_extract(payload,'$.fenceCommandId')=? LIMIT 1").get(context.commandId);
   const job=own?this.store.get(String(own.id)):undefined;
   if(job&&context.purpose!=='recovery'||this.store.unsettled(job?.id))return null;
   this.store.setFence({...context,...(job?{jobId:job.id}:{})});this.liveParticipant=structuredClone(context);
   return {ownerId:'recovery',fenceId:context.fenceId,release:async(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.releaseParticipant(context,outcome,proof,true)};
  },
  reconcileRelease:async(context:Readonly<FenceContext>&{outcome:'unchanged'|'ready';proof:ReleaseProof})=>this.releaseParticipant(context,context.outcome,context.proof),
 },args=>emptyRetention(args.context,args,this.store.fence())),args=>emptyManaged(args.context,args,this.store.fence()));
 private participantContext(value:Readonly<FenceContext>):FenceContext{const result={} as FenceContext;for(const key of ['fenceId','commandId','purpose','instanceId','dataScope'] as const){if(typeof value[key]!=='string'||!value[key]||value[key].length>200||/[\x00-\x1f]/.test(value[key]))throw Error('Bounded exact recovery participant context required');(result as Json)[key]=value[key];}if(value.purpose==='service-stop'){result.serviceIdentity=serviceIdentity(value.serviceIdentity);if(result.serviceIdentity.instanceId!==value.instanceId||result.serviceIdentity.dataScope!==value.dataScope)throw Error('Service identity differs from recovery participant');}else if(value.serviceIdentity)throw Error('Service identity requires service-stop');return result;}
 private async releaseParticipant(context:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'},liveRollback=false){
  const exact=this.participantContext(context);
  const signature=digest(canonical({context:exact,outcome,proof})),prior=this.store.releaseReceipt(context.fenceId);
  if(prior){if(prior.commandId!==context.commandId||prior.signature!==signature)throw Error('Recovery release identity has different exact proof');return;}
  const fence=this.store.fence();if(!fence||evidenceKey(this.participantContext(fence as FenceContext))!==evidenceKey(exact))throw Error('Exact recovery participant fence required');
  if(outcome==='unknown'){this.liveParticipant=undefined;return;}
  const liveRefusal=liveRollback&&outcome==='unchanged'&&proof&&'kind' in proof&&proof.kind==='admission-refused'&&Object.keys(proof).length===1&&this.liveParticipant&&evidenceKey(this.liveParticipant)===evidenceKey(exact);if(exact.purpose==='service-stop'&&!liveRefusal)validateServiceRelease(exact,outcome,proof);
  if(proof&&'kind' in proof&&proof.kind==='admission-refused'){
   const job=fence.jobId?this.store.get(fence.jobId):undefined;
   if(job&&(job.state!=='quiescing'||job.nativeCommandId||job.leaseId))throw Error('Recovery effect has already started');
  }else{
   const p=proof as ReleaseProof|undefined;
   if(!p||p.verified!==true||p.fenceId!==context.fenceId||p.commandId!==context.commandId||p.outcome!==outcome||p.dataScope!==context.dataScope||!p.instanceId||!p.receiptId)throw Error('Authenticated coordinator release proof required');
   if(fence.jobId){const job=this.store.get(fence.jobId);if(outcome!=='unchanged'||p.instanceId!==context.instanceId||!job?.releaseEvidence||!job.nativeLeaseReleased||!job.terminalState||p.receiptId!==job.releaseEvidence.receiptId)throw Error('Recovery job has no conclusive exact release receipt');}
  }
  const evidence=fence.jobId?this.store.get(fence.jobId)?.releaseEvidence:undefined;
  this.store.completeRelease(exact,signature,evidence);this.liveParticipant=undefined;
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
