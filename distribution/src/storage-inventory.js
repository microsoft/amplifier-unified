import {createHash} from 'node:crypto';
import {isAbsolute,normalize,relative} from 'node:path';

// This is an offline authority declaration. It contains private paths and must
// never be exposed through a browser capability or treated as writer exclusion.
const SCHEMA='amplifier-unified-storage-inventory';
const classes=new Set(['authoritative','derived-rebuildable','reproducible-code','external-excluded','credential-excluded']);
const captures=new Set(['tree','file','native-artifact','omit']);
const text=(v,name,max=512)=>{if(typeof v!=='string'||!v||v.length>max||/[\x00-\x1f]/.test(v))throw Error('Invalid '+name);return v;};
const object=(v,name)=>{if(!v||typeof v!=='object'||Array.isArray(v))throw Error('Invalid '+name);return v;};
const fields=(v,allowed,name)=>{object(v,name);if(Object.keys(v).some(k=>!allowed.includes(k)))throw Error('Unexpected '+name+' field');};
const array=(v,name,max=256)=>{if(!Array.isArray(v)||v.length>max)throw Error('Invalid '+name);return v;};
const unique=(values,name)=>{if(new Set(values).size!==values.length)throw Error('Duplicate '+name);return values;};
const path=(v,name)=>{text(v,name,8192);if(!isAbsolute(v)||normalize(v)!==v||v==='/')throw Error('Explicit normalized non-root '+name+' required');return v;};
const digest=v=>{if(typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v))throw Error('Invalid digest');return v;};
export function canonicalInventoryJSON(value){
 if(value===null||typeof value==='boolean'||typeof value==='string')return JSON.stringify(value);
 if(typeof value==='number'&&Number.isFinite(value))return JSON.stringify(value);
 if(Array.isArray(value))return '['+value.map(canonicalInventoryJSON).join(',')+']';
 if(value&&typeof value==='object'&&Object.getPrototypeOf(value)===Object.prototype)return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonicalInventoryJSON(value[k])).join(',')+'}';
 throw Error('Inventory must contain finite JSON values');
}
export const storageInventoryDigest=value=>createHash('sha256').update(canonicalInventoryJSON(value)).digest('hex');
const inside=(parent,child)=>{const r=relative(parent,child);return r===''||r!=='..'&&!r.startsWith('../')&&!isAbsolute(r);};

/** Validate declaration consistency only. Capture must independently verify paths,
 * stopped identities, immutable bytes and native external-writer attestations. */
export function validateStorageInventory(input){
 if(Buffer.byteLength(canonicalInventoryJSON(input))>1024*1024)throw Error('Inventory exceeds 1 MiB');
 const v=structuredClone(input);
 fields(v,['schema','version','namespace','account','applicationStateDirectory','owners','roots','nativeArtifacts','nativeCapturePlans','omissions','completeEligible','digest'],'inventory');
 if(v.schema!==SCHEMA||v.version!==1)throw Error('Unsupported inventory schema');
 text(v.namespace,'namespace');text(v.account,'account');path(v.applicationStateDirectory,'application state directory');
 const owners=array(v.owners,'owners'),roots=array(v.roots,'roots'),artifacts=array(v.nativeArtifacts,'native artifacts',32),omissions=array(v.omissions,'omissions');
 unique(owners.map(o=>text(o.id,'owner id')),'owner');unique(roots.map(r=>text(r.id,'root id')),'root');unique(artifacts.map(a=>text(a.id,'artifact id')),'artifact');unique(omissions.map(o=>text(o.id,'omission id')),'omission');
 const ownerIds=new Set(owners.map(o=>o.id)),rootIds=new Set(roots.map(r=>r.id));
 for(const o of owners){
  fields(o,['id','schemaVersion','revision','participantId','rootIds','externalStorage'],'owner');
  if(o.schemaVersion!==1)throw Error('Unsupported owner inventory schema');text(o.revision,'owner revision');text(o.participantId,'participant id');
  if(!['none','declared','unresolved'].includes(o.externalStorage))throw Error('Invalid external storage coverage');
  unique(array(o.rootIds,'owner roots').map(id=>{text(id,'owner root');if(!rootIds.has(id))throw Error('Unknown owner root');return id;}),'owner root');
 }
 for(const r of roots){
  fields(r,['id','ownerIds','path','coverage','capture','reason'],'root');path(r.path,'declared root');
  if(!classes.has(r.coverage)||!captures.has(r.capture))throw Error('Invalid root coverage');
  if(['external-excluded','credential-excluded','reproducible-code','derived-rebuildable'].includes(r.coverage)&&r.capture!=='omit'&&r.capture!=='tree'&&r.capture!=='file')throw Error('Invalid classified capture');
  if(r.capture==='omit')text(r.reason,'omission reason',4096);
  if(r.reason!==undefined)text(r.reason,'root reason',4096);
  unique(array(r.ownerIds,'root owners').map(id=>{text(id,'root owner');if(!ownerIds.has(id))throw Error('Unknown root owner');return id;}),'root owner');
  if(!r.ownerIds.length)throw Error('Root owner required');
  for(const id of r.ownerIds)if(!owners.find(o=>o.id===id).rootIds.includes(r.id))throw Error('Root ownership is not reciprocal');
 }
 for(const o of owners)for(const id of o.rootIds)if(!roots.find(r=>r.id===id).ownerIds.includes(o.id))throw Error('Owner root is not reciprocal');
 const app=roots.filter(r=>r.path===v.applicationStateDirectory&&r.capture==='tree'&&r.coverage==='authoritative');
 if(app.length!==1)throw Error('Exactly one complete application state tree required');
 const trees=roots.filter(r=>['tree','file'].includes(r.capture));
 for(let i=0;i<trees.length;i++)for(let j=i+1;j<trees.length;j++)if(inside(trees[i].path,trees[j].path)||inside(trees[j].path,trees[i].path))throw Error('Overlapping captured roots');
 const nativeRootIds=[];
 for(const a of artifacts){
  fields(a,['id','engineId','artifactId','sha256','completeNativeAuthority','manifestDigest','declaredRootIds','externalWritersExcluded','externalWriterEvidence'],'native artifact');
  text(a.engineId,'engine id');if(!/^[a-f0-9]{32}$/.test(a.artifactId))throw Error('Invalid native artifact id');digest(a.sha256);digest(a.manifestDigest);
  if(typeof a.completeNativeAuthority!=='boolean'||typeof a.externalWritersExcluded!=='boolean')throw Error('Explicit native coverage required');
  // Evidence is retained verbatim and must be matched to the native attestation
  // by the archive adapter. It is not manufactured from a boolean here.
  if(a.externalWriterEvidence!==undefined){object(a.externalWriterEvidence,'native writer evidence');if(Buffer.byteLength(canonicalInventoryJSON(a.externalWriterEvidence))>16384)throw Error('Native writer evidence too large');}
  if(a.externalWritersExcluded&&!a.externalWriterEvidence)throw Error('Native writer attestation required');
  unique(array(a.declaredRootIds,'native root ids').map(id=>{text(id,'native root id');const r=roots.find(r=>r.id===id);if(!r||r.capture!=='native-artifact')throw Error('Unknown native artifact root');nativeRootIds.push(id);return id;}),'native root');
  if(!a.declaredRootIds.length)throw Error('Native artifact roots required');
 }
 unique(nativeRootIds,'covered native root');
 const plans=array(v.nativeCapturePlans??[],'native capture plans',32),plannedRoots=[];
 unique(plans.map(plan=>text(plan.engineId,'planned native engine')),'planned native engine');
 for(const plan of plans){
  fields(plan,['engineId','rootIds'],'native capture plan');
  const selected=unique(array(plan.rootIds,'planned native roots').map(id=>{text(id,'planned native root');if(!roots.some(r=>r.id===id&&r.capture==='native-artifact'&&r.coverage==='authoritative'))throw Error('Unknown planned native root');return id;}),'planned native root');
  if(!selected.length)throw Error('Planned native roots required');
  unique(selected.map(id=>roots.find(root=>root.id===id).path),'planned native root path');plannedRoots.push(...selected);
  const matching=artifacts.filter(a=>a.engineId===plan.engineId);
  if(matching.length>1||matching.length===1&&(matching[0].declaredRootIds.length!==selected.length||selected.some(id=>!matching[0].declaredRootIds.includes(id))))throw Error('Sealed native artifact differs from reviewed capture plan');
 }
 unique(plannedRoots,'planned native root');
 if(plans.length&&artifacts.some(a=>!plans.some(p=>p.engineId===a.engineId)))throw Error('Unplanned native artifact engine');
 for(const o of omissions){fields(o,['id','ownerId','reason','blocksComplete'],'omission');text(o.reason,'omission reason',4096);if(typeof o.blocksComplete!=='boolean'||o.ownerId!==undefined&&!ownerIds.has(o.ownerId))throw Error('Invalid explicit omission');}
 const eligible=!owners.some(o=>o.externalStorage==='unresolved')&&!omissions.some(o=>o.blocksComplete)&&
  !roots.some(r=>r.coverage==='authoritative'&&(r.capture==='omit'||r.capture==='native-artifact'&&!nativeRootIds.includes(r.id)))&&
  !artifacts.some(a=>!a.completeNativeAuthority||!a.externalWritersExcluded);
 if(v.completeEligible!==eligible)throw Error('Declared complete eligibility does not match inventory');
 const {digest:recorded,...body}=v;if(digest(recorded)!==storageInventoryDigest(body))throw Error('Inventory digest mismatch');
 return v;
}

/** Producer requires an explicit census. No native config parsing, directory
 * scan, Core startup, or inference occurs to guess missing authorities. */
export function createStorageInventory({namespace,account,applicationStateDirectory,owners,roots,nativeArtifacts=[],nativeCapturePlans=[],omissions=[]}){
 const covered=new Set(nativeArtifacts.flatMap(a=>a.declaredRootIds));
 const completeEligible=!owners.some(o=>o.externalStorage==='unresolved')&&!omissions.some(o=>o.blocksComplete)&&
  !roots.some(r=>r.coverage==='authoritative'&&(r.capture==='omit'||r.capture==='native-artifact'&&!covered.has(r.id)))&&
  !nativeArtifacts.some(a=>!a.completeNativeAuthority||!a.externalWritersExcluded);
 const body={schema:SCHEMA,version:1,namespace,account,applicationStateDirectory,owners,roots,nativeArtifacts,...(nativeCapturePlans.length?{nativeCapturePlans}:{}),omissions,completeEligible};
 return validateStorageInventory({...body,digest:storageInventoryDigest(body)});
}

const packageForOwner={
 resources:'unified-resources-capability','application-updates':'unified-distribution-update-owner',
 'native-administration':'unified-native-capabilities',media:'unified-media-capability',mcp:'unified-mcp-capabilities',
 notifications:'unified-notifications-capability',diagnostics:'unified-diagnostics-capability',operations:'unified-operations-capabilities',
 coordination:'unified-coordination-capability',worktree:'unified-worktree-capability',publishing:'unified-publishing-capability',
 recall:'unified-recall-capability',feedback:'unified-feedback-capability',workspaces:'unified-workspace-capability',
 portability:'unified-portability-capability',recovery:'unified-recovery-capability',history:'unified-history-capability',
};
const configForOwner={workspaces:'workspaces',worktree:'worktrees',history:'historyImport','native-administration':'nativeAdmin','application-updates':'applicationUpdates'};

/** Called through the trusted composition API before an offline stop. The exact
 * runtime participant census and public component provenance are supplied by
 * composition, not by a browser or a guessed list of private owner directories.
 * External declarations belong to a trusted local operator/adapter. */
export function createConfiguredStorageInventory(config,{namespace,quiescence,components,ownerProvenance={},externalRoots=[],externalCoverage={},nativeArtifacts=[],nativeCapturePlans=[],nativeCaptureOwnerId,omissions:extraOmissions=[]}={}){
 if(!quiescence?.requiredOwners?.length)throw Error('Configured quiescence participant census required');
 const ids=unique([...quiescence.requiredOwners],'configured participant');
 const omissions=structuredClone(extraOmissions),owners=ids.map(id=>{
  const source=ownerProvenance[id],artifact=components?.[source?.packageName??('@amplifier/'+packageForOwner[id])];
  if(!artifact)omissions.push({id:'provenance:'+id,ownerId:id,reason:'This configured owner has no known public component provenance',blocksComplete:true});
  const key=source?.configKey??configForOwner[id]??id,settings=config[key];
  const custom=settings?.owner||settings?.command||settings?.executionHost;
  const external=key==='nativeAdmin'||key==='portability'||Boolean(custom)||!artifact||Boolean(source?.runtimeRoot&&!inside(config.stateDirectory,source.runtimeRoot));
  const declared=externalCoverage[id];
  if(declared!==undefined&&!['none','declared','unresolved'].includes(declared))throw Error('Invalid declared external coverage');
  return {id,schemaVersion:1,revision:artifact?.revision??'unresolved',participantId:id,rootIds:['application'],externalStorage:declared??(external?'unresolved':'none')};
 });
 const roots=[{id:'application',ownerIds:ids,path:config.stateDirectory,coverage:'authoritative',capture:'tree'},...structuredClone(externalRoots)];
 for(const r of roots.slice(1))for(const id of r.ownerIds){const owner=owners.find(o=>o.id===id);if(!owner)throw Error('External root owner is not configured');owner.rootIds.push(r.id);}
 // Known externally configured authorities must be covered even when a caller
 // asserts no external data. Owner adapters may add further roots, never remove
 // these facts from the composition's census.
 const requirePath=(ownerId,label,p)=>{
  if(!p||inside(config.stateDirectory,p))return;
  if(!roots.some(r=>r.ownerIds.includes(ownerId)&&inside(r.path,p)&&r.coverage==='authoritative'&&r.capture!=='omit'))omissions.push({id:label,ownerId,reason:'Configured external authority is not included: '+p,blocksComplete:true});
 };
 for(const id of ids)if(ownerProvenance[id]?.runtimeRoot)requirePath(id,'runtime-root:'+id,ownerProvenance[id].runtimeRoot);
 if(config.portability){const id=ids.find(id=>(ownerProvenance[id]?.configKey??configForOwner[id]??id)==='portability');if(!id)throw Error('Configured transfer authority missing from participant census');requirePath(id,'portability-stage',config.portability.stageDir);requirePath(id,'portability-exchange',config.portability.exchangeDir);}
 const managed=config.host?.managedSessionRoot;
 if(managed&&!inside(config.stateDirectory,managed)&&!roots.some(r=>inside(r.path,managed)&&r.coverage==='authoritative'&&['tree','file'].includes(r.capture)))omissions.push({id:'managed-session-files',reason:'Configured managed chat files and allocation markers are outside declared product capture: '+managed,blocksComplete:true});
 // A plan removes only the producer's missing-artifact omission. Its exact
 // roots remain uncovered until a matching capture is sealed; it grants no
 // capture authority and cannot make an inventory complete by itself.
 const plans=array(nativeCapturePlans,'native capture plans',32),plannedRoots=[];
 unique(plans.map(plan=>text(plan.engineId,'planned native engine')),'planned native engine');
 for(const plan of plans){
  fields(plan,['engineId','rootIds'],'native capture plan');
  if(!(config.engines??[]).some(engine=>engine.id===plan.engineId)||config.nativeAdmin?.engine!==plan.engineId)throw Error('Native capture plan requires the configured native administration engine');
  const nativeOwners=ids.filter(id=>id===nativeCaptureOwnerId&&(ownerProvenance[id]?.configKey??configForOwner[id]??id)==='nativeAdmin'&&(ownerProvenance[id]?.packageName??('@amplifier/'+packageForOwner[id]))==='@amplifier/unified-native-capabilities');
  if(nativeOwners.length!==1)throw Error('Exact configured native capture owner required');
  const rootIds=unique(array(plan.rootIds,'planned native roots').map(id=>text(id,'planned native root')),'planned native root');
  if(!rootIds.length)throw Error('Planned native roots required');
  const declaredRoots=roots.filter(root=>root.capture==='native-artifact'&&root.ownerIds.includes(nativeOwners[0]));
  if(rootIds.length!==declaredRoots.length||rootIds.some(id=>!declaredRoots.some(root=>root.id===id&&root.coverage==='authoritative')))throw Error('Native capture plan must bind all exact declared native-owner artifact roots');
  plannedRoots.push(...rootIds);
 }
 unique(plannedRoots,'planned native root');
 for(const engine of config.engines??[]){
  const declared=nativeArtifacts.some(a=>a.engineId===engine.id);
  if(!declared&&!plans.some(plan=>plan.engineId===engine.id))omissions.push({id:'engine:'+engine.id,reason:'Configured engine native authority has no sealed full artifact: '+engine.id,blocksComplete:true});
 }
 if(config.legacyClientState)omissions.push({id:'legacy-client-state',reason:'Imported legacy private client state requires an explicit archive classification',blocksComplete:true});
 return createStorageInventory({namespace:namespace??quiescence.dataScope,account:config.account,applicationStateDirectory:config.stateDirectory,owners,roots,nativeArtifacts,nativeCapturePlans,omissions});
}
