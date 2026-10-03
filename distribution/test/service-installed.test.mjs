import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,copyFile,rm} from 'node:fs/promises';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {execFile,spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
const execute=promisify(execFile),here=dirname(fileURLToPath(import.meta.url)),distribution=resolve(here,'..');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
test('installed actual signed distribution opts into owned stop and offline resume with real participant proofs',
 {skip:process.env.DISTRIBUTION_SERVICE_COMPOSITION!=='1',timeout:180000},async t=>{
 const root=await mkdtemp(join(tmpdir(),'distribution-service-installed-'));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const packed=JSON.parse((await execute('npm',['pack','--ignore-scripts','--json','--pack-destination',root],{cwd:distribution})).stdout)[0];
 const archive=join(root,packed.filename),consumer=join(root,'consumer'),graph=join(root,'graph');
 await mkdir(consumer);await mkdir(graph);
 await writeFile(join(consumer,'package.json'),JSON.stringify({name:'service-composition-consumer',private:true,type:'module'}));
 await execute('npm',['install','--ignore-scripts','--no-audit','--no-fund','--omit=dev',archive],{cwd:consumer,maxBuffer:1024*1024});
 await execute('tar',['-xzf',archive,'-C',graph]);
 for(const name of ['service-composition-worker.mjs','https-git-fixture.mjs'])await copyFile(join(here,name),join(consumer,name));
 const configFile=join(root,'fixture.json'),receiptFile=join(root,'acceptance.json');
 const fullOwners=process.env.DISTRIBUTION_SERVICE_FULL_OWNER_PYTHON?{python:process.env.DISTRIBUTION_SERVICE_FULL_OWNER_PYTHON,provider:process.env.RECOVERY_NATIVE_PROVIDER}:undefined;
 if(fullOwners)assert.ok(fullOwners.provider,'An immutable installed-native provider fixture is required');
 await writeFile(configFile,JSON.stringify({sourcePackageRoot:join(graph,'package'),receiptFile,assembledArchiveSha256:hash(await readFile(archive)),fullOwners}));
 const child=spawn(process.execPath,[join(consumer,'service-composition-worker.mjs'),configFile],{cwd:consumer,detached:true,stdio:['ignore','pipe','pipe']});
 let stdout='',stderr='';child.stdout.on('data',c=>stdout+=c);child.stderr.on('data',c=>stderr+=c);
 const reap=()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}};
 // This process group was created by this test and contains only its isolated
 // signed fixtures. Never discover or signal an existing user service.
 const timer=setTimeout(reap,150000);
 try{
  const result=await new Promise(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));
  assert.equal(result.code,0,stdout+'\n'+stderr);
  const receipt=JSON.parse(await readFile(receiptFile,'utf8'));assert.equal(receipt.authenticatedServiceRelease,true);assert.equal(receipt.applicationFacadeResumed,true);
  if(fullOwners){assert.equal(receipt.allConfiguredOwnersServiceLifecycleQualified,true);assert.equal(receipt.nativeAgentAcceptance,'Core/Foundation initialization and graceful retirement; no inference');assert.equal(receipt.configuredOwners.length,17);}
  if(process.env.DISTRIBUTION_SERVICE_ACCEPTANCE_DIR){const out=process.env.DISTRIBUTION_SERVICE_ACCEPTANCE_DIR;await mkdir(out,{recursive:true});await copyFile(archive,join(out,packed.filename));await copyFile(receiptFile,join(out,'service-composition-acceptance.json'));}
 }finally{clearTimeout(timer);reap();}
});
