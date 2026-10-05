import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {configuredOwnerLaunch} from '../src/owner-launch.js';
const exec=promisify(execFile);

// Exercise a real installed module: a flags-only assertion would not establish
// whether imports mutate an immutable runtime. No third-party packages needed.
test('a cold installed owner import preserves runtime bytes despite ambient Python settings',async t=>{
 const root=await mkdtemp(join(tmpdir(),'python-owner-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const environment=join(root,'runtime');await exec(process.env.PYTHON??'python3',['-I','-B','-m','venv','--without-pip',environment]);
 const python=join(environment,process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const purelib=(await exec(python,['-I','-B','-c',"import sysconfig;print(sysconfig.get_path('purelib'))"])).stdout.trim();
 const module=join(purelib,'installed_owner_probe');await mkdir(module);
 await writeFile(join(module,'__init__.py'),'');
 await writeFile(join(module,'__main__.py'),"import json,sys\nfrom . import implementation\nprint(json.dumps({'isolated':bool(sys.flags.isolated),'noBytecode':sys.dont_write_bytecode,'args':sys.argv[1:],'value':implementation.value}))\n");
 await writeFile(join(module,'implementation.py'),'value=42\n');
 const configuration=join(root,'configuration.json');await writeFile(configuration,'{}');
 const before=await readdir(module),env={...process.env,PYTHONDONTWRITEBYTECODE:'0',PYTHONPATH:join(root,'untrusted')};
 const launch=configuredOwnerLaunch({python,env},'installed_owner_probe',configuration,{cwd:root});
 const actual=JSON.parse((await exec(launch.command,launch.args,{cwd:launch.cwd,env:launch.env})).stdout);
 assert.deepEqual(actual,{isolated:true,noBytecode:true,args:['--config',configuration],value:42});
 assert.deepEqual(await readdir(module),before);
 assert.equal(await readFile(join(module,'implementation.py'),'utf8'),'value=42\n');
 // Causal predecessor: the same installed import without -B creates cache files,
 // even when the environment asks Python not to write them (-I ignores it).
 await exec(python,['-I','-m','installed_owner_probe','--config',configuration],{env:{...env,PYTHONDONTWRITEBYTECODE:'1'}});
 assert.ok((await readdir(join(module,'__pycache__'))).some(name=>name.startsWith('implementation.')));
});

test('an explicit executable keeps its existing argv, environment and directory',async()=>{
 const env={OWNER_SETTING:'retained'},launch=configuredOwnerLaunch({command:process.execPath,python:'/unused-python',env},'unused.module','chosen-config',{cwd:tmpdir()});
 assert.equal(launch.command,process.execPath);assert.deepEqual(launch.args,['--config','chosen-config']);
 assert.equal(launch.env,env);assert.equal(launch.cwd,tmpdir());
});
