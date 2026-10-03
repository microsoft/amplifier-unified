#!/usr/bin/env node
import {open} from 'node:fs/promises';
import {readInstallationConfiguration} from './installation.js';
import {createInstallationArchive,inspectInstallationArchive,restoreInstallationArchive} from './installation-archive.js';

// Local operator-only entry point. Private files carry trusted inventory/native
// provenance; paths and adapter modules are never accepted over a browser RPC.
async function main(){
 const [command,...values]=process.argv.slice(2),args=new Map();
 for(let i=0;i<values.length;i+=2){if(!values[i]?.startsWith('--')||values[i+1]===undefined||args.has(values[i]))throw Error('archive_arguments_invalid');args.set(values[i],values[i+1]);}
 const allowed={create:['--request'],review:['--archive','--output'],restore:['--request']};
 if(!allowed[command]||args.size!==allowed[command].length||allowed[command].some(k=>!args.has(k)))throw Error('archive_arguments_invalid');
 let result;
 if(command==='review'){
  const review=await inspectInstallationArchive(args.get('--archive'));
  const output=await open(args.get('--output'),'wx',0o600);try{await output.writeFile(JSON.stringify(review,null,2)+'\n');await output.sync();}finally{await output.close();}
  result={archiveSha256:review.archiveSha256,manifestDigest:review.manifestDigest,completeProduct:review.manifest.coverage.completeProduct,privateReviewSaved:true};
 }else{
  const input=await readInstallationConfiguration(args.get('--request'));
  if(command==='restore')result=await restoreInstallationArchive(input);
  else{
   // Owned and supplied by distribution composition, not reconstructed here.
   const {validateStorageInventory}=await import('./storage-inventory.js');
   const inventory=await readInstallationConfiguration(input.inventoryFile);
   const compositionInventory=input.compositionInventoryFile?await readInstallationConfiguration(input.compositionInventoryFile):undefined;
   result=await createInstallationArchive({...input,inventory,compositionInventory},{validateInventory:validateStorageInventory,readNativeArtifact:async descriptor=>{
    const source=input.nativeSources?.find(s=>s.id===descriptor.id);if(!source)throw Error('native_archive_source_required');
    return {path:source.artifactPath,inspection:await readInstallationConfiguration(source.inspectionFile)};
   }});
  }
 }
 process.stdout.write(JSON.stringify(result)+'\n');
}
main().catch(error=>{const code=error instanceof Error&&/^[a-z_]{1,100}$/.test(error.message)?error.message:'archive_operation_failed';process.stderr.write(JSON.stringify({error:code,workReplayed:false})+'\n');process.exitCode=1;});
