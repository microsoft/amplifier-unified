import {createHash} from 'node:crypto';
const text={type:'string',minLength:1,maxLength:100,pattern:'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$'};
const define=(description,properties={},required=[])=>({description,schema:{type:'object',properties,required,additionalProperties:false}});
export const applicationBackupSchemas={
 'updates.application.backup.inspect':define('Read complete installation backup status without creating or retrying work.'),
 'updates.application.backup.review':define('Review a complete installation backup, including private content and credentials. Does not stop the app or capture files.'),
 'updates.application.backup.create':define('Create exactly the reviewed complete installation backup. The independent controller briefly stops and resumes the app; keep the original request ID if the connection closes.',{reviewId:text,reviewDigest:{type:'string',pattern:'^[a-f0-9]{64}$'},privateContentReviewed:{const:true},credentialsReviewed:{const:true}},['reviewId','reviewDigest','privateContentReviewed','credentialsReviewed']),
 'updates.application.backup.receipt':define('Inspect the original backup request. Never repeat a missing or uncertain request.',{commandId:text},['commandId']),
 'updates.application.backup.reconcile':define('Inspect retained backup and service-resume evidence without repeating capture or restarting the app.',{commandId:text},['commandId']),
};
export const applicationBackupReads=['inspect','receipt','reconcile'].map(name=>'updates.application.backup.'+name);
const pick=(v,keys)=>Object.fromEntries(keys.filter(k=>v?.[k]!==undefined).map(k=>[k,v[k]]));
const receipt=value=>value?{
 ...pick(value,['commandId','reviewId','status','createdAt','completedAt','updatedAt','reconciledAt','error','workReplayed']),
 ...(value.status==='succeeded'&&value.archive?.completeProduct===true?{archive:{sha256:value.archive.archiveSha256,bytes:value.archive.bytes,manifestDigest:value.archive.manifestDigest,completeProduct:true,contentType:'application/vnd.amplifier.unified-archive',resourceUri:`amplifier-installation-backup://archive/${value.commandId}?sha256=${value.archive.archiveSha256}`}}:{}),
}:null;
export function createApplicationBackupForwarder({coordinator,authorize}){
 if(coordinator?.outlivesDistribution!==true||typeof authorize!=='function')throw Error('external_backup_controller_required');
 const inspect=async()=>{let value;try{value=await coordinator.inspect();}catch{return {available:false,error:'backup_controller_unavailable',workReplayed:false};}return {available:true,active:value.active?{commandId:value.active.commandId}:null,receipts:value.receipts.map(receipt),includesCredentials:true,encrypted:false,workReplayed:false};};
 return {inspect,
  async action(operation,args,commandId,context){
   await authorize(context,operation,{...args,includeCredentials:true});
   if(operation.endsWith('.inspect'))return inspect();
   if(operation.endsWith('.review'))return coordinator.review();
   if(operation.endsWith('.create'))return {receipt:receipt(await coordinator.submit(commandId,args))};
   return {receipt:receipt(await coordinator[operation.endsWith('.reconcile')?'reconcile':'receipt'](args.commandId))};
  },
  resourceProvider:{scheme:'amplifier-installation-backup',async read(params,context){
   await authorize(context,'updates.application.backup.download',{includeCredentials:true});
   const url=new URL(params.uri);
   if(params.channel!=='ahp-root://'||params.encoding!=='base64'||url.protocol!=='amplifier-installation-backup:'||url.hostname!=='archive'||url.hash||[...url.searchParams.keys()].some(k=>!['sha256','offset','maxBytes'].includes(k)))throw Error('backup_archive_resource_invalid');
   const commandId=url.pathname.slice(1),sha256=url.searchParams.get('sha256'),offset=Number(url.searchParams.get('offset')??0),maxBytes=Number(url.searchParams.get('maxBytes')??262144);
   if(!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$/.test(commandId)||!/^([a-f0-9]{64})$/.test(sha256??'')||!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>262144||[...url.searchParams.keys()].some(k=>url.searchParams.getAll(k).length!==1))throw Error('backup_archive_resource_invalid');
   const page=await coordinator.readArchive(commandId,{sha256,offset,maxBytes});
   if(typeof page?.data!=='string'||page.data.length>Math.ceil(maxBytes/3)*4||!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(page.data))throw Error('backup_archive_chunk_invalid');
   const bytes=Buffer.from(page.data,'base64');
   if(page.commandId!==commandId||page.sha256!==sha256||page.offset!==offset||page.encoding!=='base64'||bytes.length>maxBytes||createHash('sha256').update(bytes).digest('hex')!==page.chunkSha256)throw Error('backup_archive_chunk_invalid');
   return {encoding:'base64',contentType:'application/vnd.amplifier.unified-archive',data:page.data};
  }},
 };
}
