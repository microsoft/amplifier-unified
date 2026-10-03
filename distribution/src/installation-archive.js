import {constants} from 'node:fs';
import {open,lstat,realpath,readdir,mkdir,rm,chmod} from 'node:fs/promises';
import {isAbsolute,join,dirname,relative,sep} from 'node:path';
import {createHash} from 'node:crypto';
import {readInstalledServiceConfiguration} from './service.js';
import {readInstallationConfiguration} from './installation.js';
import {validateStorageInventory} from './storage-inventory.js';
import {createInstalledStorageInventory} from './installed-storage-inventory.js';

// A bounded manifest followed by exact byte ranges. No tar extraction, links,
// executable member names or archive-controlled absolute restore destinations.
const MAGIC=Buffer.from('AMPLIFIER-UNIFIED-ARCHIVE-1\n');
const MAX_HEADER=16*1024*1024,MAX_ENTRIES=100000,MAX_BYTES=1024**4;
const digest=value=>createHash('sha256').update(value).digest('hex');
const canonical=value=>JSON.stringify(sort(value));
function sort(value){
 if(Array.isArray(value))return value.map(sort);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,sort(value[k])]));
 return value;
}
const id=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,159}$/.test(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const within=(root,path)=>path===root||path.startsWith(root+sep);
async function syncDirectory(path){const h=await open(path,constants.O_RDONLY);try{await h.sync();}finally{await h.close();}}
function member(name){
 if(typeof name!=='string'||name.length>4096||name.includes('\\')||name.includes('\0')||name.startsWith('/')||
    name.split('/').some(p=>!p||p==='.'||p==='..'))throw Error('archive_member_invalid');
 return name;
}
async function canonicalPath(path,directory){
 if(typeof path!=='string'||!isAbsolute(path)||await realpath(path)!==path)throw Error('archive_canonical_path_required');
 const stat=await lstat(path);
 if(stat.isSymbolicLink()||(directory?!stat.isDirectory():!stat.isFile())||(!directory&&stat.nlink!==1))throw Error('archive_member_type_refused');
 return stat;
}
async function readExact(handle,length,position){
 const bytes=Buffer.alloc(length);let offset=0;
 while(offset<length){const r=await handle.read(bytes,offset,length-offset,position+offset);if(!r.bytesRead)throw Error('archive_truncated');offset+=r.bytesRead;}
 return bytes;
}
async function range(handle,offset,size,consume){
 let total=0;const h=createHash('sha256');
 while(total<size){const bytes=await readExact(handle,Math.min(256*1024,size-total),offset+total);h.update(bytes);await consume?.(bytes);total+=bytes.length;}
 return h.digest('hex');
}
async function fileHash(path){
 const handle=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const before=await handle.stat();if(!before.isFile()||before.nlink!==1||before.size>MAX_BYTES)throw Error('archive_member_type_refused');
  const sha256=await range(handle,0,before.size),after=await handle.stat();
  if(before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ino!==after.ino)throw Error('archive_source_changed');
  return {bytes:before.size,sha256,mode:before.mode&0o777};
 }finally{await handle.close();}
}

/** Materialize one immutable native artifact through its public bounded read
 * API. The adapter may retain this private path for later OFFLINE capture. */
export async function stageNativeInstallationArtifact({artifactId,sha256,directory},{requestNative}){
 if(!/^[a-f0-9]{32}$/.test(artifactId)||!hash(sha256)||typeof requestNative!=='function')throw Error('native_artifact_request_invalid');
 await canonicalPath(directory,true);
 const inspection=await requestNative('maintenance.artifact.inspect',{artifactId,sha256});
 if(inspection?.artifactId!==artifactId||inspection.sha256!==sha256||inspection.integrity?.artifactHashVerified!==true||
    inspection.integrity?.manifestHashVerified!==true||!Number.isSafeInteger(inspection.bytes)||inspection.bytes<1||inspection.bytes>MAX_BYTES)throw Error('native_archive_evidence_mismatch');
 const path=join(directory,artifactId+'.tar'),out=await open(path,'wx',0o600);let completed=false;
 try{
  let offset=0;const total=createHash('sha256');
  while(offset<inspection.bytes){
   const maxBytes=Math.min(262144,inspection.bytes-offset),chunk=await requestNative('maintenance.artifact.read',{artifactId,sha256,offset,maxBytes});
   if(chunk?.artifactId!==artifactId||chunk.sha256!==sha256||chunk.offset!==offset||chunk.bytes!==inspection.bytes||chunk.encoding!=='base64'||
      typeof chunk.data!=='string'||chunk.data.length>4*Math.ceil(maxBytes/3))throw Error('native_archive_chunk_invalid');
   const bytes=Buffer.from(chunk.data,'base64');
   if(!bytes.length||bytes.length>maxBytes||bytes.toString('base64')!==chunk.data||digest(bytes)!==chunk.chunkSha256||
      chunk.nextOffset!==(offset+bytes.length<inspection.bytes?offset+bytes.length:null))throw Error('native_archive_chunk_invalid');
   let n=0;while(n<bytes.length)n+=(await out.write(bytes,n)).bytesWritten;total.update(bytes);offset+=bytes.length;
  }
  if(total.digest('hex')!==sha256)throw Error('native_archive_bytes_changed');
  await out.sync();completed=true;return {path,inspection};
 }finally{await out.close();if(!completed)await rm(path,{force:true});}
}
function inventoryFacts(inventory){
 if(inventory?.schema!=='amplifier-unified-storage-inventory'||inventory.version!==1||!hash(inventory.digest)||
    !Array.isArray(inventory.owners)||!Array.isArray(inventory.roots)||!Array.isArray(inventory.nativeArtifacts)||!Array.isArray(inventory.omissions))throw Error('archive_inventory_invalid');
 const {digest:claimed,...body}=inventory;
 if(digest(canonical(body))!==claimed)throw Error('archive_inventory_digest_mismatch');
 const roots=new Map();
 for(const root of inventory.roots){if(!id(root.id)||roots.has(root.id)||!Array.isArray(root.ownerIds))throw Error('archive_inventory_invalid');roots.set(root.id,root);}
 const participants=new Set(),owners=new Set();
 for(const owner of inventory.owners){
  if(!id(owner.id)||owners.has(owner.id)||owner.schemaVersion!==1||!id(owner.participantId)||!Array.isArray(owner.rootIds)||
    !['none','declared','unresolved'].includes(owner.externalStorage)||owner.rootIds.some(root=>!roots.get(root)?.ownerIds.includes(owner.id)))throw Error('archive_inventory_invalid');
  owners.add(owner.id);participants.add(owner.participantId);
 }
 for(const root of roots.values())if(root.ownerIds.some(owner=>!owners.has(owner)))throw Error('archive_inventory_invalid');
 // Recompute eligibility even after the trusted composition validator. The
 // serialized boolean is a report, never authority to upgrade missing coverage.
 const nativeIds=new Set(),coveredNativeRoots=new Set();let incomplete=false;
 for(const artifact of inventory.nativeArtifacts){
  if(!id(artifact.id)||nativeIds.has(artifact.id)||!hash(artifact.sha256)||!hash(artifact.manifestDigest)||!Array.isArray(artifact.declaredRootIds))throw Error('archive_inventory_invalid');
  nativeIds.add(artifact.id);artifact.declaredRootIds.forEach(root=>coveredNativeRoots.add(root));
  if(artifact.completeNativeAuthority!==true||artifact.externalWritersExcluded!==true)incomplete=true;
 }
 const complete=inventory.owners.every(o=>o.externalStorage!=='unresolved')&&!inventory.omissions.some(o=>o.blocksComplete!==false)&&!incomplete&&
  inventory.roots.every(r=>!(r.coverage==='authoritative'&&r.capture==='omit')&&(r.capture!=='native-artifact'||coveredNativeRoots.has(r.id)));
 return {participants:[...participants],completeProduct:inventory.completeEligible===true&&complete};
}


function validateNativeEvidence(descriptor,inspected){
 const authority=inspected?.authority;
 if(inspected?.version!==1||inspected.artifactId!==descriptor.artifactId||inspected.sha256!==descriptor.sha256||
      inspected.contentType!=='application/x-tar'||inspected.format!=='amplifier-native-authority'||inspected.archiveVersion!==1||
      inspected.manifest?.path!=='native-manifest.sqlite3'||inspected.manifest.sha256!==descriptor.manifestDigest||
      inspected.manifest.digestKind!=='sha256-exact-native-manifest-sqlite3-bytes'||
      inspected.integrity?.artifactHashVerified!==true||inspected.integrity?.manifestHashVerified!==true||
      authority?.scope!=='all-configured-native-authority-roots'||authority.completeNativeBackup!==descriptor.completeNativeAuthority||
      authority.completeProductBackup!==false||canonical(authority.declaredRootIds)!==canonical(descriptor.declaredRootIds))throw Error('native_archive_evidence_mismatch');

 if(!Number.isSafeInteger(inspected.bytes)||inspected.bytes<1||!Number.isSafeInteger(authority.authoritativeOmissions)||authority.authoritativeOmissions<0||
    typeof authority.credentialsIncluded!=='boolean')throw Error('native_archive_evidence_mismatch');
}
function validateNativeWriterReview(descriptor,authority,reviewed){
 if(descriptor.externalWritersExcluded===true){
     const attestation=authority.externalWriters;
     if(attestation?.policy!=='operator-attested-stopped'||attestation.declaredBy!=='trusted-launcher'||
       attestation.nativeWriterGate!=='exclusive-held-through-capture'||attestation.nativeAdminGate!=='exclusive-held-through-capture'||
       attestation.noncooperatingWriters!=='not-independently-observed'||attestation.requiresOperatorVerification!==true||
       canonical(descriptor.externalWriterEvidence)!==canonical(attestation)||!reviewed||
       reviewed.attestationDigest!==digest(canonical(attestation))||reviewed.operatorAssumptionAccepted!==true)throw Error('native_external_writer_review_required');
    }
}

/** Trusted adapters belong to installation/composition and native authorities.
 * Neither inventory paths nor adapter selection come from a browser request. */
export async function createInstallationArchive(request,{validateInventory,readNativeArtifact,withSupervisorSnapshot}={}){
 if(!['darwin','linux'].includes(process.platform))throw Error('archive_platform_unsupported');
 if(typeof validateInventory!=='function')throw Error('trusted_inventory_validator_required');
 if(request.privateContentReviewed!==true)throw Error('private_archive_review_required');
 const inventory=structuredClone(await validateInventory(structuredClone(request.inventory))),facts=inventoryFacts(inventory);
 if(inventory.digest!==request.inventoryDigest)throw Error('archive_inventory_review_changed');
 const saved=await readInstalledServiceConfiguration(request.directory);
 const application=await readInstallationConfiguration(join(saved.directory,'application.json'));
 if(inventory.namespace!==saved.configuration.dataScope||inventory.account!==application.account||
    inventory.applicationStateDirectory!==join(saved.directory,'application'))throw Error('archive_installation_inventory_conflict');
 if(!isAbsolute(request.outputFile)||within(saved.directory,request.outputFile))throw Error('archive_output_must_be_separate');
 await canonicalPath(dirname(request.outputFile),true);
 if(!withSupervisorSnapshot)withSupervisorSnapshot=(await import('@amplifier/unified-distribution-update-owner')).withOfflineSupervisorSnapshot;
 if(typeof withSupervisorSnapshot!=='function')throw Error('offline_snapshot_owner_unavailable');
 const requestExpected=request.expected;
 if(requestExpected?.installationId!==saved.configuration.serviceLifecycle.installationId||requestExpected?.ownerId!==saved.configuration.serviceLifecycle.ownerId||requestExpected?.dataScope!==saved.configuration.dataScope)throw Error('archive_service_binding_conflict');
 return withSupervisorSnapshot({dataDirectory:saved.configuration.dataDirectory,inventoryDigest:inventory.digest,expected:requestExpected,
  stoppedCommandId:request.stoppedCommandId,participantIds:facts.participants},async ({proof,ledgers,state})=>{
   if(proof.inventoryDigest!==inventory.digest||!proof.ledgersFrozen||!proof.supervisorClosed)throw Error('archive_frozen_proof_required');
   let installedEvidence=null;
   if(request.compositionInventory){
    const fresh=await createInstalledStorageInventory({inventory:request.compositionInventory,directory:saved.directory,includeCredentials:request.includeCredentials===true,credentialsReviewed:request.credentialsReviewed===true});
    if(fresh.inventory.digest!==inventory.digest||canonical(fresh.captureRequirements)!==canonical(request.captureRequirements))throw Error('archive_installer_review_changed');
    installedEvidence=fresh.captureRequirements;
   }
   const entries=[],sources=new Map(),names=new Set();let totalBytes=0;
   const add=async (name,path,type='file')=>{
    member(name);if(names.has(name)||entries.length>=MAX_ENTRIES)throw Error('archive_member_duplicate_or_limit');names.add(name);
    const content=type==='file'?await fileHash(path):{bytes:0,sha256:digest(''),mode:(await lstat(path)).mode&0o777};
    totalBytes+=content.bytes;if(totalBytes>MAX_BYTES)throw Error('archive_size_limit');
    entries.push({name,type,...content});if(type==='file')sources.set(name,path);
   };
   const omissions=inventory.roots.filter(r=>r.capture==='omit').map(r=>r.path);
   const capturedPath=path=>inventory.roots.some(r=>r.capture==='file'?r.path===path:r.capture==='tree'&&within(r.path,path))&&!omissions.some(p=>within(p,path));
   const requiredProvenance=['application.json','initial-provisioning.json','supervisor-configuration.json','installer-input.json',
    'supervisor/owner/updates.sqlite3','supervisor/service/service.sqlite3'];
   if(['application.json','supervisor-configuration.json','installer-input.json'].some(name=>capturedPath(join(saved.directory,name)))&&
     !(request.includeCredentials===true&&request.credentialsReviewed===true))throw Error('installer_credentials_review_required');
   const captureOmissions=requiredProvenance.filter(name=>!capturedPath(join(saved.directory,name))).map(name=>({id:'installer:'+name,reason:'Required installer authority is not included in the declared capture',blocksComplete:true}));
   if(!installedEvidence)captureOmissions.push({id:'installer:unqualified-census',reason:'Fresh installed authority and signed release provenance were not qualified under the offline writer locks',blocksComplete:true});
   captureOmissions.push(...inventory.roots.filter(r=>r.coverage==='credential-excluded').map(r=>({id:'credentials:'+r.id,reason:'Declared credential authority was excluded',blocksComplete:true})));
   let completeProduct=facts.completeProduct&&captureOmissions.length===0;
   if(request.requireCompleteProduct===true&&!completeProduct)throw Error('archive_complete_coverage_unavailable');
   const ledgerRoot=saved.configuration.dataDirectory;
   const substituted=new Map([[join(ledgerRoot,'owner','updates.sqlite3'),ledgers.updates],[join(ledgerRoot,'service','service.sqlite3'),ledgers.service]]);
   const sidecars=new Set([...substituted.keys()].flatMap(p=>[p+'-wal',p+'-shm']));
   const selected=[];
   for(const root of inventory.roots){
    if(root.coverage==='credential-excluded'&&root.capture!=='omit')throw Error('archive_credentials_not_supported');
    if(root.capture==='omit'||root.capture==='native-artifact')continue;
    if(!['tree','file'].includes(root.capture)||!within(saved.directory,root.path))throw Error('archive_root_not_owned');
    if(selected.some(other=>within(other,root.path)||within(root.path,other)))throw Error('archive_overlapping_roots');selected.push(root.path);
    await canonicalPath(root.path,root.capture==='tree');
    const walk=async (path,name)=>{
     if(omissions.some(omitted=>within(omitted,path))||sidecars.has(path))return;
     const info=await lstat(path);if(info.isSymbolicLink())throw Error('archive_links_refused');
     if(await realpath(path)!==path)throw Error('archive_links_refused');
     if(info.isDirectory()){
      await add(name,path,'directory');
      for(const child of (await readdir(path)).sort())await walk(join(path,child),name+'/'+child);
     }else if(info.isFile())await add(name,substituted.get(path)??path);
     else throw Error('archive_special_file_refused');
    };
    await walk(root.path,'roots/'+root.id);
   }
   const nativeProofs=[];
   for(const descriptor of inventory.nativeArtifacts){
    if(typeof readNativeArtifact!=='function')throw Error('native_archive_reader_required');
    const artifact=await readNativeArtifact(structuredClone(descriptor));
    const inspected=artifact?.inspection,authority=inspected?.authority;
    validateNativeEvidence(descriptor,inspected);
    // Exact SQLite manifest digest comes from the native owner's verified
    // immutable artifact inspection. Keep native tar opaque for native restore.
    if(typeof authority.credentialsIncluded!=='boolean')throw Error('native_archive_evidence_mismatch');
    if(authority.credentialsIncluded===true&&!(request.includeCredentials===true&&request.credentialsReviewed===true))throw Error('native_credentials_review_required');
    if(authority.credentialsIncluded===false){completeProduct=false;captureOmissions.push({id:'native-credentials:'+descriptor.id,reason:'Native credentials were not included in the native archive scope',blocksComplete:true});}
    if(authority.authoritativeOmissions!==0){completeProduct=false;captureOmissions.push({id:'native-authority:'+descriptor.id,reason:'Native owner did not attest to an empty authoritative omission set',blocksComplete:true});}
    await canonicalPath(artifact.path,false);await add('native/'+descriptor.id,artifact.path);
    if(entries.at(-1).sha256!==descriptor.sha256||entries.at(-1).bytes!==inspected.bytes)throw Error('native_archive_bytes_changed');
    validateNativeWriterReview(descriptor,authority,request.nativeWriterReviews?.find(r=>r.artifactId===descriptor.artifactId));
    nativeProofs.push({id:descriptor.id,inspection:artifact.inspection,manifestDigest:descriptor.manifestDigest,
      operatorReview:request.nativeWriterReviews?.find(r=>r.artifactId===descriptor.artifactId)??null});
   }
   if(request.requireCompleteProduct===true&&!completeProduct)throw Error('archive_complete_coverage_unavailable');
   const manifest={schema:'amplifier-unified-installation-archive',version:1,inventory,proof,installedEvidence,releaseState:state,nativeProofs,
    coverage:{completeProduct,credentialCoverage:'declared-exclusions-only-not-content-redacted',installerCredentialsReviewed:request.includeCredentials===true&&request.credentialsReviewed===true,
      omissions:[...inventory.omissions,...captureOmissions]},entries,totalBytes,
    restore:{inactive:true,requiresRebinding:true,automaticResume:false,replayed:false}};
   const header=Buffer.from(canonical(manifest));if(header.length>MAX_HEADER)throw Error('archive_manifest_limit');
   const length=Buffer.alloc(8);length.writeBigUInt64BE(BigInt(header.length));
   const output=await open(request.outputFile,'wx',0o600);let success=false;
   try{
    const archiveHash=createHash('sha256');const write=async bytes=>{let n=0;while(n<bytes.length)n+=(await output.write(bytes,n)).bytesWritten;archiveHash.update(bytes);};
    await write(MAGIC);await write(length);await write(header);
    for(const entry of entries){if(entry.type!=='file')continue;const input=await open(sources.get(entry.name),constants.O_RDONLY|constants.O_NOFOLLOW);
     try{const stat=await input.stat();if(!stat.isFile()||stat.nlink!==1||stat.size!==entry.bytes||await range(input,0,entry.bytes,write)!==entry.sha256)throw Error('archive_source_changed');}finally{await input.close();}}
    await output.sync();await syncDirectory(dirname(request.outputFile));success=true;
    return {schema:'amplifier-unified-archive-receipt',version:1,archiveSha256:archiveHash.digest('hex'),manifestDigest:digest(header),inventoryDigest:inventory.digest,
     completeProduct,entries:entries.length,bytes:MAGIC.length+8+header.length+totalBytes,workReplayed:false};
   }finally{await output.close();if(!success)await rm(request.outputFile,{force:true});}
  });
}

async function inspectOpenArchive(handle){
 const stat=await handle.stat();if(!stat.isFile()||stat.size>MAX_BYTES+MAX_HEADER+MAGIC.length+8)throw Error('archive_file_invalid');
 if(!(await readExact(handle,MAGIC.length,0)).equals(MAGIC))throw Error('archive_format_invalid');
 const count=(await readExact(handle,8,MAGIC.length)).readBigUInt64BE();if(count<2n||count>BigInt(MAX_HEADER))throw Error('archive_manifest_limit');
 const header=await readExact(handle,Number(count),MAGIC.length+8);const manifest=JSON.parse(header.toString('utf8'));
 if(manifest.schema!=='amplifier-unified-installation-archive'||manifest.version!==1||!Array.isArray(manifest.entries)||manifest.entries.length>MAX_ENTRIES||canonical(manifest)!==header.toString('utf8'))throw Error('archive_manifest_invalid');
 validateStorageInventory(manifest.inventory);
 const facts=inventoryFacts(manifest.inventory);if((manifest.coverage?.completeProduct===true&&(!facts.completeProduct||manifest.coverage.omissions?.some(o=>o.blocksComplete!==false)))||
   typeof manifest.coverage?.completeProduct!=='boolean'||manifest.proof?.inventoryDigest!==manifest.inventory.digest||manifest.proof.ledgersFrozen!==true||manifest.proof.supervisorClosed!==true||
   !Array.isArray(manifest.proof.qualifiedOwners)||facts.participants.some(id=>!manifest.proof.qualifiedOwners.includes(id))||
   manifest.restore?.inactive!==true||manifest.restore.automaticResume!==false||manifest.restore.replayed!==false)throw Error('archive_coverage_invalid');
 const nativeProofs=manifest.nativeProofs;
 if(!Array.isArray(nativeProofs)||nativeProofs.length!==manifest.inventory.nativeArtifacts.length)throw Error('archive_native_proof_invalid');
 for(const descriptor of manifest.inventory.nativeArtifacts){
  const matches=nativeProofs.filter(p=>p.id===descriptor.id);if(matches.length!==1)throw Error('archive_native_proof_invalid');
  const proof=matches[0];validateNativeEvidence(descriptor,proof.inspection);
  validateNativeWriterReview(descriptor,proof.inspection.authority,proof.operatorReview);
  if(manifest.coverage.completeProduct&&(!proof.inspection.authority.credentialsIncluded||proof.inspection.authority.authoritativeOmissions!==0))throw Error('archive_coverage_invalid');
 }
 if(manifest.coverage.completeProduct){
  const installation=dirname(manifest.inventory.applicationStateDirectory),roots=manifest.inventory.roots;
  const required=['application.json','initial-provisioning.json','supervisor-configuration.json','installer-input.json','supervisor/owner/updates.sqlite3','supervisor/service/service.sqlite3'];
  const evidence=manifest.installedEvidence;
  if(evidence?.schema!=='amplifier-unified-installed-capture-requirements'||evidence.inventoryDigest!==manifest.inventory.digest||evidence.offlineOnly!==true||evidence.requiresStoppedApplication!==true||evidence.requiresClosedSupervisor!==true||!Array.isArray(evidence.sqliteExports)||!['updates','service'].every(kind=>evidence.sqliteExports.some(row=>row.kind===kind&&row.required===true&&row.method==='updates-owner-frozen-sqlite-export'))||!evidence.retainedReleases?.length)throw Error('archive_coverage_invalid');
  if(manifest.coverage.installerCredentialsReviewed!==true||roots.some(r=>r.coverage==='credential-excluded')||required.some(name=>{
   const path=join(installation,name);return !roots.some(r=>r.capture==='file'?r.path===path:r.capture==='tree'&&within(r.path,path))||roots.some(r=>r.capture==='omit'&&within(r.path,path));
  }))throw Error('archive_coverage_invalid');
 }
 let offset=MAGIC.length+8+Number(count),total=0;const seen=new Set(),files=new Set(),rows=[];
 const capturedRoots=new Map(manifest.inventory.roots.filter(r=>['tree','file'].includes(r.capture)).map(r=>[r.id,r]));
 const nativeRoots=new Map(manifest.inventory.nativeArtifacts.map(a=>[a.id,a]));
 for(const entry of manifest.entries){
  member(entry.name);if(seen.has(entry.name)||!['file','directory'].includes(entry.type)||!Number.isSafeInteger(entry.bytes)||entry.bytes<0||!hash(entry.sha256)||
   !Number.isInteger(entry.mode)||entry.mode<0||entry.mode>0o777)throw Error('archive_member_invalid');
  const [kind,root,...parts]=entry.name.split('/');
  if(kind==='roots'){
   const declared=capturedRoots.get(root);if(!declared||(declared.capture==='file'&&parts.length)||(parts.length===0&&entry.type!==(declared.capture==='tree'?'directory':'file')))throw Error('archive_member_not_declared');
  }else if(kind==='native'){
   const declared=nativeRoots.get(root);if(!declared||parts.length||entry.type!=='file'||entry.sha256!==declared.sha256)throw Error('archive_native_member_invalid');
  }else throw Error('archive_member_invalid');
  for(let parent=dirname(entry.name);parent!=='.';parent=dirname(parent))if(files.has(parent))throw Error('archive_member_parent_invalid');
  if(entry.type==='directory'&&(entry.bytes!==0||entry.sha256!==digest('')))throw Error('archive_directory_invalid');
  if(entry.type==='file')files.add(entry.name);seen.add(entry.name);
  total+=entry.bytes;if(total>MAX_BYTES||offset+entry.bytes>stat.size)throw Error('archive_size_invalid');
  if(await range(handle,offset,entry.bytes)!==entry.sha256)throw Error('archive_member_digest_mismatch');
  rows.push({...entry,offset});offset+=entry.bytes;
 }
 for(const entry of rows)for(let parent=dirname(entry.name);parent!=='.';parent=dirname(parent))if(files.has(parent))throw Error('archive_member_parent_invalid');
 for(const root of capturedRoots.keys())if(!seen.has('roots/'+root))throw Error('archive_declared_root_missing');
 for(const root of nativeRoots.keys())if(!seen.has('native/'+root))throw Error('archive_declared_native_missing');
 if(offset!==stat.size||total!==manifest.totalBytes)throw Error('archive_size_invalid');
 const archiveSha256=await range(handle,0,stat.size);
 return {manifest,rows,archiveSha256,manifestDigest:digest(header)};
}
export async function inspectInstallationArchive(path){
 const file=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const {manifest,archiveSha256,manifestDigest}=await inspectOpenArchive(file);return {manifest,archiveSha256,manifestDigest};}finally{await file.close();}
}
/** Restore data into a new inert container, never over live authorities. Exact
 * archive and manifest review happens before allocating the final namespace. */
export async function restoreInstallationArchive(request){
 if(!['darwin','linux'].includes(process.platform))throw Error('archive_platform_unsupported');
 if(request.privateContentReviewed!==true||!hash(request.archiveSha256)||!hash(request.manifestDigest))throw Error('archive_review_required');
 if(!isAbsolute(request.destination))throw Error('absolute_archive_destination_required');
 await canonicalPath(dirname(request.destination),true);
 const file=await open(request.archiveFile,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const review=await inspectOpenArchive(file);
  if(review.archiveSha256!==request.archiveSha256||review.manifestDigest!==request.manifestDigest)throw Error('archive_review_changed');
  // Exclusive mkdir after full validation. Never rename over an existing empty
  // directory, follow a link, or roll back by deleting a caller-owned directory.
  await mkdir(request.destination,{mode:0o700});
  await syncDirectory(dirname(request.destination));
  const marker=join(request.destination,'RESTORE-INCOMPLETE.json');
  const writeOnce=async(path,value)=>{const h=await open(path,'wx',0o600);try{await h.writeFile(canonical(value)+'\n');await h.sync();}finally{await h.close();}};
  await writeOnce(marker,{archiveSha256:review.archiveSha256,inactive:true,automaticResume:false});
  for(const row of review.rows){const path=join(request.destination,row.name);
   await mkdir(dirname(path),{recursive:true,mode:0o700});
   if(row.type==='directory'){await mkdir(path,{recursive:true,mode:0o700});continue;}
   const output=await open(path,'wx',0o600);try{const got=await range(file,row.offset,row.bytes,async bytes=>{let n=0;while(n<bytes.length)n+=(await output.write(bytes,n)).bytesWritten;});if(got!==row.sha256)throw Error('archive_changed_during_restore');await output.chmod(row.mode);await output.sync();}finally{await output.close();}
  }
  await writeOnce(join(request.destination,'ARCHIVE-MANIFEST.json'),review.manifest);
  const receipt={schema:'amplifier-unified-inactive-restore',version:1,archiveSha256:review.archiveSha256,manifestDigest:review.manifestDigest,
   completeProduct:review.manifest.coverage.completeProduct,inactive:true,requiresRebinding:true,automaticResume:false,workReplayed:false};
  const directories=new Set([request.destination]);for(const row of review.rows){let path=row.type==='directory'?join(request.destination,row.name):dirname(join(request.destination,row.name));while(within(request.destination,path)){directories.add(path);if(path===request.destination)break;path=dirname(path);}}
  for(const path of [...directories].sort((a,b)=>b.length-a.length))await syncDirectory(path);
  for(const row of review.rows.filter(r=>r.type==='directory').reverse())await chmod(join(request.destination,row.name),row.mode);
  await writeOnce(join(request.destination,'RESTORE-RECEIPT.json'),receipt);await rm(marker);await syncDirectory(request.destination);
  return receipt;
 }finally{await file.close();}
}
