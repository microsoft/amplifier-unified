import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, realpath, rm, rename, readFile, writeFile, chmod, symlink} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
const dist=process.env.DISTRIBUTION_OWNER_DIRECTORY??fileURLToPath(new URL('../dist',import.meta.url));
const {createManualIngressGate}=await import(pathToFileURL(join(dist,'manual-ingress.js')));
const {privateBytes}=await import(pathToFileURL(join(dist,'manual-authority.js')));
async function directory(t){const p=await realpath(await mkdtemp(join(tmpdir(),'authority-files-')));t.after(()=>rm(p,{recursive:true,force:true}));return p;}
function childGate(directory, refusal){
 const script=`import {createManualIngressGate} from ${JSON.stringify(pathToFileURL(join(dist,'manual-ingress.js')).href)};try{const gate=await createManualIngressGate({directory:${JSON.stringify(directory)},id:'fixture'});gate.close();if(${refusal})process.exitCode=2;}catch(e){if(!${refusal}||e.message!=='manual_authority_file_invalid')throw e;}`;
 return spawnSync(process.execPath,['--input-type=module','-e',script],{timeout:2000,encoding:'utf8'});
}
for(const name of ['key','authority.json'])test(`existing ingress refuses unwritten FIFO ${name} without waiting, then reopens restored authority`,async t=>{
 const p=await directory(t),gatePath=join(p,'gate');(await createManualIngressGate({directory:gatePath,id:'fixture'})).close();
 const file=join(gatePath,name),saved=file+'.saved',before=await readFile(file);
 assert.equal(childGate(gatePath,false).status,0);
 await rename(file,saved);assert.equal(spawnSync('mkfifo',['-m','600',file]).status,0);
 const result=childGate(gatePath,true);assert.equal(result.error,undefined);assert.equal(result.signal,null);assert.equal(result.status,0,result.stderr);
 await rm(file);await rename(saved,file);assert.deepEqual(await readFile(file),before);assert.equal(childGate(gatePath,false).status,0);
});
test('private authority reader retains regular, size, permission and no-follow guards',async t=>{
 const p=await directory(t),file=join(p,'private');await writeFile(file,'fixture',{mode:0o600});assert.equal((await privateBytes(file)).toString(),'fixture');
 await writeFile(file,Buffer.alloc(65537));await assert.rejects(privateBytes(file),/manual_authority_file_invalid/);
 await writeFile(file,'fixture');await chmod(file,0o644);await assert.rejects(privateBytes(file),/manual_authority_file_invalid/);await chmod(file,0o600);
 const link=join(p,'link');await symlink(file,link);await assert.rejects(privateBytes(link));await assert.rejects(privateBytes(p),/manual_authority_file_invalid/);
});
