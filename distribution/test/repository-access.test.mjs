import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir,devNull} from 'node:os';
import {execFileSync} from 'node:child_process';
import {repositoryEnvironment} from '../src/repository-access.js';

test('anonymous download policy excludes account injection and personal Git rewrites for nested children',async t=>{
 const root=await mkdtemp(join(tmpdir(),'anonymous-source-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const config=join(root,'personal-git'),text='[url "ssh://private.invalid/"]\n\tinsteadOf = https://github.com/\n[credential]\n\thelper = blocked-account\n';await writeFile(config,text);
 const original={...process.env,AMPLIFIER_GITHUB_ACCESS:'anonymous',GIT_CONFIG_GLOBAL:config,GIT_CONFIG_COUNT:'1',GIT_CONFIG_KEY_0:'http.extraHeader',GIT_CONFIG_VALUE_0:'Authorization: fixture',GIT_CONFIG_PARAMETERS:"'credential.helper=another-account'",GH_TOKEN:'fixture-token',GITHUB_TOKEN:'fixture-token',GH_ENTERPRISE_TOKEN:'fixture-token',GITHUB_ENTERPRISE_TOKEN:'fixture-token',GIT_ASKPASS:'/must-not-run',SSH_ASKPASS:'/must-not-run',HTTPS_PROXY:'http://proxy.invalid:8080',SSL_CERT_FILE:'/fixture-ca'};
 const env=repositoryEnvironment(original),url='https://github.com/example/public.git';
 const code=`const {execFileSync}=require('node:child_process');process.stdout.write(execFileSync('git',['ls-remote','--get-url',${JSON.stringify(url)}]));`;
 assert.equal(execFileSync(process.execPath,['-e',code],{cwd:root,env,encoding:'utf8'}).trim(),url);
 const settings=execFileSync('git',['config','--list'],{cwd:root,env,encoding:'utf8'});
 assert.ok(!/Authorization|blocked-account|another-account/.test(settings));
 for(const key of ['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN','GIT_ASKPASS','SSH_ASKPASS','GIT_CONFIG_PARAMETERS'])assert.equal(env[key],undefined);
 assert.equal(env.GIT_CONFIG_GLOBAL,devNull);assert.equal(env.HTTPS_PROXY,original.HTTPS_PROXY);assert.equal(env.SSL_CERT_FILE,original.SSL_CERT_FILE);
 assert.equal(await readFile(config,'utf8'),text);assert.equal(original.GH_TOKEN,'fixture-token');
 assert.deepEqual(repositoryEnvironment(env),env);
 const normal={...original,AMPLIFIER_GITHUB_ACCESS:'auto'};assert.deepEqual(repositoryEnvironment(normal),normal);
});
