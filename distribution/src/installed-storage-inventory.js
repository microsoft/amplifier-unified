import {constants} from 'node:fs';
import {lstat,open,opendir} from 'node:fs/promises';
import {join,relative,isAbsolute} from 'node:path';
import {createHash} from 'node:crypto';
import {readSignedChannel} from '@amplifier/unified-distribution-update-owner';
import {readInstalledServiceConfiguration} from './service.js';
import {readInstallationConfiguration} from './installation.js';
import {createStorageInventory,validateStorageInventory} from './storage-inventory.js';

const OWNER='application-updates', PREFIX='installed:';
const LEDGERS=[['updates','supervisor/owner/updates.sqlite3'],['service','supervisor/service/service.sqlite3']];
const CONFIGS=['installer-input.json','application.json','supervisor-configuration.json'];
const AUTHORITY=['initial-provisioning.json','initial-provisioning.claim','installer-attempt.json'];
const TOKENS=['host-token','supervisor-token'];
const DISCOVERY=['host-control.json','supervisor.json'];
const HEX=/^[a-f0-9]{64}$/;
const inside=(a,b)=>{const p=relative(a,b);return p===''||p!=='..'&&!p.startsWith('../')&&!isAbsolute(p);};
const sha=b=>createHash('sha256').update(b).digest('hex');
const issue=()=>Error('installed_inventory_binding_conflict');
async function info(path){try{return await lstat(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
async function jsonFile(path,limit){
 const handle=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const st=await handle.stat();if(!st.isFile()||st.nlink!==1||st.size>limit)throw Error('invalid_installed_receipt');return JSON.parse(await handle.readFile('utf8'));}finally{await handle.close();}
}
async function fileHash(path,maxBytes,budget){
 const handle=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{
  const st=await handle.stat();if(!st.isFile()||st.nlink!==1||st.size!==maxBytes)throw Error('installed_bytes_changed');
  budget.bytes+=st.size;if(budget.bytes>1024*1024*1024)throw Error('installed_inventory_byte_limit');
  const h=createHash('sha256');for await(const chunk of handle.createReadStream({autoClose:false}))h.update(chunk);return h.digest('hex');
 }finally{await handle.close();}
}
async function names(path,budget){
 const st=await info(path);if(!st)return null;if(!st.isDirectory()||st.isSymbolicLink())throw Error('linked_installer_directory');
 const result=[];const dir=await opendir(path);
 for await(const e of dir){if(++budget.entries>100000)throw Error('installed_inventory_entry_limit');result.push(e.name);}
 return result.sort();
}
async function verifiedPackage(path,release,budget){
 const expected=new Map(release.files.map(f=>[f.path,f])),seen=new Set(),directories=new Set();
 for(const f of release.files){const parts=f.path.split('/');for(let i=1;i<parts.length;i++)directories.add(parts.slice(0,i).join('/'));}
 async function walk(dir,prefix='',depth=0){
  if(depth>64)throw Error('installed_inventory_depth_limit');
  const entries=await names(dir,budget);if(entries===null)throw Error('installed_package_missing');
  for(const name of entries){
   const key=prefix+name,full=join(dir,name),st=await lstat(full);
   if(st.isSymbolicLink())throw Error('installed_package_link');
   if(st.isDirectory()){if(!directories.has(key))throw Error('untracked_installed_directory');await walk(full,key+'/',depth+1);continue;}
   const f=expected.get(key);if(!f||!st.isFile()||st.nlink!==1||st.size!==f.bytes||process.platform!=='win32'&&(st.mode&0o777)!==f.mode)throw Error('untracked_installed_file');
   if(await fileHash(full,f.bytes,budget)!==f.sha256)throw Error('installed_package_changed');seen.add(key);
  }
 }
 await walk(path);if(seen.size!==expected.size)throw Error('installed_package_incomplete');
}

/** Trusted, read-only maintenance inventory. This does not stop any writer,
 * open a ledger, capture bytes, attest an export, or authorize archive replay.
 * The capture consumer MUST enforce captureRequirements before copying roots.
 */
export async function createInstalledStorageInventory({inventory,directory,includeCredentials=false,credentialsReviewed=false,maxReleases=32}={}){
 const original=validateStorageInventory(inventory);
 if(typeof includeCredentials!=='boolean'||typeof credentialsReviewed!=='boolean'||includeCredentials&&!credentialsReviewed)throw Error('explicit_installer_credential_review_required');
 if(!Number.isInteger(maxReleases)||maxReleases<1||maxReleases>64)throw Error('invalid_retained_release_limit');
 const saved=await readInstalledServiceConfiguration(directory),root=saved.directory;
 const [input,application,authority]=await Promise.all(['installer-input.json','application.json','initial-provisioning.json'].map(n=>readInstallationConfiguration(join(root,n))));
 if(original.namespace!==saved.configuration.dataScope||original.applicationStateDirectory!==join(root,'application')||application.account!==original.account||input.application?.account!==original.account||input.directory!==root||authority.directory!==root)throw issue();
 const result=structuredClone(original),owner=result.owners.find(o=>o.id===OWNER&&o.participantId===OWNER);
 if(!owner||owner.revision==='unresolved')throw Error('actual_application_updates_participant_required');
 if(owner.externalStorage==='none')owner.externalStorage='declared';
 if(result.roots.some(r=>r.id.startsWith(PREFIX))||result.omissions.some(r=>r.id.startsWith(PREFIX)))throw Error('installer_inventory_already_augmented');
 // Never erase another declaration's unknown/external authority. Also refuse a
 // preexisting broad root that would swallow the credential exclusions below.
 if(result.roots.some(r=>r.path!==result.applicationStateDirectory&&inside(root,r.path)||inside(r.path,root)))throw Error('installer_root_already_declared');
 const budget={entries:0,bytes:0};
 const requirement={schema:'amplifier-unified-installed-capture-requirements',version:1,installationId:authority.installationId,dataScope:result.namespace,participantId:OWNER,inventoryDigest:null,offlineOnly:true,requiresStoppedApplication:true,requiresClosedSupervisor:true,credentials:{included:includeCredentials,reviewed:credentialsReviewed},sqliteExports:[],retainedReleases:[],recheckUnknownEntries:true};
 function omission(id,reason){result.omissions.push({id:PREFIX+id,ownerId:OWNER,reason,blocksComplete:true});}
 function declare(id,path,coverage,capture,reason){
  id=PREFIX+id;result.roots.push({id,ownerIds:[OWNER],path:join(root,path),coverage,capture,...(reason?{reason}:{})});owner.rootIds.push(id);
 }
 async function regular(name,{id=name,secret=false,required=true}={}){
  const st=await info(join(root,name));
  if(!st){if(required)omission('missing:'+id,'Required installer authority is absent: '+name);return false;}
  if(!st.isFile()||st.isSymbolicLink()||st.nlink!==1){declare(id,name,'authoritative','omit','Unverified installer entry type; no link is followed');omission('unsafe:'+id,'Unsafe installer authority: '+name);return false;}
  if(secret&&!includeCredentials){declare(id,name,'credential-excluded','omit','Raw private configuration or authentication token requires includeCredentials and credentialsReviewed');omission('credentials:'+id,'Private installer authority was explicitly excluded: '+name);}
  else declare(id,name,'authoritative','file');
  return true;
 }
 async function unknown(parent,allowed){
  const entries=await names(join(root,parent),budget);if(entries===null)return;
  for(const name of entries)if(!allowed.has(name)){
   const p=parent?parent+'/'+name:name,key=sha(p).slice(0,24);
   declare('unknown:'+key,p,'authoritative','omit','Unclassified installer-owned entry; explicit owner classification is required');omission('unknown:'+key,'Unclassified installer-owned entry: '+p);
  }
 }
 for(const n of CONFIGS)await regular(n,{secret:true});
 for(const n of AUTHORITY)await regular(n);
 for(const n of TOKENS)await regular(n,{secret:true});
 for(const n of DISCOVERY){
  const st=await info(join(root,n));if(!st)continue;
  if(!st.isFile()||st.isSymbolicLink()||st.nlink!==1){omission('unsafe:'+n,'Unverified discovery entry: '+n);continue;}
  declare(n,n,'derived-rebuildable','omit','Transient authenticated discovery endpoint; a restored installation must establish new process identity and endpoint');
 }
 const topAllowed=new Set([...CONFIGS,...AUTHORITY,...TOKENS,...DISCOVERY,'application','supervisor','releases']);
 await unknown('',topAllowed);
 await unknown('supervisor',new Set(['owner','service']));
 for(const [kind,p]of LEDGERS){
  const id='ledger:'+kind;
  if(await regular(p,{id})){
   const declared=result.roots.find(r=>r.id===PREFIX+id);declared.reason='Capture only from the Updates owner frozen offline SQLite export; never copy this live file or its WAL';
   requirement.sqliteExports.push({rootId:PREFIX+id,kind,sourcePath:join(root,p),required:true,method:'updates-owner-frozen-sqlite-export'});
  }
  const parent=p.slice(0,p.lastIndexOf('/')),base=p.slice(p.lastIndexOf('/')+1);
  await unknown(parent,new Set([base,base+'-wal',base+'-shm',base+'-journal']));
  for(const suffix of ['-wal','-shm','-journal']){
   const side=p+suffix,st=await info(join(root,side));
   // Required offline snapshots may create these companions. Their presence is
   // not new authority and must not invalidate the already reviewed inventory.
   // Unsafe present entries still block completeness without following them.
   if(st&&(!st.isFile()||st.isSymbolicLink()||st.nlink!==1)){omission('unsafe:'+id+suffix,'Unverified SQLite companion: '+side);continue;}
   declare(id+suffix,side,'derived-rebuildable','omit','SQLite transactional companion; its committed authority must be included by the required frozen SQLite export');
  }
 }
 await unknown('releases',new Set(['releases','artifacts','staging']));
 const releaseNames=await names(join(root,'releases/releases'),budget);
 if(!releaseNames)omission('missing:releases','Retained signed release provenance is absent');
 const candidates=releaseNames??[];
 if(candidates.length>maxReleases)throw Error('retained_release_limit_exceeded');
 const artifacts=new Map();let initialFound=false;
 for(const digest of candidates){
  const p='releases/releases/'+digest,receiptPath=p+'/receipt.json',key=sha(digest).slice(0,24);
  if(!HEX.test(digest)){declare('release-unknown:'+key,p,'authoritative','omit','Unrecognized retained release identity');omission('release-unknown:'+key,'Unrecognized retained release identity');continue;}
  try{
   await unknown(p,new Set(['receipt.json','package']));
   const receipt=await jsonFile(join(root,receiptPath),16*1024*1024);
   if(receipt.schema!=='distribution-candidate-v1'||Object.keys(receipt).some(k=>!['schema','releaseId','signed'].includes(k))||!receipt.signed||Object.keys(receipt.signed).some(k=>!['schema','keyId','payload','signature'].includes(k)))throw Error('unsupported_candidate_receipt');
   const {channel}=readSignedChannel(receipt.signed,saved.configuration.release.trustedKeys,false);
   const matches=channel.releases.filter(r=>r.identity.digest===digest&&r.identity.id===receipt.releaseId);
   if(matches.length!==1)throw Error('release_provenance_conflict');
   const release=matches[0];
   await verifiedPackage(join(root,p,'package'),release,budget);
   await regular(receiptPath,{id:'release:'+digest});
   declare('package:'+digest,p+'/package','reproducible-code','omit','Verified against the retained signed release file manifest; restore must requalify the exact signed artifact before execution');
   const provenance={rootId:PREFIX+'release:'+digest,identity:release.identity,receiptSha256:await fileHash(join(root,receiptPath),(await lstat(join(root,receiptPath))).size,budget),artifact:release.artifact,components:release.components};
   requirement.retainedReleases.push(provenance);artifacts.set(release.artifact.sha256,release.artifact);
   if(digest===authority.initial.digest&&release.identity.id===authority.initial.id&&release.identity.revision===authority.initial.revision&&release.identity.version===authority.initial.version)initialFound=true;
  }catch(e){
   if(/inventory_.*limit/.test(e.message))throw e;
   declare('release-unverified:'+digest,p,'authoritative','omit','Retained release provenance or local code is invalid, changed, linked, or incomplete; original bytes require explicit preservation');
   omission('release-unverified:'+digest,'Cannot classify retained release as reproducible: '+digest);
  }
 }
 if(!initialFound)omission('initial-release','The exact initially provisioned signed release is not qualified in retained provenance');
 const archives=await names(join(root,'releases/artifacts'),budget);
 for(const n of archives??[]){
  const p='releases/artifacts/'+n,a=artifacts.get(n.slice(0,-4));
  try{
   if(!/^[a-f0-9]{64}\.tgz$/.test(n)||!a||await fileHash(join(root,p),a.bytes,budget)!==a.sha256)throw Error('unverified_artifact');
   declare('artifact:'+a.sha256,p,'reproducible-code','omit','Exact retained signed artifact digest verified; source and artifact provenance are captured in the signed receipt');
  }catch(e){if(/inventory_.*limit/.test(e.message))throw e;const id='artifact-unverified:'+sha(n).slice(0,24);declare(id,p,'authoritative','omit','Unverified or dirty release artifact requires explicit preservation');omission(id,'Unverified retained artifact: '+n);}
 }
 const staging=await info(join(root,'releases/staging'));
 if(staging){if(!staging.isDirectory()||staging.isSymbolicLink())omission('staging','Unverified staging directory');else declare('staging','releases/staging','derived-rebuildable','omit','Interrupted download/extraction scratch only; durable commands remain in the frozen Updates ledger and are never replayed');}
 const augmented=createStorageInventory(result);requirement.inventoryDigest=augmented.digest;
 if(Buffer.byteLength(JSON.stringify(requirement))>1024*1024)throw Error('installed_capture_requirements_limit');
 return {inventory:augmented,captureRequirements:requirement};
}
