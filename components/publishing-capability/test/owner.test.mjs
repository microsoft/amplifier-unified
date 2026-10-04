import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {createPublishingCapabilities} from '../dist/index.js';
const sid='ahp-session:/stdio-publication';
test('installed Python owner: explicit capture/review/deploy, scoped lazy reads and durable exact retry',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'pub-cap-'));await mkdir(join(directory,'workspace','dist'),{recursive:true});await writeFile(join(directory,'workspace','dist','index.html'),'<h1>Fixture</h1>');
 const config=join(directory,'config.json');await writeFile(config,JSON.stringify({dataDir:join(directory,'owner')}));
 const workspace={uri:sid,session:sid,workingDirectory:join(directory,'workspace'),executionDirectory:join(directory,'workspace'),executionRevision:0};let held=false,inspections=0;
 const provider=createPublishingCapabilities({owner:{command:process.env.PUBLISHING_PYTHON||resolve('python/.venv/bin/python'),args:['-m','amplifier_unified_publishing.server','--config',config]},inspectSession:async uri=>{assert.equal(uri,sid);inspections++;return workspace;},withSessionWorkspace:async(uri,expected,callback)=>{assert.equal(uri,sid);held=true;try{return await callback(workspace);}finally{held=false;}}});
 const invoke=(operation,args={},origin='ui')=>provider.action({version:1,channel:sid,topic:'publishing',operation:'publishing.'+operation,args,commandId:'fixture:'+operation},{clientId:'fixture',session:sid,origin});
 try{
  const schemas=await provider.actionSchemas();assert.ok(schemas['publishing.release']);assert.equal(inspections,0);
  const release=(await invoke('build',{requestId:'capture',siteId:'site',sourcePath:'dist'})).result;assert.equal(held,false);assert.equal(release.sessionId,sid);
  await invoke('review',{requestId:'review',releaseId:release.id,note:'Fixture content inspected'});
  await assert.rejects(invoke('deploy',{requestId:'denied',siteId:'site',releaseId:release.id,expectedRevision:0},'agent'),/approval/);
  const deployed=(await invoke('deploy',{requestId:'deploy',siteId:'site',releaseId:release.id,expectedRevision:0})).result;
  assert.equal(await(await fetch(deployed.result.url)).text(),'<h1>Fixture</h1>');
  const receipt=(await invoke('receipt',{requestId:'deploy'})).result;assert.equal(receipt.state,'succeeded');
  const listed=(await invoke('list')).result;assert.equal(listed.releases[0].fileCount,1);assert.equal(listed.releases[0].files,undefined);
  const selected=(await invoke('release',{releaseId:release.id})).result;assert.equal(selected.files[0].path,'index.html');
  await assert.rejects(invoke('list',{sessionId:'ahp-session:/elsewhere'}),/trusted context/);
  await invoke('stop',{siteId:'site',expectedRevision:1,requestId:'stop'});
  assert.deepEqual((await invoke('deploy',{requestId:'deploy',siteId:'site',releaseId:release.id,expectedRevision:0})).result,deployed);
  const state=await provider.read({uri:'amplifier-capability://publishing/publishing?scope=ahp-session%3A%2Fstdio-publication',topic:'publishing',scope:sid,clientId:'fixture'});assert.equal(state.data.publishing[sid].sites[0].status,'stopped');
 }finally{await provider.close();await rm(directory,{recursive:true,force:true});}
});
