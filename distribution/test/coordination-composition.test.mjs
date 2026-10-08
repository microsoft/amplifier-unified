import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {composeCoordination} from '../src/coordination.js';

test('peer delivery capabilities compose before the Host instance exists',async()=>{
 for(const peerSteering of [false,true])for(const peerResults of [false,true])for(const peerCreation of [false,true]){
  const owner=await composeCoordination({owner:{command:process.execPath,args:[]}}, {}, {
   host:()=>{throw Error('Host not constructed yet');},catalog:{},activeInputProof:true,peerInput:true,peerSteering,peerResults,peerCreation,
   admit:()=>{throw Error('Composition must not admit an input');},
  });
  try{assert.equal(!!owner.manifest.actions['coordination.create'],peerCreation);assert.ok(owner.manifest.actions['coordination.send']);assert.equal(!!owner.manifest.actions['coordination.reply'],peerResults);assert.equal(!!owner.manifest.actions['coordination.subscribe'],peerResults);}finally{await owner.close();}
 }
});

test('installed distribution starts and advertises peer steering with its real catalog and owner',{
 skip:!process.env.COORDINATION_PYTHON,timeout:30000,
},async()=>{
 const {createDistribution}=await import('../src/index.js'),directory=await mkdtemp(join(tmpdir(),'coordination-startup-'));
 const web=join(directory,'web'),workspace=join(directory,'workspace');await mkdir(web);await mkdir(workspace);await writeFile(join(web,'index.html'),'Owned startup check');
 const python=process.env.COORDINATION_PYTHON;let app;
 try{
  app=await createDistribution({account:'coordination-startup',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],
   engines:[{id:'no-execution',command:process.execPath,args:['-e','throw Error("No model work allowed during startup")']}],
   catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--scan-interval','0','--workspace-check-interval','0']},coordination:{python},
  });
  assert.equal((await fetch(app.url)).status,200);
  const schema=await app.capabilities.getActionSchemas();
  assert.deepEqual(schema['coordination.send'].schema.properties.mode.enum,['notify','queue','steer']);
  assert.ok(schema['coordination.create']);
  assert.equal(app.host.diagnostics().activeAgents,0);
 }finally{await app?.close();await rm(directory,{recursive:true,force:true});}
});
