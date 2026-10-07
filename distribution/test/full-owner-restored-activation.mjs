// Explicit test-only restore operator. No archive data is executable configuration.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
import {join} from 'node:path';

export async function activateRestoredFixture({root,directory,restoredApp,nativeFile,nativeEnv,descriptor,connect,installed,expected}) {
 const source=JSON.parse(await readFile(nativeFile,'utf8'));
 const client=connect(nativeFile,nativeEnv);
 const fence={fenceId:'restore-native-review',commandId:'restore-native-review',purpose:'recovery',instanceId:expected.instanceId,dataScope:expected.dataScope};
 let admin,restored;
 try {
  await client.request('initialize',{protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}});
  const held=await client.request('_amplifier/admin/lifecycle',{cwd:join(root,'workspace'),operation:'acquire',args:fence});
  assert.equal(held.acquired,true);admin=Object.fromEntries(['fenceId','commandId','leaseId'].map(k=>[k,held.lease[k]]));
  const call=(operation,args)=>client.request('_amplifier/admin',{cwd:join(root,'workspace'),operation,args,_meta:{'amplifier.dev/admin':admin}});
  const review=await call('maintenance.restore.preview',{artifactId:descriptor.artifactId,sha256:descriptor.sha256,destination:{rootId:'fixture',name:'restored-native'},privateContentReviewed:true,credentialsReviewed:true});
  assert.equal(review.startsWorker,false);
  restored=await call('maintenance.restore.apply',{commandId:'restore-native-once',previewHash:review.previewHash});
  assert.equal(restored.receipt.state,'succeeded',JSON.stringify(restored));
  const inspected=await call('maintenance.restore.inspect',{restoreCommandId:'restore-native-once'});
  assert.equal(inspected.finalized,true);assert.equal(inspected.pending,false);
  const released=await client.request('_amplifier/admin/lifecycle',{cwd:join(root,'workspace'),operation:'release',args:{...admin,outcome:'unchanged',proof:{verified:true,...fence,outcome:'unchanged',receiptId:'restore-native-reviewed'}}});
  assert.equal(released.released,true);
 } finally {await client.close();}
 const launcher=JSON.parse(await readFile(restored.launcherPath,'utf8'));
 const environment=JSON.parse(await readFile(restored.environmentPath,'utf8'));
 const newNativeFile=join(root,'restored-native-launcher.json');
 // Paths and runtime authority come from this trusted fixture, not the archive.
 const n={...source,...launcher,transferAuthorityDirectory:join(restoredApp,'capabilities/portability')};
 n.maintenanceFullNativeRoots={checkpoint:environment.AMPLIFIER_SESSION_STATE_HOME,events:environment.AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH,sources:environment.AMPLIFIER_SOURCE_STORE,bundle:launcher.bundle};
 await writeFile(newNativeFile,JSON.stringify(n),{flag:'wx',mode:0o600});
 const file=join(directory,'application.json'),original=await readFile(file,'utf8'),configuration=JSON.parse(original);
 const paths=[[join(directory,'application'),restoredApp],[source.home,launcher.home],[source.appHome,launcher.appHome],[nativeFile,newNativeFile]].sort((a,b)=>b[0].length-a[0].length);
 const rebind=value=>typeof value==='string'?paths.reduce((v,[from,to])=>v===from||v.startsWith(from+'/')?to+v.slice(from.length):v,value):Array.isArray(value)?value.map(rebind):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).map(([k,v])=>[k,rebind(v)])):value;
 const next=rebind(configuration);next.engines[0].env={...next.engines[0].env,...environment,AMPLIFIER_HOME:launcher.home,AMPLIFIER_WEB_HOME:launcher.appHome};
 // Only the finalized native owner receipt authorizes this copied-index rebase.
 // No reader or worker of the restored installation has started yet.
 const catalogArgs=next.catalogProcess.args;
 const projection=JSON.parse((await promisify(execFile)(next.catalogProcess.command,['-I','-B',new URL('./restore-catalog-fixture.py',import.meta.url).pathname,catalogArgs[catalogArgs.indexOf('--db')+1],source.home,launcher.home,createHash('sha256').update(JSON.stringify(restored)).digest('hex')],{maxBuffer:1024*1024})).stdout);
 assert.ok(projection.rebound>0);
 await writeFile(join(root,'pre-restore-application.json'),original,{flag:'wx',mode:0o600});
 await writeFile(join(root,'restore-activation-request.json'),JSON.stringify({commandId:'restored-explicit-input',text:'RESTORED-CHECK-42: confirm the saved artifact after restoring this installation.',sourceHome:source.home,restoredHome:launcher.home}),{flag:'wx',mode:0o600});
 await writeFile(file,JSON.stringify(next),{mode:0o600});
 installed.supervisor.service.resume({commandId:'restore-resume',stoppedCommandId:'archive-stop',expected});
 const resumed=await installed.supervisor.service.waitFor('restore-resume');
 assert.equal(resumed.status,'ready',JSON.stringify(resumed));assert.equal(resumed.admissionSettlement.state,'settled',JSON.stringify(resumed));
 let result;
 for(let i=0;i<1200;i++){
  const error=await readFile(join(root,'restore-activation-error.txt'),'utf8').catch(e=>{if(e.code==='ENOENT')return null;throw e;});
  assert.equal(error,null,error??'');
  result=await readFile(join(root,'restore-activation-result.json'),'utf8').then(JSON.parse).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
  if(result)break;await new Promise(resolve=>setTimeout(resolve,50));
 }
 assert.ok(result,'Restored signed child did not finish its explicit acceptance input');
 return {native:restored,resumed,catalogProjection:projection,...result,configurationReboundBy:'trusted-disposable-fixture-operator',sourceConfigurationPreserved:true};
}
