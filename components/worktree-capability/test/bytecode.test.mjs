import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,cp,readdir,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const exec=promisify(execFile),python=process.env.WORKTREE_PYTHON??fileURLToPath(new URL('../.venv/bin/python',import.meta.url));

test('installed Git worker handles a read without adding cache files to its packaged module',async t=>{
 // This is the real installed public library, not a fake worker or argv spy.
 await exec(python,['-I','-B','-c','import amplifier_worktrees']);
 const root=await mkdtemp(join(tmpdir(),'worktree-bytecode-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const pkg=join(root,'package');await mkdir(join(pkg,'src'),{recursive:true});
 await writeFile(join(pkg,'package.json'),' {"type":"module"}');
 await cp(new URL('../src/git-worker.js',import.meta.url),join(pkg,'src/git-worker.js'));
 const modules=join(pkg,'python/amplifier_unified_worktrees');await mkdir(modules,{recursive:true});
 for(const name of ['__init__.py','worker.py'])await cp(new URL('../python/amplifier_unified_worktrees/'+name,import.meta.url),join(modules,name));
 const before=(await readdir(modules)).sort();
 const {createGitWorker}=await import(pathToFileURL(join(pkg,'src/git-worker.js')).href);
 const worker=createGitWorker({python,directory:join(root,'state')});
 try{await assert.rejects(worker.request('get',{id:'00000000-0000-4000-8000-000000000000'}),/not found|Unknown|does not exist/i);}finally{await worker.close();}
 assert.deepEqual((await readdir(modules)).sort(),before);
});
