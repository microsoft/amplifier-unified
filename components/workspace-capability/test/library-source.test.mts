import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile,realpath,readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createWorkspaceCapabilities} from '../src/index.js';
const hostSource=process.env.LIBRARY_HOST_SOURCE,catalogSource=process.env.LIBRARY_CATALOG_SOURCE,operationsSource=process.env.LIBRARY_OPERATIONS_SOURCE;
test('actual source Host/Catalog/Workspace25k query, explicit working/input-needed, no cold starts or native reads',{skip:!hostSource||!catalogSource||!operationsSource},async()=>{
 const {createHost,StdioCatalog}=await import(pathToFileURL(resolve(hostSource!,'src/index.ts')).href);
 const python=process.env.LIBRARY_PYTHON??'python3',dir=await realpath(await mkdtemp(join(tmpdir(),'library-source-'))),workspace=join(dir,'projects'),db=join(dir,'catalog.sqlite'),config=join(dir,'workspace.json');
 await import('node:fs/promises').then(fs=>fs.mkdir(workspace));const sentinel=join(workspace,'transcript.jsonl');await writeFile(sentinel,'canonical bytes untouched\n');
 const seed="from amplifier_session_catalog import Catalog\nimport sys\nc=Catalog(sys.argv[1]);w=sys.argv[2]\nc.upsert(dict(uri='ahp-session:/00000001',engineId='fixture',nativeSessionId='1',workingDirectory=w,title='Cold1',createdAt=1001,modifiedAt=1001))\nwith c.connect() as d:\n d.execute(\"WITH RECURSIVE ids(n) AS (VALUES(2) UNION ALL SELECT n+1 FROM ids WHERE n<25000) INSERT INTO sessions SELECT printf('ahp-session:/%08d',n),engine,printf('%d',n),workspace,parent_uri,kind,printf('Cold%d',n),1000+n,1000+n,availability,source,storage_path,native_project,fingerprint,seen_run,indexed_at,details,visible FROM ids,sessions WHERE uri='ahp-session:/00000001'\")\n";
 const seeded=spawnSync(python,['-P','-c',seed,db,workspace],{env:{...process.env,PYTHONPATH:join(catalogSource!,'src')},encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 const catalog=new StdioCatalog({command:python,args:['-P','-m','amplifier_session_catalog','serve','--db',db,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0'],env:{PYTHONPATH:join(catalogSource!,'src')}});
 await writeFile(config,JSON.stringify({stateDirectory:join(dir,'owner'),allowedRoots:[workspace],defaultRoot:workspace}));
 let host:any;const owner=createWorkspaceCapabilities({owner:{command:python,args:['-P','-m','amplifier_unified_workspaces.server','--config',config],env:{PYTHONPATH:[resolve('components/workspace-capability/python'),operationsSource!].join(':')}},catalog,libraryQuery:args=>host.queryLibrary(args as any)});
 host=await createHost({stateDirectory:join(dir,'host'),allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',command:process.execPath,args:[resolve(hostSource!,'fixtures/acp-peer.mjs')]}],catalog,capabilities:owner});
 const request=(args:any)=>owner.action({version:1,topic:'workspaces',operation:'workspace.sessions',channel:'host',commandId:randomUUID(),args:{libraryQueryVersion:1,...args}},{clientId:'source-test',origin:'ui'});
 try{
  const cold=await request({sort:'created'});assert.equal(cold.result.items.length,50);assert.ok(cold.result.items.every((r:any)=>r._meta['amplifier.dev/catalog'].activity.kind==='unknown'));assert.equal(host.diagnostics().activeAgents,0);
  await catalog.upsert({uri:'ahp-session:/00025001',engineId:'fixture',nativeSessionId:'25001',workingDirectory:workspace,title:'New metadata',createdAt:26001,modifiedAt:26001});
  const stale=await request({sort:'created',cursor:cold.result.nextCursor});assert.equal(stale.result.available,false);assert.equal(stale.result.refreshRequired,true);assert.equal(host.diagnostics().activeAgents,0);
  const uri='ahp-session:/00000003';assert.ok(!cold.result.items.some((r:any)=>r.resource===uri));
  const input=randomUUID();await host.submitTurn(uri,{commandId:input,text:'queue-hold',clientId:'source-test'});const before=host.diagnostics().activeAgents;
  const working=await request({sort:'created',activity:'working'});assert.deepEqual(working.result.items.map((r:any)=>r.resource),[uri]);assert.equal(host.diagnostics().activeAgents,before);await host.waitForTurn(uri,input);
  const approval=randomUUID();await host.submitTurn(uri,{commandId:approval,text:'approval',clientId:'source-test'});
  for(let n=0;n<200&&!host.store.get(uri)?.state.inputNeeded?.length;n++)await new Promise(r=>setTimeout(r,5));
  const attention=await request({activity:'attention'});assert.ok(attention.result.items.some((r:any)=>r.resource===uri));assert.equal(host.diagnostics().activeAgents,before);
  assert.equal(await readFile(sentinel,'utf8'),'canonical bytes untouched\n');assert.equal(attention.result.countsAvailable,false);
 }finally{await owner.close();await host.close();await rm(dir,{recursive:true,force:true});}
});
