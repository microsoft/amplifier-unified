import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm,chmod,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
export async function serviceFixture(t){
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
