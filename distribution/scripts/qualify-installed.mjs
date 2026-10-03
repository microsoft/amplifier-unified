import {mkdtemp,copyFile,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';

const [distribution,web,receiptPath]=process.argv.slice(2);
if(!distribution||!web)throw Error('Usage: node scripts/qualify-installed.mjs distribution.tgz web.tgz [receipt.json]');
const archives=[resolve(distribution),resolve(web)],directory=await mkdtemp(join(tmpdir(),'unified-installed-consumer-'));
const run=(command,args)=>new Promise((resolve,reject)=>{
 const child=spawn(command,args,{cwd:directory,stdio:['ignore','pipe','pipe'],env:{...process.env,NODE_PATH:''}});let stdout='',stderr='';
 child.stdout.on('data',chunk=>{stdout+=chunk;});child.stderr.on('data',chunk=>{stderr+=chunk;});
 const timer=setTimeout(()=>{child.kill('SIGKILL');reject(Error('Installed consumer exceeded 90 seconds'));},90000);
 child.once('error',error=>{clearTimeout(timer);reject(error);});
 child.once('exit',code=>{clearTimeout(timer);code===0?resolve(stdout):reject(Error(stderr+'\n'+stdout));});
});
try{
 await writeFile(join(directory,'package.json'),JSON.stringify({private:true,type:'module'}));
 await run('npm',['install','--ignore-scripts',...archives,'ws@^8.18.0']);
 await copyFile(new URL('./installed-probe.mjs',import.meta.url),join(directory,'probe.mjs'));
 await copyFile(new URL('../test/fixtures/acp.mjs',import.meta.url),join(directory,'acp.mjs'));
 const result=JSON.parse(await run(process.execPath,['probe.mjs']));
 const artifacts=await Promise.all(archives.map(async path=>({path,sha256:createHash('sha256').update(await readFile(path)).digest('hex')})));
 const receipt={...result,artifacts,limits:['Independent ACP fixture only; native runtime and browser/device qualification are separate.']};
 if(receiptPath)await writeFile(resolve(receiptPath),JSON.stringify(receipt,null,2)+'\n');
 process.stdout.write(JSON.stringify(receipt,null,2)+'\n');
}finally{await rm(directory,{recursive:true,force:true});}
