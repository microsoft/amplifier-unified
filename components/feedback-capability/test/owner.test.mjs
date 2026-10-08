import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,chmod} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
const {createFeedbackCapability,feedbackUploadScope}=await import(process.env.FEEDBACK_PACKAGE_MODULE??'../src/index.js');

const python=process.env.FEEDBACK_PYTHON,resourcesModule=process.env.FEEDBACK_RESOURCES_MODULE;
test('installed feedback owner shares bounded uploads only explicitly and preserves unknown GitHub delivery',{skip:!python||!resourcesModule},async()=>{
 const {createResourcesCapability}=await import(resourcesModule),directory=await mkdtemp(join(tmpdir(),'unified-feedback-'));
 let owner;
 try{
  const bin=join(directory,'bin');await mkdir(bin);await mkdir(join(directory,'state'));
  await writeFile(join(directory,'launch.json'),JSON.stringify({dataDir:join(directory,'state')}));
  await writeFile(join(bin,'gh'),`#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]; endpoint=next(value for value in args if value=='user' or value.startswith('repos/'))
payload=json.load(sys.stdin) if '--input' in args else None
with Path(os.environ['FEEDBACK_TEST_LOG']).open('a') as stream:stream.write(json.dumps({'endpoint':endpoint,'payload':payload})+'\\n')
if endpoint=='user': result={'id':7,'login':'fixture'}
elif endpoint.endswith('/issues'):
 print('Lost response',file=sys.stderr);sys.exit(1)
else: result={'full_name':'microsoft/amplifier-unified','private':True}
print(json.dumps(result))
`);await chmod(join(bin,'gh'),0o700);
  const create=()=>createFeedbackCapability({owner:{command:python,args:['-I','-m','amplifier_unified_feedback.server','--config',join(directory,'launch.json')],cwd:directory,env:{PATH:bin+':'+process.env.PATH,FEEDBACK_TEST_LOG:join(directory,'github.jsonl')}},
   uploadOwner:createResourcesCapability({directory:join(directory,'uploads'),inspectSession:async session=>{assert.equal(session,feedbackUploadScope);return {session,workingDirectory:directory}}}),
   inspectSession:async session=>({session}),readExport:async()=>{throw Error('No implicit conversation export')},
  });
  owner=create();const invoke=(operation,args,context={origin:'ui'})=>owner.action({topic:'feedback',version:1,channel:'ahp-root://',operation,args,commandId:'outer-'+crypto.randomUUID()},context).then(row=>row.result);
  const schemas=await owner.actionSchemas();assert.equal(schemas['feedback.upload.create'].schema.properties.size.maximum,8*1024*1024);
  const body=Buffer.alloc(600000,112),sha256=createHash('sha256').update(body).digest('hex');
  const meta=(await invoke('feedback.upload.create',{requestId:'upload-request',name:'reviewed.txt',size:body.length,sha256,contentType:'text/plain'})).attachment;
  const provider=owner.resourceProviders[0];assert.match(meta.uploadUri,/^amplifier-feedback-attachment:/);
  for(let offset=0;offset<body.length;offset+=meta.chunkBytes){const uri=new URL(meta.uploadUri);uri.searchParams.set('offset',String(offset));await provider.write({uri:uri.href,encoding:'base64',mode:'append',data:body.subarray(offset,offset+meta.chunkBytes).toString('base64')});}
  await invoke('feedback.upload.commit',{id:meta.id,requestId:'commit-request'});
  const added=await invoke('feedback.attachment.add',{requestId:'stage-request',resourceUri:meta.resourceUri,name:'reviewed.txt',sha256});assert.equal(added.status,'completed');assert.equal(added.attachment.size,body.length);
  await assert.rejects(readFile(join(directory,'github.jsonl')),/ENOENT/);
  const denied=await invoke('feedback.submit',{requestId:'agent-post',title:'No implied authority',body:'Do not send',category:'bug'},{origin:'agent',session:'ahp-session:/selected'});assert.equal(denied.status,'authorization_required');assert.equal(denied.executed,false);assert.equal(denied.error.code,'FEEDBACK_AUTHORIZATION_REQUIRED');
  await assert.rejects(readFile(join(directory,'github.jsonl')),/ENOENT/);
  const args={requestId:'explicit-post',title:'Reviewed report',body:'Only explicitly requested feedback',category:'bug'};
  assert.equal((await invoke('feedback.submit',args)).status,'unknown');
  await owner.close();owner=create();
  assert.equal((await invoke('feedback.receipt',{requestId:args.requestId})).status,'unknown');
  assert.equal((await invoke('feedback.submit',args)).status,'unknown');
  const calls=(await readFile(join(directory,'github.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(calls.filter(row=>row.endpoint.endsWith('/issues')).length,1);
  const snapshot=await owner.read({topic:'feedback',scope:'host',uri:owner.manifest.topics.feedback.uri});assert.ok(snapshot.data.feedback.items.length<=20);assert.equal(snapshot.data.feedbackDraft,undefined);
  const context={fenceId:'feedback-held',commandId:'restart',purpose:'distribution-update',instanceId:'original',dataScope:'feedback-test'};
  const lease=await owner.quiescenceParticipant('feedback').acquire(context);assert.equal(lease.fenceId,context.fenceId);
  await assert.rejects(invoke('feedback.upload.create',{requestId:'blocked',name:'blocked.txt',size:1,sha256:createHash('sha256').update('x').digest('hex'),contentType:'text/plain'}),/quiescen|intake|fenced/i);
  assert.equal((await invoke('feedback.receipt',{requestId:args.requestId})).status,'unknown');
  await owner.close();owner=create();
  const proof={...context,verified:true,outcome:'ready',instanceId:'replacement',receiptId:'trusted-host'};
  await owner.quiescenceParticipant('feedback').reconcileRelease({...context,outcome:'ready',proof});
  await owner.quiescenceParticipant('feedback').reconcileRelease({...context,outcome:'ready',proof});
  assert.equal((await invoke('feedback.receipt',{requestId:args.requestId})).status,'unknown');

 }finally{await owner?.close();await rm(directory,{recursive:true,force:true})}
});
