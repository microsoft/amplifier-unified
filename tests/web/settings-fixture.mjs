import {startFixture} from './fixture-readiness.mjs';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

export async function startSettingsFixture(env={}) {
 return startFixture({
  command:process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
  args:['-u',fileURLToPath(new URL('../fixtures/settings_ui_server.py',import.meta.url))],
  env:{...process.env,...env},tag:'settings-ui',name:'Settings',
 });
}

// Real host appearance commands. Capture destination/CAS once and wait for the
// browser's generation-bound storage/render reports, never synthesize an ACK.
export const readAppearance=page=>page.evaluate(async()=>{
 const receipt=await window.amplifier.dispatch('appearance.preferences.read',{});
 if(receipt.accepted!==true)throw Error(receipt.error||'Appearance read rejected');
 return receipt.result;
});
export async function settledAppearance(page) {
 await page.waitForFunction(()=>{
  const a=window.amplifier?.getState()?.appearance;
  return a?.availability==='connected'&&a.local?.reported&&!a.local.error&&
   a.observedCompositionId===a.composition?.id&&a.command?.status!=='pending';
 });
 return readAppearance(page);
}
export async function updateAppearance(page,patch) {
 const before=await settledAppearance(page),scope=before.activeScope;
 const args={clientId:before.clientId,scope,generation:before.generation,
  expectedRevision:scope==='client'?before.local.revision:before.shared.revision,patch,
  ...(scope==='client'?{expectedStorageVersion:before.local.storageVersion}:{})};
 const receipt=await page.evaluate(args=>window.amplifier.dispatch('appearance.preferences.update',args),args);
 assert.equal(receipt.accepted,true,'Captured appearance update accepted');
 await page.waitForFunction(({scope,revision})=>{
  const a=window.amplifier.getState().appearance;
  return a[scope==='client'?'local':'shared'].revision===revision+1;
 },{scope,revision:args.expectedRevision});
 const after=await settledAppearance(page);
 assert.equal(after.generation,before.generation);
 assert.equal(after.activeScope,scope);
 if(scope==='client'){
  assert.deepEqual(after.shared,before.shared,'Browser edits never write Global');
  const stored=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),after.namespace);
  assert.equal(stored.version,after.local.storageVersion);
  assert.deepEqual(stored.values,after.local.values);
 }
 return after;
}
export async function previewAppearance(page,{name,css}) {
 const before=await settledAppearance(page);
 const receipt=await page.evaluate(args=>window.amplifier.dispatch('appearance.preview',args),
  {clientId:before.clientId,name,css});
 assert.equal(receipt.accepted,true,'Document preview accepted');
 assert.ok(receipt.result.previewId,'Preview receipt owns an exact identity');
 await page.waitForFunction(({id,generation,clientId})=>{
  const a=window.amplifier.getState().appearance;
  return a.generation===generation&&a.clientId===clientId&&a.preview?.id===id;
 },{id:receipt.result.previewId,generation:before.generation,clientId:before.clientId});
 const after=await settledAppearance(page);
 assert.equal(after.preview.id,receipt.result.previewId);
 assert.equal(after.generation,before.generation);assert.equal(after.clientId,before.clientId);
 assert.deepEqual(after.effective,before.effective,'Preview never edits the complete profile');
 return after;
}
export async function cancelAppearancePreview(page) {
 const before=await readAppearance(page);
 const receipt=await page.evaluate(clientId=>window.amplifier.dispatch('appearance.preview.cancel',{clientId}),before.clientId);
 assert.equal(receipt.accepted,true,'Preview cancellation accepted');
 await page.waitForFunction(()=>!window.amplifier.getState().appearance.preview);
 const after=await settledAppearance(page);
 assert.equal(after.generation,before.generation);assert.equal(after.clientId,before.clientId);
 assert.deepEqual(after.effective,before.effective);
 return after;
}
