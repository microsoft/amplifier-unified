import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,readFile,cp,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {createDistribution} from '../src/index.js';

const python=process.env.LEGACY_CONTINUATION_PYTHON,legacy=process.env.LEGACY_UNIFIED_SOURCE;
const helper=fileURLToPath(new URL('./legacy-switch-fixture.py',import.meta.url));
const hash=raw=>createHash('sha256').update(raw).digest('hex');

test('legacy chat continues through installed Host/Native and remains readable by the old app after new writes',{
 skip:!python||!legacy,timeout:120000,
},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'legacy-continuation-'))),run=mode=>JSON.parse(execFileSync(python,['-I','-B',helper,mode,legacy,root],{encoding:'utf8'}));
 const seed=run('seed'),workspace=join(root,'workspace'),web=join(root,'web');await mkdir(web);await writeFile(join(web,'index.html'),'Owned continuation rehearsal');
 const options={account:'legacy-continuation',stateDirectory:join(root,'application'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,
  engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(root,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(root,'writers'),XDG_CACHE_HOME:join(root,'cache')}}],
  nativeAdmin:{engine:'amplifier'},catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(root,'catalog.sqlite'),'--home',join(root,'candidate-native'),'--app-home',join(root,'candidate-app'),'--workspace',workspace,'--scan-on-start','--scan-interval','0','--workspace-check-interval','0']}};
 let app;try{
  app=await createDistribution(options);await app.host.reconcileLibrary();
  const page=await app.host.queryLibrary({connectionId:'fixture-reader',allowedWorkspaceRoots:[workspace],limit:10,archive:'all'});
  assert.equal(page.items.length,1,JSON.stringify(page));const session=page.items[0].uri;
  assert.equal(app.host.diagnostics().activeAgents,0);
  await assert.rejects(stat(join(root,'provider-requests.jsonl')),e=>e.code==='ENOENT');
  const id=randomUUID();await app.host.submitTurn(session,{commandId:id,text:'CONTINUE-MIGRATED-41. Use the previous phrase and save the next artifact.',origin:'ui',clientId:'fixture-user'});
  const result=await app.host.waitForTurn(session,id,60000);assert.equal(result.status,'completed',JSON.stringify(result));assert.match(result.text,/violet compass/);
  await app.host.refreshSessionHistory(session);
  const transcript=join(root,'candidate-native',seed.relativeDirectory,'transcript.jsonl');
  const rows=(await readFile(transcript,'utf8')).trim().split('\n').map(JSON.parse);
  const prefix=rows.slice(0,seed.originalRows.length).map((row,index)=>{
   const copy=structuredClone(row),original=seed.originalRows[index];
   // Context assigns canonical sequence numbers when admitting older rows.
   // Only that documented additive field may differ from the old serializer.
   if(original.metadata?._seq===undefined&&copy.metadata?._seq!==undefined){
    assert.equal(copy.metadata._seq,index);delete copy.metadata._seq;
    if(!original.metadata&&Object.keys(copy.metadata).length===0)delete copy.metadata;
   }
   return copy;
  });
  assert.deepEqual(prefix,seed.originalRows,'Original content or provenance changed during continuation');
  const requests=await readFile(join(root,'provider-requests.jsonl'),'utf8');assert.equal(requests.trim().split('\n').length,2);
  await app.close();app=null;
  app=await createDistribution(options);await app.host.reconcileLibrary();
  const context=await app.host.readSessionContext(session,10);assert.ok(context.messages.some(m=>m.text.includes('violet compass')));
  assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),requests,'Cold reopen replayed work');
  await app.close();app=null;
  const after=await readFile(transcript);await cp(join(root,'candidate-native'),join(root,'rollback-native'),{recursive:true,errorOnExist:true,force:false});
  const rollback=run('readback');assert.equal(hash(await readFile(join(root,'rollback-native',seed.relativeDirectory,'transcript.jsonl'))),hash(after));
  assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),requests);
  const originals=JSON.parse(await readFile(join(root,'original-hashes.json'),'utf8'));
  for(const [path,expected]of Object.entries(originals))assert.equal(hash(await readFile(join(root,'original-native',path))),expected);
  const receipt={kind:'legacy-chat-continuation-and-readback',passed:true,root,session,rollback,originalFilesUnchanged:Object.keys(originals).length,
   originalRowsPreserved:seed.originalRows.length,providerCalls:2,toolEffects:1,coldRestartReplayed:false,
   limits:['Isolated actual legacy serializer and display reader, current installed Host/Native and offline provider.',
    'Qualifies native chat continuation, new workspace artifact and old-version readback. It does not qualify a complete installation activation or migration of every product owner.',
    'The original pending job remains unchanged in the old installation; explicit continuation records its interrupted/unconfirmed outcome without replay. No production service or credentials used.']};
  await writeFile(join(root,'acceptance.json'),JSON.stringify(receipt,null,2));console.log('Continuation receipt: '+join(root,'acceptance.json'));
 }finally{await app?.close();}
});
