import {realpath,stat} from 'node:fs/promises';
import {isAbsolute,normalize} from 'node:path';

const token=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f]/.test(value);
const path=value=>typeof value==='string'&&value.length<=8192&&isAbsolute(value)&&normalize(value)===value&&value!=='/'&&!/[\x00-\x1f]/.test(value);

/** Trusted launcher bindings for infrastructure owners with no user-facing
 * capability. The launcher retains their lifetime; registration never invents
 * operation markers, held leases, storage coverage, or release authority. */
export function bindRuntimeOwners(input=[]){
 if(!Array.isArray(input)||input.length>32)throw Error('Bounded runtime owner bindings required');
 const ids=new Set();
 return input.map(binding=>{
  if(!binding||Object.keys(binding).some(key=>!['owner','storage'].includes(key))||!token(binding.owner?.id)||typeof binding.owner.acquire!=='function'||ids.has(binding.owner.id))throw Error('Exact distinct runtime owner binding required');
  ids.add(binding.owner.id);
  return {owner:binding.owner,storage:binding.storage===undefined?undefined:structuredClone(binding.storage)};
 });
}

/** Cold inventory only. A package declaration is checked against the public
 * composition artifact and the actual configured canonical root. A missing or
 * changed declaration remains an explicit gap, never guessed from an owner ID. */
export async function runtimeOwnerProvenance(bindings,config,components){
 const ownerProvenance={},omissions=[];
 for(const {owner,storage:s}of bindings){
  // An empty package key deliberately prevents fallback to a familiar owner ID.
  ownerProvenance[owner.id]={packageName:'',configKey:''};
  try{
   if(!s||Object.keys(s).some(key=>!['packageName','packageVersion','revision','configKey','rootRole','stateDirectory'].includes(key))||
      ![s.packageName,s.packageVersion,s.revision,s.configKey,s.rootRole].every(token)||!path(s.stateDirectory)||
      !Object.hasOwn(config,s.configKey))throw Error('Incomplete storage declaration');
   const artifact=components[s.packageName],configured=config[s.configKey]?.stateDirectory;
   if(!artifact||artifact.revision!==s.revision||artifact.version!==s.packageVersion||!path(configured))throw Error('Storage source or configuration does not match');
   const canonical=await realpath(configured);
   if(canonical!==s.stateDirectory||!(await stat(canonical)).isDirectory())throw Error('Storage root does not match');
   ownerProvenance[owner.id]={packageName:s.packageName,configKey:s.configKey,runtimeRoot:canonical,rootRole:s.rootRole};
  }catch{
   omissions.push({id:'runtime-provenance:'+owner.id,ownerId:owner.id,reason:'Runtime storage declaration does not match the qualified public package and configured canonical root',blocksComplete:true});
  }
 }
 return {ownerProvenance,omissions};
}
