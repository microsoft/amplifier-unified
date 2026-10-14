import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,chmod,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {readInstalledServiceConfiguration} from '../src/service.js';

async function fixture(t){
 const root=await realpath(await mkdtemp(join(tmpdir(),'installed-binding-')));await chmod(root,0o700);t.after(()=>rm(root,{recursive:true,force:true}));
 const initial={id:'first',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)};
 const release={channelUrl:'https://example.invalid/channel',trustedKeys:{fixture:'public-key'},accessScope:'fixture',allowedArtifactOrigins:['https://example.invalid']};
 const binding={installationId:'installation',ownerId:'owner'};
 const values={
  'installer-input.json':{schema:'unified-installation-v1',directory:root,dataScope:'fixture',serviceLifecycle:{enabled:true},sourceTracking:{sources:[{repository:'https://example.invalid/repo',ref:'main'}]},release},
  'initial-provisioning.json':{schema:'distribution-pristine-installation-v1',directory:root,dataScope:'fixture',installationId:'installation',initial},
  'supervisor-configuration.json':{schema:'distribution-supervisor-v1',serviceLifecycle:binding,dataScope:'fixture',dataDirectory:join(root,'supervisor'),tokenFile:join(root,'supervisor-token'),discoveryFile:join(root,'supervisor.json'),hostDiscoveryFile:join(root,'host-control.json'),provisioningAuthorityFile:join(root,'initial-provisioning.json'),initial:{identity:initial,handle:'release:'+initial.digest},release:{...release,directory:join(root,'releases'),launchArgs:['--config',join(root,'application.json')]}},
  'application.json':{stateDirectory:join(root,'application'),supervision:{serviceLifecycle:binding,discoveryFile:join(root,'supervisor.json'),trustedKeys:release.trustedKeys,hostControl:{discoveryFile:join(root,'host-control.json'),tokenFile:join(root,'host-token')}}},
 };
 async function save(){for(const [name,value]of Object.entries(values))await writeFile(join(root,name),JSON.stringify(value),{mode:0o600});}
 await save();return {root,values,save};
}
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
