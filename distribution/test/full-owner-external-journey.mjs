// A human or approved browser controller drives this exact installed fixture.
// This runner never opens a browser or evaluates application code in one.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
export async function openExternalJourney({root,url,session}){
 const checks=[],receipts=[],read=name=>readFile(join(root,name),'utf8');
 const migration=JSON.parse(await read('journey-migration.json'));
 const count=async()=>(await read('provider-requests.jsonl')).trim().split('\n').length;
 async function phase(name,instructions){
  const nonce=randomUUID(),request={name,nonce,url,session,instructions,migration};
  await writeFile(join(root,'external-browser-phase.json'),JSON.stringify(request,null,2),{mode:0o600});
  console.log('External browser phase:',name,'in',root);
  const deadline=Date.now()+30*60*1000;
  while(Date.now()<deadline){
   const result=await read('external-browser-'+name+'.json').then(JSON.parse).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
   if(result){assert.equal(result.nonce,nonce);assert.equal(result.passed,true);assert.ok(Array.isArray(result.checks)&&result.checks.length);receipts.push(result);checks.push(...result.checks);return;}
   await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw Error('External browser qualification did not finish phase '+name);
 }
 await phase('before-update',[
  'Open two independent browser tabs. Select the migrated conversation and inspect saved history, outputs, the pending question, paused task and reviewed schedules.',
  'In the first tab enter JOURNEY-COPPER-941 in the pending question but do not submit. Leave that answer blank in the second tab.',
  'Enter Private unsent draft survives the switch in the first composer and Other browser keeps its own draft in the second. Do not send either draft.'
 ]);
 assert.equal(await count(),2,'Passive browser inspection and drafts cannot run inference');
 let disconnects=0;
 return {
  async disconnect(){await phase('disconnect-'+(++disconnects),['Navigate both fixture tabs to about:blank so maintenance can drain. Preserve their private browser storage.']);},
  async afterUpdate(){
   const before=await count();
   await phase('after-update',[
    'Return both tabs to the fixture. Verify their different unsent composer drafts and the first tab-only unsent question answer survived.',
    'Submit JOURNEY-COPPER-941 exactly once through the first question form. Wait for completion. Reload and verify the accepted answer is no longer pending in either tab.',
    'Verify the saved outputs and paused task remain available. Retain both unsent composer drafts.'
   ]);
   assert.equal(await count(),before+1,'One explicit answer must reach the provider once');
  },
  async afterRecovery(){
   const before=await read('provider-requests.jsonl');
   await phase('after-recovery',[
    'Return both tabs to the recovered fixture. Verify both private drafts, accepted answer, saved output versions and paused task survive.',
    'Inspect the conversation at a narrow mobile width and verify no horizontal overflow. Save screenshots of the restored result.'
   ]);
   assert.equal(await read('provider-requests.jsonl'),before,'Recovery readback cannot replay inference');
   const receipt={passed:true,driver:'external-browser-controller',checks,receipts,migration,paidInference:false,lostAcknowledgementInjection:false};
   await writeFile(join(root,'browser-journey.json'),JSON.stringify(receipt,null,2));return receipt;
  },
  async close(){}
 };
}
