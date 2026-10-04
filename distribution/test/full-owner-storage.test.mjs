import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createConfiguredStorageInventory, validateStorageInventory} from '../src/storage-inventory.js';
import {createFullOwnerReadyReceipt} from '../src/full-owner-ready.mjs';

const declaration = JSON.parse(await readFile(new URL('../examples/full-owner-storage-inventory.json', import.meta.url)));
const config = {
 account: 'fixture', stateDirectory: '/owned/install/application',
 allowedWorkspaceRoots: ['/owned/workspace'],
 portability: {stageDir: '/owned/workspace/.unified-transfer-stage', exchangeDir: '/owned/workspace/.unified-transfer-exchange'},
 host: {managedSessionRoot: '/owned/install/managed-sessions'},
 manualIngress: {stateDirectory: '/owned/install/ingress'},
 nativeAdmin: {engine: 'amplifier'}, engines: [{id: 'amplifier'}],
};
const ownerProvenance = {
 'manual-preview-ingress': {packageName: '@amplifier/unified-distribution-update-owner', configKey: 'manualIngress', runtimeRoot: config.manualIngress.stateDirectory},
 portability: {packageName: '@amplifier/unified-portability-capability', configKey: 'portability'},
 'managed-files': {packageName: '@amplifier/unified', configKey: 'managedFiles'},
 'native-admin': {packageName: '@amplifier/unified-native-capabilities', configKey: 'nativeAdmin'},
 'native-message-metadata': {packageName: '@amplifier/unified-native-capabilities', configKey: 'nativeAdmin'},
};
const components = Object.fromEntries(Object.values(ownerProvenance).map(owner => [owner.packageName, {revision: 'a'.repeat(40)}]));
const options = {namespace: 'fixture', quiescence: {requiredOwners: Object.keys(ownerProvenance)}, components, ownerProvenance};
const produce = (storage = declaration) => validateStorageInventory(createConfiguredStorageInventory(config, {...options, ...storage}));
const omissions = inventory => inventory.omissions.map(row => row.id).sort();
const ready = inventory => createFullOwnerReadyReceipt({
 source: false, identity: {instanceId: 'fixture'}, owners: inventory.owners.map(owner => owner.id),
 inventory, releaseBinding: {schema: 'fixture-binding'},
});

test('explicit full-owner example covers exactly the three external data roots without claiming native capture', () => {
 const before = structuredClone(config);
 const missing = produce({...declaration, externalRoots: declaration.externalRoots.filter(row => row.id === 'ingress')});
 assert.deepEqual(omissions(missing), ['engine:amplifier', 'managed-session-files', 'portability-exchange', 'portability-stage']);
 const inventory = produce();
 assert.deepEqual(omissions(inventory), ['engine:amplifier']);
 assert.equal(inventory.completeEligible, false);
 assert.deepEqual(inventory.nativeArtifacts, []);
 for (const id of ['native-admin', 'native-message-metadata'])
  assert.equal(inventory.owners.find(owner => owner.id === id).externalStorage, 'unresolved');
 assert.equal(ready(inventory).storageComplete, false);
 assert.deepEqual(config, before, 'declaration never expands workspace permissions');
 assert.deepEqual(inventory.roots.map(row => row.path).sort(), [
  config.stateDirectory, config.manualIngress.stateDirectory,
  config.portability.stageDir, config.portability.exchangeDir, config.host.managedSessionRoot,
 ].sort());
 for (const root of inventory.roots)
  for (const ownerId of root.ownerIds)
   assert.ok(inventory.owners.find(owner => owner.id === ownerId).rootIds.includes(root.id));
});

for (const id of ['portability-stage', 'portability-exchange', 'managed-session-files']) {
 test('removing the exact ' + id + ' declaration restores its blocking omission', () => {
  const inventory = produce({...declaration, externalRoots: declaration.externalRoots.filter(row => row.id !== id)});
  assert.deepEqual(omissions(inventory), ['engine:amplifier', id].sort());
  assert.ok(inventory.omissions.find(row => row.id === id).blocksComplete);
  assert.equal(ready(inventory).storageComplete, false);
 });
}

test('incorrect paths or transfer ownership do not hide the configured external authorities', () => {
 for (const id of ['portability-stage', 'portability-exchange', 'managed-session-files']) {
  const changed = structuredClone(declaration);
  changed.externalRoots.find(row => row.id === id).path += '-different';
  assert.ok(omissions(produce(changed)).includes(id));
 }
 const changed = structuredClone(declaration);
 changed.externalRoots.find(row => row.id === 'portability-stage').ownerIds = ['managed-files'];
 assert.ok(omissions(produce(changed)).includes('portability-stage'));
});

test('production ready receipt maps the real validated eligibility field for eligible and incomplete inventories', () => {
 const eligible = validateStorageInventory(createConfiguredStorageInventory({
  account: 'fixture', stateDirectory: '/owned/application',
 }, {
  namespace: 'fixture', quiescence: {requiredOwners: ['managed-files']},
  components, ownerProvenance: {'managed-files': ownerProvenance['managed-files']},
 }));
 assert.equal(eligible.completeEligible, true);
 assert.equal(Object.hasOwn(eligible, 'complete'), false);
 const receipt = ready(eligible);
 assert.equal(receipt.storageComplete, true);
 assert.equal(receipt.schema, 'full-owner-ready-v1');
 assert.equal(receipt.mode, 'supervised');
 assert.equal(createFullOwnerReadyReceipt({source: true, inventory: eligible}).mode, 'instrumented-source');
 assert.equal(ready(produce()).storageComplete, false);
 assert.equal(Object.hasOwn(receipt, 'completeProductBackup'), false);
 assert.equal(Object.hasOwn(receipt, 'archive'), false);
});
