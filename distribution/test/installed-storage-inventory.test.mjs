import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,chmod,realpath,mkdir,symlink,link,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash,generateKeyPairSync,sign} from 'node:crypto';
import {releaseDigest} from '@amplifier/unified-distribution-update-owner';
import {createStorageInventory,validateStorageInventory} from '../src/storage-inventory.js';
import {createInstalledStorageInventory} from '../src/installed-storage-inventory.js';
const hash=v=>createHash('sha256').update(v).digest('hex');
async function fixture(t){
 const root=await realpath(await mkdtemp(join(tmpdir(),'installed-inventory-')));await chmod(root,0o700);t.after(()=>rm(root,{recursive:true,force:true}));
 const key=generateKeyPairSync('ed25519'),components=[{name:'fixture',version:'1.0.0',root:'',repository:'https://example.invalid/repo',ref:'main',revision:'a'.repeat(40)}];
 const files={'package.json':JSON.stringify({name:'fixture',version:'1.0.0'}),'cli.js':'// fixture; never executed\n'},archive=Buffer.from('opaque signed archive fixture');
 const release={identity:{id:'first',version:'1.0.0',revision:'a'.repeat(40),digest:'0'.repeat(64)},entrypoint:'cli.js',platform:'any',arch:'any',files:Object.entries(files).map(([path,data])=>({path,bytes:Buffer.byteLength(data),sha256:hash(data),mode:0o644})),components,artifact:{url:'https://example.invalid/fixture.tgz',bytes:archive.length,sha256:hash(archive)}};release.identity.digest=releaseDigest(release);
 const payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:1,recommendedId:'first',releases:[release]}));
 const signed={schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,key.privateKey).toString('base64')};
 const trust={channelUrl:'https://example.invalid/channel',trustedKeys:{fixture:key.publicKey.export({format:'pem',type:'spki'})},accessScope:'fixture',allowedArtifactOrigins:['https://example.invalid']};
 const binding={installationId:'installation',ownerId:'owner'};
 const application={account:'account',stateDirectory:join(root,'application'),supervision:{serviceLifecycle:binding,discoveryFile:join(root,'supervisor.json'),trustedKeys:trust.trustedKeys,hostControl:{discoveryFile:join(root,'host-control.json'),tokenFile:join(root,'host-token')}}};
 const values={
  'installer-input.json':{schema:'unified-installation-v1',directory:root,dataScope:'fixture',serviceLifecycle:{enabled:true},application:{account:'account'},sourceTracking:{sources:components,env:{FIXTURE_SECRET:'owned-test-sentinel'}},release:trust},
  'initial-provisioning.json':{schema:'distribution-pristine-installation-v1',directory:root,dataScope:'fixture',installationId:'installation',initial:release.identity},
  'supervisor-configuration.json':{schema:'distribution-supervisor-v1',serviceLifecycle:binding,dataScope:'fixture',dataDirectory:join(root,'supervisor'),tokenFile:join(root,'supervisor-token'),discoveryFile:join(root,'supervisor.json'),hostDiscoveryFile:join(root,'host-control.json'),provisioningAuthorityFile:join(root,'initial-provisioning.json'),initial:{identity:release.identity,handle:'release:'+release.identity.digest},release:{...trust,directory:join(root,'releases'),launchArgs:['--config',join(root,'application.json')]}},
  'application.json':application,'initial-provisioning.claim':{schema:'distribution-initial-claim-v1'},'installer-attempt.json':{schema:'unified-installation-attempt-v1',status:'ready'},
 };
 async function save(){for(const [name,value]of Object.entries(values))await writeFile(join(root,name),JSON.stringify(value),{mode:0o600});}
 await save();
 for(const dir of ['application','supervisor/owner','supervisor/service','releases/releases/'+release.identity.digest+'/package','releases/artifacts','releases/staging'])await mkdir(join(root,dir),{recursive:true,mode:0o700});
 for(const name of ['supervisor-token','host-token'])await writeFile(join(root,name),'owned-test-token',{mode:0o600});
 for(const name of ['supervisor.json','host-control.json'])await writeFile(join(root,name),'{}',{mode:0o600});
 for(const path of ['supervisor/owner/updates.sqlite3','supervisor/service/service.sqlite3'])await writeFile(join(root,path),'NOT A DATABASE: must never open as SQLite',{mode:0o600});
 const candidate=join(root,'releases/releases',release.identity.digest);
 await writeFile(join(candidate,'receipt.json'),JSON.stringify({schema:'distribution-candidate-v1',releaseId:'first',signed}),{mode:0o600});
 for(const [name,data]of Object.entries(files))await writeFile(join(candidate,'package',name),data,{mode:0o644});
 await writeFile(join(root,'releases/artifacts',release.artifact.sha256+'.tgz'),archive,{mode:0o400});
 const inventory=createStorageInventory({namespace:'fixture',account:'account',applicationStateDirectory:application.stateDirectory,owners:[{id:'application-updates',schemaVersion:1,revision:'b'.repeat(40),participantId:'application-updates',externalStorage:'none',rootIds:['application']},{id:'resources',schemaVersion:1,revision:'c'.repeat(40),participantId:'resources',externalStorage:'none',rootIds:['application']}],roots:[{id:'application',ownerIds:['application-updates','resources'],path:application.stateDirectory,coverage:'authoritative',capture:'tree'}]});
 const args={inventory,directory:root,includeCredentials:true,credentialsReviewed:true};return {root,values,save,args,inventory,candidate,release};
}
async function tree(path,prefix=''){const out={};for(const e of await readdir(path,{withFileTypes:true})){const key=prefix+e.name;if(e.isDirectory())Object.assign(out,await tree(join(path,e.name),key+'/'));else out[key]=hash(await readFile(join(path,e.name)));}return out;}

test('actual saved installer binds immutable provenance and exact offline exports without opening or changing ledgers',async t=>{
 const f=await fixture(t),before=await tree(f.root),original=structuredClone(f.inventory);
 const {inventory,captureRequirements:r}=await createInstalledStorageInventory(f.args);
 assert.equal(inventory.completeEligible,true);assert.deepEqual(validateStorageInventory(inventory),inventory);assert.deepEqual(f.inventory,original);
 assert.equal(r.inventoryDigest,inventory.digest);assert.equal(r.installationId,'installation');assert.equal(r.requiresStoppedApplication,true);assert.equal(r.requiresClosedSupervisor,true);
 assert.deepEqual(r.sqliteExports.map(x=>[x.kind,x.sourcePath,x.method]),[['updates',join(f.root,'supervisor/owner/updates.sqlite3'),'updates-owner-frozen-sqlite-export'],['service',join(f.root,'supervisor/service/service.sqlite3'),'updates-owner-frozen-sqlite-export']]);
 for(const ledger of r.sqliteExports){const root=inventory.roots.find(x=>x.id===ledger.rootId);assert.equal(root.capture,'file');assert.match(root.reason,/never copy/);}
 assert.equal(r.retainedReleases.length,1);assert.deepEqual(r.retainedReleases[0].identity,f.release.identity);assert.deepEqual(r.retainedReleases[0].components,f.release.components);
 assert.equal(r.retainedReleases[0].receiptSha256,hash(await readFile(join(f.candidate,'receipt.json'))));
 assert.equal(inventory.roots.find(x=>x.id==='installed:initial-provisioning.claim').coverage,'authoritative');
 assert.equal(JSON.stringify({inventory,r}).includes('owned-test-sentinel'),false);assert.equal(JSON.stringify({inventory,r}).includes('owned-test-token'),false);
 assert.deepEqual(await tree(f.root),before);
});
test('private config and token inclusion requires explicit credential review and omitted raw files remain partial',async t=>{
 const f=await fixture(t);await assert.rejects(createInstalledStorageInventory({...f.args,credentialsReviewed:false}),/credential_review/);
 const {inventory,captureRequirements:r}=await createInstalledStorageInventory({...f.args,includeCredentials:false,credentialsReviewed:false});
 assert.equal(inventory.completeEligible,false);assert.equal(r.credentials.included,false);
 assert.equal(inventory.roots.filter(x=>x.coverage==='credential-excluded').length,5);assert.equal(inventory.omissions.filter(x=>x.id.startsWith('installed:credentials:')).length,5);
 assert.equal(inventory.roots.some(x=>x.path===f.root&&x.capture==='tree'),false);
});
test('binding, participant and broad-root shortcuts are refused before a declaration is returned',async t=>{
 const f=await fixture(t);
 const {digest,completeEligible,...base}=f.inventory;
 for(const change of [{account:'foreign'},{namespace:'foreign'}])await assert.rejects(createInstalledStorageInventory({...f.args,inventory:createStorageInventory({...base,...change})}),/binding_conflict/);
 const owners=structuredClone(base.owners);owners[0].participantId='invented';await assert.rejects(createInstalledStorageInventory({...f.args,inventory:createStorageInventory({...base,owners})}),/actual_application_updates/);
 const owners2=structuredClone(base.owners);owners2[0].rootIds.push('broad');await assert.rejects(createInstalledStorageInventory({...f.args,inventory:createStorageInventory({...base,owners:owners2,roots:[...base.roots,{id:'broad',ownerIds:['application-updates'],path:join(f.root,'releases'),coverage:'authoritative',capture:'tree'}]})}),/already_declared/);
 const v=await createInstalledStorageInventory(f.args);await assert.rejects(createInstalledStorageInventory({...f.args,inventory:v.inventory}),/already_augmented/);
 f.values['supervisor-configuration.json'].dataDirectory='/tmp/foreign';await f.save();await assert.rejects(createInstalledStorageInventory(f.args),/binding_conflict/);
});
test('unknown installer and supervisor entries are explicit blocking authority, never blanket copied',async t=>{
 const f=await fixture(t);await writeFile(join(f.root,'private-other.json'),'private');await writeFile(join(f.root,'supervisor/owner/unrecognized.sqlite3'),'private');
 const {inventory}=await createInstalledStorageInventory(f.args);assert.equal(inventory.completeEligible,false);assert.equal(inventory.omissions.filter(x=>x.id.startsWith('installed:unknown:')).length,2);
 for(const r of inventory.roots.filter(x=>x.id.startsWith('installed:unknown:')))assert.equal(r.capture,'omit');
});
test('dirty, untracked and unsigned retained releases are not labelled reproducible',async t=>{
 for(const mode of ['dirty','untracked','signature','link']){
  const f=await fixture(t);
  if(mode==='dirty')await writeFile(join(f.candidate,'package/cli.js'),'unpublished edits');
  if(mode==='untracked')await writeFile(join(f.candidate,'package/local-only.txt'),'unpushed work');
  if(mode==='signature'){const p=join(f.candidate,'receipt.json'),v=JSON.parse(await readFile(p));v.signed.signature=Buffer.alloc(64).toString('base64');await writeFile(p,JSON.stringify(v));}
  if(mode==='link'){await rm(join(f.candidate,'package/cli.js'));await symlink(join(f.root,'host-token'),join(f.candidate,'package/cli.js'));}
  const {inventory,captureRequirements:r}=await createInstalledStorageInventory(f.args);assert.equal(inventory.completeEligible,false,mode);assert.equal(r.retainedReleases.length,0,mode);assert.ok(inventory.omissions.some(x=>x.id.startsWith('installed:release-unverified:')),mode);
 }
});
test('signed receipt is retained even when its original channel expired, without network/source resolution',async t=>{
 const f=await fixture(t),fetchBefore=globalThis.fetch;globalThis.fetch=()=>{throw Error('network must not be used');};try{const {inventory}=await createInstalledStorageInventory(f.args);assert.equal(inventory.completeEligible,true);}finally{globalThis.fetch=fetchBefore;}
});
test('missing authority, unverified artifacts and inherited external omissions remain incomplete',async t=>{
 const f=await fixture(t);await rm(join(f.root,'initial-provisioning.claim'));await writeFile(join(f.root,'releases/artifacts/unknown.tgz'),'not signed');
 const {digest,completeEligible,...base}=f.inventory;
 const inventory=createStorageInventory({...base,omissions:[{id:'external-owner',ownerId:'resources',reason:'Unsupported external authority',blocksComplete:true}]});
 const v=await createInstalledStorageInventory({...f.args,inventory});assert.equal(v.inventory.completeEligible,false);
 assert.ok(v.inventory.omissions.some(x=>x.id==='external-owner'));assert.ok(v.inventory.omissions.some(x=>x.id==='installed:missing:initial-provisioning.claim'));assert.ok(v.inventory.omissions.some(x=>x.id.startsWith('installed:artifact-unverified:')));
});
test('known WAL/SHM state is never read or raw-captured; the SQLite export requirement remains mandatory',async t=>{
 const f=await fixture(t);for(const s of ['-wal','-shm','-journal'])await writeFile(join(f.root,'supervisor/owner/updates.sqlite3'+s),'committed-or-transient',{mode:0o600});
 const v=await createInstalledStorageInventory(f.args);assert.equal(v.inventory.completeEligible,true);assert.equal(v.captureRequirements.sqliteExports.length,2);
 assert.equal(v.inventory.roots.filter(x=>x.id.startsWith('installed:ledger:updates-')&&x.capture==='omit').length,3);
});
test('linked authority and linked directory boundaries cannot authorize external reads or complete coverage',async t=>{
 const f=await fixture(t);await rm(join(f.root,'host-token'));await link(join(f.root,'supervisor-token'),join(f.root,'host-token'));
 assert.equal((await createInstalledStorageInventory(f.args)).inventory.completeEligible,false);
 await rm(join(f.root,'supervisor/owner'),{recursive:true});await symlink(join(f.root,'application'),join(f.root,'supervisor/owner'));await assert.rejects(createInstalledStorageInventory(f.args),/linked_installer/);
});
test('retained release census and parameter bounds fail closed instead of truncating authority',async t=>{
 const f=await fixture(t);await mkdir(join(f.root,'releases/releases','f'.repeat(64)));
 await assert.rejects(createInstalledStorageInventory({...f.args,maxReleases:1}),/release_limit/);await assert.rejects(createInstalledStorageInventory({...f.args,maxReleases:1000}),/release_limit/);
});
