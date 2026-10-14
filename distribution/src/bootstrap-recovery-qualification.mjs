// Transitional, exact-artifact preview recovery only. This is not a generic
// stopped-owner capability. The root operator continuously owns the reviewed
// offline window; actual effective service masks remain until owner acquisition.
// The policy enumerates inspected files/tables/start paths, not echoed approval.
import {readFile,open} from 'node:fs/promises';
import {constants} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {isDeepStrictEqual as equal} from 'node:util';
import {isAbsolute,join} from 'node:path';
const hash=b=>createHash('sha256').update(b).digest('hex');
async function boundJson(item){
 if(!item||!isAbsolute(item.path)||!/^[a-f0-9]{64}$/.test(item.sha256))throw Error('bootstrap_recovery_input_invalid');
 const fd=await open(item.path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const s=await fd.stat();if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077)||s.size>16*1024*1024)throw Error('bootstrap_recovery_input_not_private');
  const bytes=await fd.readFile();if(hash(bytes)!==item.sha256)throw Error('bootstrap_recovery_input_changed');return JSON.parse(bytes);
 }finally{await fd.close();}
}
function inspect(python,policy,mode,allowedPid){
 return new Promise((resolve,reject)=>{
  const child=execFile(python,['-I','-B',fileURLToPath(new URL('./preview_bootstrap_inspection.py',import.meta.url))],
   {maxBuffer:4*1024*1024},(error,stdout)=>{
    let value;try{value=JSON.parse(stdout);}catch{return reject(Error('bootstrap_recovery_inspector_failed'));}
    if(error||value.error)return reject(Error(/^inspection_[a-z_]+$/.test(value.error??'')?value.error:'bootstrap_recovery_inspector_failed'));
    resolve(value);
   });
  child.stdin.end(JSON.stringify({policy,mode,allowedPid}));
 });
}
export async function createBootstrapRecoveryOptions({configuration:c,expected,api,preparing=false}){
 const recovery=c.bootstrapRecovery;
 if(recovery?.schema!=='preview-bootstrap-recovery-v1'||recovery.scope!=='operator-held-offline-window')throw Error('bootstrap_recovery_scope_required');
 const original=await boundJson(recovery.predecessorConfiguration),policy=await boundJson(recovery.policy);
 const facts=await api.inspectFailedBootstrapSource(original.authority.sourceDirectory);
 if(!equal([...original.expectedOwners].sort(),[...c.expectedOwners].sort())||
    !equal([...policy.owners].sort(),[...c.expectedOwners].sort())||
    policy.successorUnit!==c.sourceUnit||policy.sourceDirectory!==original.authority.sourceDirectory||
    policy.predecessorConfigurationSha256!==recovery.predecessorConfiguration.sha256)
  throw Error('bootstrap_recovery_composition_mismatch');
 const requiredAbsent=[original.authority.claimDirectory,original.authority.supervisorDirectory,
  join(original.receiptDirectory,facts.predecessor.instanceId+'-ready.json')];
 if(requiredAbsent.some(p=>!policy.absentPaths?.includes(p))||
  !policy.guardedFiles.some(row=>row.path===join(original.authority.sourceDirectory,'authority.json'))||
  !policy.guardedFiles.some(row=>row.path===join(original.authority.sourceDirectory,'key')))
  throw Error('bootstrap_recovery_admission_coverage_incomplete');
 const expectedRoots=c.bindings.filter(b=>b.kind==='directory').map(b=>b.path);
 if(expectedRoots.some(p=>!policy.roots.some(r=>r.path===p)))throw Error('bootstrap_recovery_roots_incomplete');
 if(!policy.maskedUnits?.length||!policy.guardedFiles?.length||!policy.artifacts?.length)throw Error('bootstrap_recovery_start_paths_incomplete');
 // Re-read hash-bound policy/config for every use. Neither a changed operator
 // file nor a stale audit result may be substituted after preparation.
 const fresh=async()=>{
  if(!equal(await boundJson(recovery.policy),policy)||!equal(await boundJson(recovery.predecessorConfiguration),original))
   throw Error('bootstrap_recovery_review_changed');
  await api.createLinuxSystemdSourceObserver({unit:original.sourceUnit,python:c.observerPython}).confirmFailedBootstrapExited(facts.witness);
 };
 const allowedPid=preparing?undefined:process.pid;
 const assertExclusionHeld=async()=>{await fresh();await inspect(c.observerPython,policy,'exclusion',allowedPid);};
 return {
  sourceDirectory:original.authority.sourceDirectory,
  claimDirectory:original.authority.claimDirectory,supervisorDirectory:original.authority.supervisorDirectory,
  predecessorBindings:original.bindings,expectedOwners:original.expectedOwners,
  successor:{directory:c.authority.sourceDirectory,unit:c.sourceUnit,expected,bindings:c.bindings},
  observer:api.createLinuxSystemdSourceObserver({unit:original.sourceUnit,python:c.observerPython}),
  assertExclusionHeld,
  qualifyStoppedState:async()=>{
   await fresh();
   const value=await inspect(c.observerPython,policy,'stopped',allowedPid);
   return {schema:'failed-bootstrap-stopped-state-v1',predecessor:facts.predecessor,witnessDigest:api.witnessDigest(facts.witness),
    predecessorBindingDigest:await api.inspectExistingStateBindings(original.bindings),
    successorBindingDigest:await api.inspectExistingStateBindings(c.bindings),
    inventoryDigest:value.inventory.digest,evidenceDigest:value.evidenceDigest,owners:original.expectedOwners,
    coverage:'complete',activeWriters:0,pendingEffects:0,unaccountedEffects:0,
    historicalUnknown:value.effects.historicalUnknown,publicReadyObserved:false,admissionObserved:false};
  },
 };
}
