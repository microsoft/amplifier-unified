import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,readFile,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {createDistribution} from '../src/index.js';
const python=process.env.LEGACY_CONTINUATION_PYTHON,legacy=process.env.LEGACY_UNIFIED_SOURCE,legacyPython=process.env.LEGACY_READBACK_PYTHON;
const hash=raw=>createHash('sha256').update(raw).digest('hex');

test('migrated saved memory survives reviewed context clear and reaches the provider without restoring cleared context',{
 skip:!python||!legacy||!legacyPython,timeout:180000,
},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'retained-memory-clear-')));
 const context=execFileSync(python,['-I','-B','-c','from pathlib import Path; import amplifier_module_context_simple as m; print(Path(m.__file__).parent)'],{encoding:'utf8'}).trim();
 const helper=fileURLToPath(new URL('./legacy-switch-fixture.py',import.meta.url));
 const run=mode=>JSON.parse(execFileSync(mode==='seed'?legacyPython:python,['-I','-B',helper,mode,legacy,root],{encoding:'utf8',env:{...process.env,LEGACY_MEMORY_QUALIFICATION:'1',LEGACY_CANDIDATE_CONTEXT_SOURCE:context}}));
 const seed=run('seed'),workspace=join(root,'workspace'),web=join(root,'web');await mkdir(web);await writeFile(join(web,'index.html'),'Owned saved-memory qualification');
 const transcript=join(root,'candidate-native',seed.relativeDirectory,'transcript.jsonl');
 const options={account:'retained-memory-clear',stateDirectory:join(root,'application'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,
  engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(root,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(root,'writers'),XDG_CACHE_HOME:join(root,'cache')}}],
  nativeAdmin:{engine:'amplifier'},catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(root,'catalog.sqlite'),'--home',join(root,'candidate-native'),'--app-home',join(root,'candidate-app'),'--workspace',workspace,'--scan-on-start','--scan-interval','0','--workspace-check-interval','0']}};
 let app;try{
  app=await createDistribution(options);await app.host.reconcileLibrary();
  const page=await app.host.queryLibrary({connectionId:'fixture-reader',allowedWorkspaceRoots:[workspace],limit:10,archive:'all'});assert.equal(page.items.length,1);
  const session=page.items[0].uri;await app.close();app=null;
  await writeFile(join(root,'memory-target.json'),JSON.stringify({session}));const memory=run('migrate-memory');
  options.recall={python};app=await createDistribution(options);await app.host.reconcileLibrary();
  const review=await app.host.nativeControl(session,'context.clear.review',{});assert.equal(review.canClear,true);
  const before=await readFile(transcript);
  const originalRows=JSON.parse(JSON.stringify(seed.originalRows));
  const admitted=before.toString().trim().split('\n').map(JSON.parse).slice(0,originalRows.length);
  for(const row of admitted)if(row.metadata){delete row.metadata._seq;if(!Object.keys(row.metadata).length)delete row.metadata;}
  assert.deepEqual(admitted,originalRows,'Resumption changed original content or provenance');
  const cleared=await app.host.nativeControl(session,'context.clear',{commandId:randomUUID(),expectedHistoryRevision:review.historyRevision,expectedControlRevision:review.controlRevision});assert.equal(cleared.cleared,true);
  assert.equal((await readFile(transcript)).length,0,'Clear must actually empty model context');
  const archive=join(root,'candidate-app/sessions',seed.nativeId,'history-revisions','context-'+cleared.archiveId,'transcript.jsonl');
  assert.equal(hash(await readFile(archive)),hash(before));
  await assert.rejects(stat(join(root,'provider-requests.jsonl')),e=>e.code==='ENOENT');
  await app.close();app=null;
  app=await createDistribution(options);await app.host.reconcileLibrary();
  const historical=await app.host.readHistoricalMessage(session,'original-typed-input');
  assert.equal(historical.text,'Keep the project phrase violet compass.');
  assert.equal(historical._meta['amplifier.dev/history'].historicalOnly,true);
  assert.equal(historical._meta['amplifier.dev/history'].preservedSource.complete,true);
  assert.equal(app.host.diagnostics().activeAgents,0,'Passive source verification started a worker');
  await assert.rejects(app.host.readUserMessage(session,'original-typed-input'),/not-indexed/);
  const commandId=randomUUID();await app.host.submitTurn(session,{commandId,text:'CONTINUE-MIGRATED-41. Use the saved project phrase and save the next artifact.',origin:'ui',clientId:'fixture-user'});
  const result=await app.host.waitForTurn(session,commandId,60000);assert.equal(result.status,'completed',JSON.stringify(result));assert.match(result.text,/violet compass/);
  const requests=await readFile(join(root,'provider-requests.jsonl'),'utf8');assert.equal(requests.trim().split('\n').length,2);
  assert.ok(!requests.includes('Later archived discussion'),'Cleared conversation was silently restored');
  const delivered=(await readFile(join(root,'memory-deliveries.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(delivered.length,2);assert.ok(delivered.every(items=>items.some(item=>item.id===memory.memoryId)));
  assert.equal(await readFile(join(root,'effects.jsonl'),'utf8'),'write\n');
  await app.close();app=null;app=await createDistribution(options);await app.host.reconcileLibrary();
  assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),requests,'Reopen replayed work');
  assert.equal(hash(await readFile(archive)),hash(before));assert.equal(hash(await readFile(join(root,'old-memories.sqlite3'))),memory.sourceSha256);
  const receipt={passed:true,root,session,memoryId:memory.memoryId,originalSourceSha256:hash(before),clearActuallyEmptiedContext:true,passiveSourceVerificationWorkers:0,providerDeliveries:2,explicitTurns:1,artifactEffects:1,clearedConversationRestored:false,currentHumanAuthorityCreated:false,coldRestartReplayed:false,oldMemoryDatabaseUnchanged:true};
  await writeFile(join(root,'acceptance.json'),JSON.stringify(receipt,null,2)+'\n');console.log('Retained memory clear receipt: '+join(root,'acceptance.json'));
 }finally{await app?.close();}
});
