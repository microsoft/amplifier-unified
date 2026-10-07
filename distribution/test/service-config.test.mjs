import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,chmod,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {readInstalledServiceConfiguration} from '../src/service.js';

import {serviceFixture as fixture} from './service-config-fixture.mjs';
test('reopen binds exact retained namespace, source authority and private configuration',async t=>{
 const f=await fixture(t),saved=await readInstalledServiceConfiguration(f.root);assert.equal(saved.directory,f.root);assert.deepEqual(saved.sourceTracking,f.values['installer-input.json'].sourceTracking);
 for(const [name,key,value]of [['supervisor-configuration.json','dataScope','foreign'],['supervisor-configuration.json','dataDirectory','/tmp/foreign'],['application.json','stateDirectory','/tmp/foreign'],['installer-input.json','directory','/tmp/foreign']]){
  const before=f.values[name][key];f.values[name][key]=value;await f.save();await assert.rejects(readInstalledServiceConfiguration(f.root),/binding_conflict/);f.values[name][key]=before;
 }
 await f.save();f.values['supervisor-configuration.json'].release.trustedKeys={other:'key'};await f.save();await assert.rejects(readInstalledServiceConfiguration(f.root),/binding_conflict/);
});
test('missing configuration, nonprivate files and absent lifecycle cannot authorize reopening',async t=>{
 const f=await fixture(t);await chmod(join(f.root,'installer-input.json'),0o644);await assert.rejects(readInstalledServiceConfiguration(f.root),/private_installation_configuration/);
 await chmod(join(f.root,'installer-input.json'),0o600);f.values['installer-input.json'].serviceLifecycle=undefined;await f.save();await assert.rejects(readInstalledServiceConfiguration(f.root),/binding_conflict/);
 await rm(join(f.root,'initial-provisioning.json'));await assert.rejects(readInstalledServiceConfiguration(f.root));
});
