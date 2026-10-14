import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createDistribution} from '../src/index.js';

test('packaged headless entrypoint authenticates through the public gateway and never repeats a saved command',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'unified-headless-')),web=join(directory,'web'),workspace=join(directory,'workspace');let app;
 try{
  await mkdir(web);await mkdir(workspace);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
  const hostPackage=dirname(dirname(fileURLToPath(import.meta.resolve('@amplifier/unified-host'))));
  const log=join(directory,'prompts.jsonl');
  app=await createDistribution({account:'owned-test',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',label:'Fixture',command:process.execPath,args:[join(hostPackage,'fixtures/acp-peer.mjs')],env:{FIXTURE_LOG:log}}]},
   {authorize:async request=>request.headers.authorization==='Bearer owned-test-token'?{account:'owned-test'}:undefined});
  const token=join(directory,'token'),prompt=join(directory,'prompt');await writeFile(token,'owned-test-token',{mode:0o600});await writeFile(prompt,'public gateway input');
  const common=['--server',app.url.replace(/^http/,'ws')+'/ahp','--token-file',token,'--state-dir',join(directory,'client'),'--identity','owned-account','--command','original','--output','json'];
  const execute=promisify(execFile),entry=fileURLToPath(new URL('../src/headless-cli.js',import.meta.url));
  const first=await execute(process.execPath,[entry,'run',...common,'--prompt-file',prompt,'--workspace',workspace]);
  const result=JSON.parse(first.stdout);assert.equal(result.status,'completed');assert.equal(result.output,'Echo: public gateway input');
  const recovered=JSON.parse((await execute(process.execPath,[entry,'inspect',...common])).stdout);assert.equal(recovered.session,result.session);assert.equal(recovered.output,result.output);
  assert.equal((await readFile(log,'utf8')).trim().split('\n').length,1);
  await writeFile(token,'different-token');await assert.rejects(execute(process.execPath,[entry,'inspect',...common]));
 }finally{await app?.close();await rm(directory,{recursive:true,force:true});}
});
