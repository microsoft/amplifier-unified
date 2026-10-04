import {DatabaseSync} from 'node:sqlite';import {mkdirSync,chmodSync} from 'node:fs';import {isAbsolute,join} from 'node:path';import {createHash,randomUUID} from 'node:crypto';
import {HistoryQuiescence} from './quiescence.js';import {importActions,importLimits} from './schemas.js';export {importActions,importLimits};
const digest=value=>createHash('sha256').update(value).digest('hex');
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const signature=value=>digest(JSON.stringify(canonical(value)));
const fault=message=>Object.assign(Error(message),{code:-32602,data:{executed:false}});
function text(value,max=200){if(typeof value!=='string'||!value||value.length>max||/[\x00-\x1f]/.test(value))throw fault('Bounded nonempty import field required');return value}
function keys(value,allowed,required=allowed){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!allowed.includes(key))||required.some(key=>value[key]===undefined))throw fault('Exact import fields required')}
function hash(value){if(typeof value!=='string'||!/^[a-f0-9]{64}$/.test(value))throw fault('SHA256 import identity required');return value}
/** Product metadata and exact receipts only. All transcript/source bytes belong to native storage. */
export function createHistoryCapability(options){
 if(!isAbsolute(options.directory)||!options.engineId||['nativeAdmin','authorizeWorkspace','adoptImportedSession','importAdoptionReceipt'].some(key=>typeof options[key]!=='function'))throw Error('History import requires explicit private storage, engine and public authority callbacks');
 mkdirSync(options.directory,{recursive:true,mode:0o700});const gate=new HistoryQuiescence(options.directory,options.onMayBeIdle,{storeProfile:true});let db;
 try{db=new DatabaseSync(join(options.directory,'history-import.sqlite'));db.exec("PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,value TEXT NOT NULL);CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,workflow TEXT NOT NULL,operation TEXT NOT NULL,signature TEXT NOT NULL,metadata TEXT NOT NULL,status TEXT NOT NULL,result TEXT);CREATE INDEX IF NOT EXISTS commands_workflow ON commands(workflow,status);CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL);INSERT OR IGNORE INTO revision VALUES(1,0);UPDATE commands SET status='unknown' WHERE status='dispatching';");chmodSync(join(options.directory,'history-import.sqlite'),0o600)}catch(error){db?.close();gate.close();throw error}
 db.exec("CREATE INDEX IF NOT EXISTS retention_commands ON commands(status);PRAGMA user_version=1");
 gate.retentionReferences=sessions=>{const unknown=!!db.prepare("SELECT 1 FROM commands WHERE status IN ('dispatching','unknown') LIMIT 1").get();return {coverage:unknown?'partial':'complete',protected:[],omissions:unknown?[{reason:'history-import-unsettled',scope:'owner'}]:[]}};
 let requests=0,closed=false;const locks=new Map();
 const manifest={version:1,topics:{'history-import':{uri:'amplifier-capability://history/import',version:1,scope:'host',watch:true}},actions:Object.fromEntries(Object.keys(importActions).map(operation=>[operation,{topic:'history-import',operation,method:'x-amplifier/capabilityAction'}]))};
 const tx=fn=>{db.exec('BEGIN IMMEDIATE');try{const result=fn();db.exec('COMMIT');return result}catch(error){db.exec('ROLLBACK');throw error}};
 const changed=()=>{db.prepare('UPDATE revision SET value=value+1 WHERE id=1').run();try{Promise.resolve(options.onInvalidate?.('history-import','host')).catch(()=>{})}catch{}};
 const workflow=id=>{const row=db.prepare('SELECT value FROM workflows WHERE id=?').get(text(id));if(!row)throw fault('Selected import workflow is unavailable');return JSON.parse(row.value)};
 gate.managedReferences=args=>gate.retentionReferences(args.sessions); // Native import artifacts are copied, never external execution paths.
 const put=flow=>db.prepare('INSERT OR REPLACE INTO workflows VALUES(?,?)').run(flow.id,JSON.stringify(flow));
 const command=id=>db.prepare('SELECT * FROM commands WHERE id=?').get(text(id));
 const visible=row=>row?{commandId:row.id,operation:row.operation,status:row.status,workflowId:row.workflow,...(row.result?{result:JSON.parse(row.result)}:{}),replayed:false}:null;
 const finishFlow=(flow,row,result)=>{tx(()=>{put(flow);db.prepare("UPDATE commands SET status='succeeded',result=? WHERE id=?").run(JSON.stringify(result),row.id);changed()});return visible(command(row.id))};
 const save=(row,status,result)=>{tx(()=>{db.prepare('UPDATE commands SET status=?,result=? WHERE id=?').run(status,result?JSON.stringify(result):null,row.id);changed()});return visible(command(row.id))};
 const lock=async(id,fn)=>{const prior=locks.get(id)??Promise.resolve(),run=prior.catch(()=>{}).then(fn);locks.set(id,run);try{return await run}finally{if(locks.get(id)===run)locks.delete(id)}};
 const guarded=async(fn,mutating)=>{if(closed)throw fault('History owner is closed');if(requests>=importLimits.concurrentRequests)throw fault('History request capacity reached');requests++;try{return await(mutating?gate.effect(fn):gate.read(fn))}finally{requests--;gate.notice()}};
 const authority=async(flow,context)=>{const cwd=await options.authorizeWorkspace(flow.workingDirectory,context);if(cwd!==flow.workingDirectory)throw fault('Import workspace identity changed');return {...context,workingDirectory:cwd}};
 const native=async(operation,args,flow,context)=>{const result=await options.nativeAdmin(operation,args,await authority(flow,context));if(result!==null&&(!result||typeof result!=='object'||Array.isArray(result)||Buffer.byteLength(JSON.stringify(result))>importLimits.receiptBytes))throw Error('Invalid bounded native import receipt');return result};
 const verified=(row,receipt)=>{if(!receipt||receipt.commandId!==row.id||receipt.operation!==row.operation||!['succeeded','failed','unknown'].includes(receipt.status)||receipt.replayed!==false)throw Error('Native import receipt identity differs');return receipt};
 const apply=async(row,receipt,context)=>{
  verified(row,receipt);if(receipt.status==='unknown')return save(row,'unknown',{reason:'Native import outcome is unknown; no replay'});
  if(receipt.status==='failed')return save(row,'failed',{...(receipt.executed===false?{executed:false}:{}),reason:'Native owner rejected this import command'});
  const flow=workflow(row.workflow),meta=JSON.parse(row.metadata),result=receipt.result;
  if(!result||typeof result!=='object')throw Error('Native import result missing');
  if(row.operation==='history.import.begin'){
   text(result.uploadId);if(result.receivedBytes!==0)throw Error('Native staging baseline differs');flow.uploadId=result.uploadId;flow.phase='uploading';return finishFlow(flow,row,{workflowId:flow.id,uploadId:flow.uploadId,receivedBytes:0,session:flow.session});
  }
  if(row.operation==='history.import.chunk'){
   if(result.uploadId!==flow.uploadId||result.receivedBytes!==meta.offset+meta.bytes||flow.receivedBytes!==meta.offset)throw Error('Native upload offset differs');flow.receivedBytes=result.receivedBytes;return finishFlow(flow,row,{workflowId:flow.id,receivedBytes:flow.receivedBytes});
  }
  if(row.operation==='history.import.preview'){
   hash(result.previewHash);if(result.uploadId!==flow.uploadId||result.sourceSha256!==flow.sha256||result.workReplayed!==false||!result.summary)throw Error('Native review source differs');
   const summary={};for(const key of ['messageCount','sourceMessageCount','interruptedToolCalls']){if(!Number.isSafeInteger(result.summary[key])||result.summary[key]<0||result.summary[key]>20000)throw Error('Invalid import summary count');summary[key]=result.summary[key]}
   summary.title=text(result.summary.title,1000);summary.bundle=result.summary.bundle===null?null:text(result.summary.bundle,2048);summary.roles={};for(const [role,count] of Object.entries(result.summary.roles??{})){if(!['system','developer','user','assistant','tool','function'].includes(role)||!Number.isSafeInteger(count)||count<0||count>20000)throw Error('Invalid import role summary');summary.roles[role]=count}
   if(!Array.isArray(result.summary.omissions)||result.summary.omissions.length>64)throw Error('Invalid import omissions');summary.omissions=result.summary.omissions.map(value=>{const code=text(value.code,128);if(value.count!==undefined&&(!Number.isSafeInteger(value.count)||value.count<0||value.count>20000))throw Error('Invalid import omission count');return {code,...(value.count!==undefined?{count:value.count}:{})}});
   flow.previewHash=result.previewHash;flow.phase='reviewed';return finishFlow(flow,row,{workflowId:flow.id,uploadId:flow.uploadId,previewHash:result.previewHash,sourceSha256:flow.sha256,summary,workReplayed:false});
  }
  if(row.operation==='history.import.commit'){
   if(result.importCommandId!==row.id||result.previewHash!==meta.previewHash||result.sourceSha256!==flow.sha256||result.historyCwd!==flow.workingDirectory||result.executionDirectory!==flow.workingDirectory||result.creationConfirmed!==true||result.workReplayed!==false)throw Error('Native creation proof differs');
   const adoptionId='import-adopt:'+digest(row.id),prior=await options.importAdoptionReceipt(adoptionId),adopted=prior?.status==='completed'?prior.result:prior?((await options.importAdoptionReceipt(adoptionId,{reconcile:true}))?.result):await options.adoptImportedSession({commandId:adoptionId,session:flow.session,engineId:options.engineId,workingDirectory:flow.workingDirectory,importCommandId:row.id,sourceSha256:flow.sha256,previewHash:meta.previewHash});
   if(!adopted||adopted.nativeSessionId!==result.nativeSessionId||adopted.importCommandId!==row.id||adopted.creationConfirmed!==true||adopted.workReplayed!==false)throw Error('Verified host registration incomplete');
   flow.phase='completed';flow.session=adopted.uri;return finishFlow(flow,row,{workflowId:flow.id,session:adopted.uri,nativeSessionId:adopted.nativeSessionId,sourceSha256:flow.sha256,historyRevision:result.historyRevision,creationConfirmed:true,workReplayed:false});
  }
  throw Error('Unrecognized import receipt operation');
 };
 const receipt=async(id,context)=>{const row=command(id);if(!row)return null;const result=visible(row);if(row.status!=='unknown'&&row.status!=='dispatching')return result;const flow=workflow(row.workflow);try{const proof=await native('history.import.receipt',{commandId:row.id},flow,context);if(!proof)return {...result,nativeStatus:'unavailable',requiresReconciliation:false};verified(row,proof);return {...result,nativeStatus:proof.status,requiresReconciliation:proof.status==='succeeded'||proof.status==='failed'}}catch{return {...result,nativeStatus:'unavailable',requiresReconciliation:false}}};
 const reconcile=async(id,context)=>{const initial=command(id);if(!initial)return null;return lock(initial.workflow,async()=>{const row=command(id);if(row.status==='succeeded'||row.status==='failed')return visible(row);const flow=workflow(row.workflow),proof=await native('history.import.receipt',{commandId:row.id},flow,context);if(!proof)return {...visible(row),nativeStatus:'unavailable',requiresReconciliation:false};try{return await apply(row,proof,context)}catch{return save(row,'unknown',{reason:'Import confirmation or registration remains unknown; no replay'})}})};
 const execute=async(operation,args,id,context)=>{
  text(id);if(Buffer.byteLength(JSON.stringify(args))>32768)throw fault('Import action exceeds32KiB');const expected=importActions[operation].schema;keys(args,Object.keys(expected.properties),expected.required);const requestSignature=signature({operation,args});
  const existing=command(id);if(existing){if(existing.operation!==operation||existing.signature!==requestSignature)throw fault('Import command ID conflicts with its original request');return visible(existing)}
  let flow,meta,nativeArgs;
  if(operation==='history.import.begin'){
   const cwd=await options.authorizeWorkspace(text(args.workingDirectory,8192),context);if(!isAbsolute(cwd))throw fault('Canonical workspace authority required');
   if(!['json','jsonl'].includes(args.format)||!Number.isSafeInteger(args.bytes)||args.bytes<1||args.bytes>importLimits.maxBytes)throw fault('JSON/JSONL source must be1..1048576 bytes');hash(args.sha256);if(args.title!==undefined)text(args.title,1000);if(args.bundle!==undefined)text(args.bundle,2048);
   flow={id:randomUUID(),session:'ahp-session:/'+randomUUID(),workingDirectory:cwd,format:args.format,bytes:args.bytes,sha256:args.sha256,receivedBytes:0,phase:'staging'};meta={format:args.format,bytes:args.bytes,sha256:args.sha256,...(args.title?{title:args.title}:{}),...(args.bundle?{bundle:args.bundle}:{})};nativeArgs={commandId:id,...meta};
  }else{flow=workflow(args.workflowId);meta={};}
  return lock(flow.id,async()=>{
   const duplicate=command(id);if(duplicate){if(duplicate.signature!==requestSignature)throw fault('Import command ID conflicts');return visible(duplicate)}
   if(operation!=='history.import.begin'){
    flow=workflow(flow.id);await authority(flow,context);
    if(db.prepare("SELECT 1 FROM commands WHERE workflow=? AND status IN ('dispatching','unknown') LIMIT 1").get(flow.id))throw fault('Resolve this workflow original receipt before another mutation');
    if(flow.phase==='completed')throw fault('Import already completed; no duplicate native creation');
    if(operation==='history.import.chunk'){
     if(flow.phase!=='uploading'||args.offset!==flow.receivedBytes||!Number.isSafeInteger(args.offset)||typeof args.contentBase64!=='string'||args.contentBase64.length>21848||!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(args.contentBase64))throw fault('Exact next upload offset and canonical base64 required');
     const bytes=Buffer.from(args.contentBase64,'base64');if(!bytes.length||bytes.length>importLimits.chunkBytes||args.offset+bytes.length>flow.bytes)throw fault('Import chunk exceeds reviewed source or16KiB limit');meta={offset:args.offset,bytes:bytes.length,sha256:digest(bytes)};nativeArgs={commandId:id,uploadId:flow.uploadId,offset:args.offset,contentBase64:args.contentBase64};
    }else if(operation==='history.import.preview'){
     if(flow.phase!=='uploading'||flow.receivedBytes!==flow.bytes)throw fault('Complete source upload required before review');nativeArgs={commandId:id,uploadId:flow.uploadId};
    }else if(operation==='history.import.commit'){
     if(flow.phase!=='reviewed'||hash(args.previewHash)!==flow.previewHash)throw fault('Exact reviewed import hash required');meta={previewHash:args.previewHash};nativeArgs={commandId:id,previewHash:args.previewHash};
    }else throw fault('Unsupported import mutation');
   }
   tx(()=>{if(operation==='history.import.begin')put(flow);db.prepare('INSERT INTO commands VALUES(?,?,?,?,?,?,NULL)').run(id,flow.id,operation,requestSignature,JSON.stringify(meta),'dispatching');changed()});
   const row=command(id);let returned=false;try{const proof=await native(operation,nativeArgs,flow,context);returned=true;return await apply(row,proof,context)}catch(error){const known=!returned&&error?.data?.executed===false;return save(row,known?'failed':'unknown',{reason:known?'Native import was refused before effect':'Import outcome unknown; inspect original receipt',...(known?{executed:false}:{})})}
  });
 };
 const snapshot=()=>({topic:'history-import',scope:'host',revision:Number(db.prepare('SELECT value FROM revision WHERE id=1').get().value),data:{historyImport:{engineId:options.engineId,configured:true,nativeCapabilityRequired:true,limits:importLimits,originalPreserved:true,workReplayed:false}}});
 return {
  manifest,actionSchemas:async()=>importActions,quiescenceAccess:{'history.import.receipt':'read'},quiescenceParticipant:gate.participant('history-import'),inspectQuiescence:async()=>gate.inspect(),
  read:async({topic,scope,uri})=>{if(topic!=='history-import'||!['host','ahp-root://'].includes(scope)||new URL(uri).origin!=='null'||uri.split('?')[0]!==manifest.topics['history-import'].uri)throw fault('Host-scoped history import resource required');return snapshot()},
  action:async(request,context={})=>{
   if(request.version!==1||request.topic!=='history-import'||!importActions[request.operation]||!['host','ahp-root://'].includes(request.channel))throw fault('Advertised host history import action required');
   const args=request.args??{},read=request.operation==='history.import.receipt';let result;
   if(read||request.operation==='history.import.reconcile'){keys(args,['commandId']);result=await guarded(()=>read?receipt(text(args.commandId),context):reconcile(text(args.commandId),context),!read)}
   else result=await guarded(()=>execute(request.operation,args,request.commandId,context),true);
   return {accepted:result?.status==='succeeded',result,updates:read?[]:[snapshot()],invalidate:read?[]:['history-import']};
  },
  close:async()=>{if(requests)throw Error('History import work is still running');closed=true;gate.assertClosable();db.close();gate.close()},
 };
}
