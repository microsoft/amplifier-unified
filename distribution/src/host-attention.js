import {createHash} from 'node:crypto';

// Read only already advertised, passive owner projections. Never enumerate
// transcripts, invoke actions, refresh authentication, or contact providers.
export const attentionSources=['provider-catalog','connectors','application-updates','maintenance','diagnostics','feedback'];
const destinations={
 'provider-catalog':['ai-connections','ai-connections','AI connections'],
 connectors:['smart-tools','tool-connections','Tool connections'],
 'application-updates':['updates','updates','Application updates'],
 maintenance:['updates','updates','Agent updates'],
 diagnostics:['advanced','diagnostics','Diagnostics'],
 feedback:['feedback','feedback','Feedback'],
};
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const failure=row=>['failed','error','rejected','unknown','interrupted'].includes(row?.status)||row?.outcome==='unknown';
export function projectHostAttention(topic,data){
 const items=[];let truncated=false;
 const [section,page,label]=destinations[topic];
 const add=(identity,kind,fact,title,detail,acknowledge=true,extra={})=>items.push({
  id:'settings:'+hash([topic,identity,kind]),fingerprint:hash(fact),
  section,page,label,title,detail,acknowledge,...extra,
 });
 const rows=value=>{const list=Array.isArray(value)?value:[];if(list.length>25)truncated=true;return list.slice(0,25);};
 const operations=(value,kind='operation')=>{for(const row of rows(value))if(failure(row)){const identity=row.commandId||row.requestId||row.id;if(typeof identity!=='string'||!identity){truncated=true;continue;}add(identity,kind,[row.status,row.outcome,row.updatedAt,row.updated,row.phase], 'A request needs review','Review its recorded result before deciding what to do next.',true,topic==='feedback'&&row.requestId?{requestId:row.requestId}:{});}};
 if(topic==='provider-catalog'){
  if(data.nativeAdministration?.state==='unavailable')add('administration','unavailable',data.nativeAdministration.state,'Connection settings unavailable','Open AI connections to review the current status.',false);
  for(const row of rows(data.setup?.providers)){
   if(row.enabled===false)continue;
   const accountMissing=row.account&&row.account.connected===false;
   const credentialMissing=row.credential?.supported===true&&row.credentialsConfigured===false;
   if(accountMissing||credentialMissing)add(row.id,'credentials',[accountMissing,credentialMissing,row.account?.authMode],'Connection needs setup','Open AI connections to sign in or choose a credential.',false);
  }
 }else if(topic==='connectors'){
  const value=data.mcp||{};
  for(const row of rows(value.servers))if(['auth-required','error','failed','account-review'].includes(row.connectionState)||['auth-required','error','failed'].includes(row.status)){
   const auth=row.connectionState==='auth-required'||row.status==='auth-required'||row.connectionState==='account-review';
   add(row.id,'connection',[row.status,row.connectionState,row.accountRevision,row.updatedAt],auth?'Tool connection needs sign-in':'Tool connection needs review','Open tool connections to review its current status.',!auth);
  }
  operations(value.operations);
  if(Object.values(value.pages||{}).some(page=>page.nextOffset!=null))truncated=true;
 }else if(topic==='application-updates'){
  const value=data.applicationUpdates||{};operations(value.operations);
  if(value.staged)add('staged','update',[value.staged.commandId,value.staged.target],'An update is ready to review','Open Updates to review the prepared version.');
  if(value.restartUnresolved)add('restart','unresolved',value.restartUnresolved,'Update status needs review','Open Updates to inspect the saved outcome before making another change.',false);
  if(value.releaseNotes?.unreviewedCount>0)add('release-notes','notice',value.releaseNotes,'Release notes need review','Open Updates to review the changes in this version.',false);
 }else if(topic==='maintenance'){
  const value=data.updates||{};operations(value.operations||value.items);
 }else if(topic==='diagnostics'){
  const value=data.diagnostics||{},local=value.local||{};
  if(value.available===false||local.storageError||local.configurationError)add('local','storage',[value.available,local.storageError,local.configurationError],'Diagnostics need attention','Open Diagnostics to review the current recording status.',false);
  if(local.dropped||local.outboxDropped||local.expiredPending)add('local','dropped',[local.dropped,local.outboxDropped,local.expiredPending],'Some diagnostics were not retained','Open Diagnostics to review recording and delivery limits.');
  for(const row of rows(value.destinations))if(failure(row.last)||row.credentialAvailable===false)add(row.id,'delivery',[row.last?.status,row.last?.updated,row.credentialAvailable],'Diagnostic delivery needs review','Open Diagnostics to review the destination.',row.credentialAvailable!==false);
 }else if(topic==='feedback'){
  const value=data.feedback||{};operations(value.items);truncated=value.nextCursor!=null;
 }
 return {items,truncated};
}

export function createHostAttentionReader(owners){
 return async context=>{
  const results=await Promise.all(attentionSources.map(async topic=>{
   const owner=owners().find(candidate=>candidate.manifest?.topics?.[topic]);
   if(!owner)return {topic,status:'not-configured',items:[]};
   const spec=owner.manifest.topics[topic];
   try{
    const record=await owner.read({topic,scope:'host',uri:spec.uri,clientId:context?.clientId},context);
    if(record.topic!==topic||record.scope!=='host')throw Error('Attention source scope differs');
    const value=projectHostAttention(topic,record.data||{});
    return {topic,status:value.truncated?'bounded-page':'available',items:value.items};
   }catch{
    const [section,page,label]=destinations[topic];
    return {topic,status:'unavailable',items:[{id:'settings:'+hash([topic,'unavailable']),fingerprint:hash([topic,'unavailable']),section,page,label,title:'Status could not be checked',detail:'Open this settings page to check its current status.',acknowledge:false}]};
   }
  }));
  const items=results.flatMap(row=>row.items);
  return {items:items.slice(0,200),coverage:{source:'configured-host-settings',complete:items.length<=200&&results.every(row=>['available','not-configured'].includes(row.status)),sources:results.map(({topic,status})=>({topic,status}))}};
 };
}
