import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,symlink} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {createInstallationArchive,inspectInstallationArchive,restoreInstallationArchive,stageNativeInstallationArtifact} from '../src/installation-archive.js';
import {DistributionUpdateOwner,ServiceLifecycleOwner,withOfflineSupervisorSnapshot} from '@amplifier/unified-distribution-update-owner';
import {validateStorageInventory} from '../src/storage-inventory.js';

const sort=v=>Array.isArray(v)?v.map(sort):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])])):v;
const hash=v=>createHash('sha256').update(v).digest('hex');
const seal=v=>({...v,digest:hash(JSON.stringify(sort(v)))});
async function fixture(t){
 const temp=await realpath(await mkdtemp(join(tmpdir(),'installation-archive-')));t.after(()=>rm(temp,{recursive:true,force:true}));
 const root=join(temp,'installation');await mkdir(root,{mode:0o700});await mkdir(join(root,'application'),{mode:0o700});
 await writeFile(join(root,'application','history.json'),'retained history');await writeFile(join(root,'application','credentials'),'secret fixture');
 const initial={id:'first',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},expected={installationId:'installation',ownerId:'owner',dataScope:'fixture',instanceId:'instance',releaseDigest:initial.digest};
 const release={channelUrl:'https://example.invalid/channel',trustedKeys:{fixture:'key'},accessScope:'fixture',allowedArtifactOrigins:['https://example.invalid']};
 const binding={installationId:'installation',ownerId:'owner'},target={identity:initial,handle:'release:'+initial.digest};
 const values={
  'installer-input.json':{schema:'unified-installation-v1',directory:root,dataScope:'fixture',serviceLifecycle:{enabled:true},sourceTracking:{sources:[]},release},
  'initial-provisioning.json':{schema:'distribution-pristine-installation-v1',directory:root,dataScope:'fixture',installationId:'installation',initial},
  'supervisor-configuration.json':{schema:'distribution-supervisor-v1',serviceLifecycle:binding,dataScope:'fixture',dataDirectory:join(root,'supervisor'),tokenFile:join(root,'supervisor-token'),discoveryFile:join(root,'supervisor.json'),hostDiscoveryFile:join(root,'host-control.json'),provisioningAuthorityFile:join(root,'initial-provisioning.json'),initial:target,release:{...release,directory:join(root,'releases'),launchArgs:['--config',join(root,'application.json')]}},
  'application.json':{account:'fixture',stateDirectory:join(root,'application'),supervision:{serviceLifecycle:binding,discoveryFile:join(root,'supervisor.json'),trustedKeys:release.trustedKeys,hostControl:{discoveryFile:join(root,'host-control.json'),tokenFile:join(root,'host-token')}}},
 };
 for(const [name,value]of Object.entries(values))await writeFile(join(root,name),JSON.stringify(value),{mode:0o600});
 // Exercise the independently installed public owner API. These archive-unit
 // fixtures use explicit deterministic host/process ports; the installed service
 // integration test separately qualifies actual owned process exit and restore.
 const unused=async()=>{throw Error('Unexpected fixture effect');};
 const releases={check:unused,prepare:unused,verify:async()=>true};
 const owner=new DistributionUpdateOwner({directory:join(root,'supervisor','owner'),dataScope:'fixture',initial:target,releases,lifecycle:{inspect:async()=>null,admitRestart:unused,restart:unused},preferences:{autoCheck:false,autoInstall:false,intervalMs:60000}});await owner.close();
 const service=new ServiceLifecycleOwner({directory:join(root,'supervisor','service'),...binding,dataScope:'fixture',releases,currentRelease:()=>target,
  lifecycle:{processes:{ownerId:'owner'},inspectOwned:async()=>({identity:initial,dataScope:'fixture',instanceId:'instance',ready:true}),stopOwned:async()=>({ownerId:'owner',instanceId:'instance',observedAt:1,code:0,signal:null})},
  host:{admitServiceStop:async()=>({admitted:true,commandId:'stop',purpose:'service-stop',expected,fenceId:'fence',evidence:{activeWork:0,intakeClosed:true,instanceId:'instance',dataScope:'fixture'}}),inspectServiceLifecycle:async()=>({fence:{phase:'held',fenceId:'fence',commandId:'stop',purpose:'service-stop',serviceIdentity:expected,instanceId:'instance',dataScope:'fixture',owners:['resources']}}),serviceStopReceipt:unused,releaseServiceStop:unused}});
 try{service.stop({commandId:'stop',expected});assert.equal((await service.waitFor('stop')).status,'stopped');}finally{await service.close();}
 const inventory=seal({schema:'amplifier-unified-storage-inventory',version:1,namespace:'fixture',account:'fixture',applicationStateDirectory:join(root,'application'),
  owners:[{id:'resources',schemaVersion:1,revision:'fixture',participantId:'resources',rootIds:['application','supervisor','credentials'],externalStorage:'none'}],
  roots:[{id:'application',ownerIds:['resources'],path:join(root,'application'),coverage:'authoritative',capture:'tree'},
   {id:'supervisor',ownerIds:['resources'],path:join(root,'supervisor'),coverage:'authoritative',capture:'tree'},
   {id:'credentials',ownerIds:['resources'],path:join(root,'application','credentials'),coverage:'credential-excluded',capture:'omit',reason:'credentials deliberately excluded'}],nativeArtifacts:[],omissions:[],completeEligible:true});
 return {root,temp,inventory,expected,request:{directory:root,inventory,inventoryDigest:inventory.digest,expected,stoppedCommandId:'stop',outputFile:join(temp,'backup.unified'),privateContentReviewed:true},ports:{validateInventory:validateStorageInventory,withSupervisorSnapshot:withOfflineSupervisorSnapshot}};
}
test('offline archive round trips declared state and frozen SQLite into an inactive namespace',async t=>{
 const f=await fixture(t),receipt=await createInstallationArchive(f.request,f.ports);assert.equal(receipt.completeProduct,false);
 const review=await inspectInstallationArchive(f.request.outputFile);assert.equal(review.archiveSha256,receipt.archiveSha256);
 assert.ok(!review.manifest.entries.some(e=>e.name.includes('credentials')||e.name.endsWith('-wal')));
 const destination=join(f.temp,'restored'),restored=await restoreInstallationArchive({archiveFile:f.request.outputFile,destination,...receipt,privateContentReviewed:true});
 assert.equal(restored.inactive,true);assert.equal(restored.automaticResume,false);assert.equal(await readFile(join(destination,'roots/application/history.json'),'utf8'),'retained history');
 await assert.rejects(restoreInstallationArchive({archiveFile:f.request.outputFile,destination,...receipt,privateContentReviewed:true}),/EEXIST/);
 assert.equal(await readFile(join(f.root,'application/credentials'),'utf8'),'secret fixture');
});
test('stale review, links and declared roots outside installation refuse without a published archive',async t=>{
 const f=await fixture(t);
 await assert.rejects(createInstallationArchive({...f.request,inventoryDigest:'b'.repeat(64)},f.ports),/review_changed/);
 await symlink(join(f.root,'application/history.json'),join(f.root,'application/link'));
 await assert.rejects(createInstallationArchive(f.request,f.ports),/links_refused/);await assert.rejects(readFile(f.request.outputFile),/ENOENT/);
 await rm(join(f.root,'application/link'));
 const {digest,...body}=f.inventory;body.roots[1].path=join(f.temp,'outside');const inventory=seal(body);
 await assert.rejects(createInstallationArchive({...f.request,inventory,inventoryDigest:inventory.digest},f.ports),/root_not_owned/);
});
test('raw installer configuration requires credential review and separately qualified complete coverage',async t=>{
 const f=await fixture(t),{digest,...body}=f.inventory;
 for(const name of ['application.json','initial-provisioning.json','supervisor-configuration.json','installer-input.json']){
  body.owners[0].rootIds.push(name);body.roots.push({id:name,ownerIds:['resources'],path:join(f.root,name),coverage:'authoritative',capture:'file'});
 }
 body.roots=body.roots.filter(r=>r.id!=='credentials');body.owners[0].rootIds=body.owners[0].rootIds.filter(id=>id!=='credentials');
 const inventory=seal(body),request={...f.request,inventory,inventoryDigest:inventory.digest};
 await assert.rejects(createInstallationArchive(request,f.ports),/installer_credentials_review_required/);
 const receipt=await createInstallationArchive({...request,includeCredentials:true,credentialsReviewed:true},f.ports);assert.equal(receipt.completeProduct,false);
 await assert.rejects(createInstallationArchive({...request,outputFile:join(f.temp,'complete.unified'),includeCredentials:true,credentialsReviewed:true,requireCompleteProduct:true},f.ports),/complete_coverage_unavailable/);
 const review=await inspectInstallationArchive(f.request.outputFile);assert.equal(review.manifest.coverage.credentialCoverage,'declared-exclusions-only-not-content-redacted');
});
test('native staging verifies bounded chunks and refuses altered payloads',async t=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'native-artifact-')));t.after(()=>rm(directory,{recursive:true,force:true}));
 const bytes=Buffer.alloc(300000,7),artifactId='d'.repeat(32),sha256=hash(bytes);let calls=0;
 const inspection={artifactId,sha256,bytes:bytes.length,integrity:{artifactHashVerified:true,manifestHashVerified:true}};
 const requestNative=async(method,args)=>{if(method.endsWith('inspect'))return inspection;calls++;const chunk=bytes.subarray(args.offset,args.offset+args.maxBytes);return {artifactId,sha256,bytes:bytes.length,offset:args.offset,encoding:'base64',data:chunk.toString('base64'),chunkSha256:hash(chunk),nextOffset:args.offset+chunk.length<bytes.length?args.offset+chunk.length:null};};
 const staged=await stageNativeInstallationArtifact({directory,artifactId,sha256},{requestNative});assert.equal(calls,2);assert.deepEqual(await readFile(staged.path),bytes);await rm(staged.path);
 await assert.rejects(stageNativeInstallationArtifact({directory,artifactId,sha256},{requestNative:async(method,args)=>{const result=await requestNative(method,args);if(method.endsWith('read'))result.chunkSha256='a'.repeat(64);return result;}}),/chunk_invalid/);
 await assert.rejects(readFile(staged.path),/ENOENT/);
});
test('tampered payload and wrong review are rejected before destination allocation',async t=>{
 const f=await fixture(t),receipt=await createInstallationArchive(f.request,f.ports),destination=join(f.temp,'restored');
 await assert.rejects(restoreInstallationArchive({archiveFile:f.request.outputFile,destination,...receipt,manifestDigest:'b'.repeat(64),privateContentReviewed:true}),/review_changed/);
 let bytes=await readFile(f.request.outputFile);bytes[bytes.length-1]^=1;await writeFile(f.request.outputFile,bytes);
 await assert.rejects(restoreInstallationArchive({archiveFile:f.request.outputFile,destination,...receipt,privateContentReviewed:true}),/digest_mismatch/);await assert.rejects(readFile(join(destination,'RESTORE-RECEIPT.json')),/ENOENT/);
});
test('blocking omission overrides a caller-supplied complete flag',async t=>{
 const f=await fixture(t),{digest,...body}=f.inventory;body.omissions=[{id:'outside',reason:'unregistered writer',blocksComplete:true}];let inventory=seal(body);
 await assert.rejects(createInstallationArchive({...f.request,inventory,inventoryDigest:inventory.digest},f.ports),/eligibility/);
 body.completeEligible=false;inventory=seal(body);const result=await createInstallationArchive({...f.request,inventory,inventoryDigest:inventory.digest},f.ports);assert.equal(result.completeProduct,false);
});

test('native authority requires exact inspected manifest and explicit writer attestation review',async t=>{
 const f=await fixture(t),{digest,...body}=f.inventory;
 const bytes=Buffer.from('opaque native artifact fixture'),path=join(f.temp,'native.tar');await writeFile(path,bytes,{mode:0o600});
 const attestation={policy:'operator-attested-stopped',declaredBy:'trusted-launcher',nativeWriterGate:'exclusive-held-through-capture',nativeAdminGate:'exclusive-held-through-capture',cooperativeSessionLeasesHeld:false,noncooperatingWriters:'not-independently-observed',requiresOperatorVerification:true};
 const descriptor={id:'native-authority',engineId:'native',artifactId:'a'.repeat(32),sha256:hash(bytes),manifestDigest:'b'.repeat(64),declaredRootIds:['native-home'],completeNativeAuthority:true,externalWritersExcluded:true,externalWriterEvidence:attestation};
 body.roots.push({id:'native-home',ownerIds:['resources'],path:join(f.temp,'external-native-home'),coverage:'authoritative',capture:'native-artifact'});body.owners[0].rootIds.push('native-home');body.owners[0].externalStorage='declared';body.nativeArtifacts=[descriptor];
 const inventory=seal(body),inspection={version:1,artifactId:descriptor.artifactId,sha256:descriptor.sha256,bytes:bytes.length,contentType:'application/x-tar',format:'amplifier-native-authority',archiveVersion:1,
  manifest:{path:'native-manifest.sqlite3',sha256:descriptor.manifestDigest,digestKind:'sha256-exact-native-manifest-sqlite3-bytes'},integrity:{artifactHashVerified:true,manifestHashVerified:true},
  authority:{scope:'all-configured-native-authority-roots',completeNativeBackup:true,completeProductBackup:false,declaredRootIds:descriptor.declaredRootIds,credentialsIncluded:true,authoritativeOmissions:0,externalWriters:attestation}};
 const request={...f.request,inventory,inventoryDigest:inventory.digest,includeCredentials:true,credentialsReviewed:true};const ports={...f.ports,readNativeArtifact:async()=>({path,inspection})};
 await assert.rejects(createInstallationArchive(request,ports),/native_external_writer_review_required/);
 const receipt=await createInstallationArchive({...request,nativeWriterReviews:[{artifactId:descriptor.artifactId,attestationDigest:hash(JSON.stringify(sort(attestation))),operatorAssumptionAccepted:true}]},ports);
 const reviewed=await inspectInstallationArchive(f.request.outputFile);assert.equal(receipt.completeProduct,false);assert.equal(reviewed.manifest.nativeProofs[0].manifestDigest,descriptor.manifestDigest);
});

test('archive review rejects traversal, duplicate members and file parents before allocating restore',async t=>{
 const f=await fixture(t);await createInstallationArchive(f.request,f.ports);const original=await readFile(f.request.outputFile),magic=Buffer.from('AMPLIFIER-UNIFIED-ARCHIVE-1\n');
 const headerBytes=Number(original.readBigUInt64BE(magic.length)),payload=original.subarray(magic.length+8+headerBytes),manifest=JSON.parse(original.subarray(magic.length+8,magic.length+8+headerBytes));
 for(const [name,edit,pattern]of [
  ['traversal',m=>{m.entries[1].name='roots/application/../../escaped';},/member_invalid/],
  ['duplicate',m=>{m.entries[1].name=m.entries[0].name;},/member_invalid/],
  ['parent',m=>{m.entries.find(e=>e.type==='file').name='roots/application/a/b';m.entries.push({name:'roots/application/a',type:'file',bytes:0,sha256:hash(''),mode:0o600});},/parent_invalid/],
  ['false-coverage',m=>{m.coverage.completeProduct=true;m.coverage.omissions=[];},/coverage_invalid/],
 ]){
  const changed=structuredClone(manifest);edit(changed);const header=Buffer.from(JSON.stringify(sort(changed))),size=Buffer.alloc(8);size.writeBigUInt64BE(BigInt(header.length));const raw=Buffer.concat([magic,size,header,payload]),archiveFile=join(f.temp,name+'.unified'),destination=join(f.temp,name+'-restore');await writeFile(archiveFile,raw);
  await assert.rejects(restoreInstallationArchive({archiveFile,destination,archiveSha256:hash(raw),manifestDigest:hash(header),privateContentReviewed:true}),pattern);
  await assert.rejects(readFile(join(destination,'RESTORE-INCOMPLETE.json')),/ENOENT/);
 }
});

test('installed native artifact inspection composes and restores exact opaque bytes',
 {skip:!process.env.DISTRIBUTION_NATIVE_ARCHIVE_FIXTURE},async t=>{
 const f=await fixture(t),nativeDirectory=process.env.DISTRIBUTION_NATIVE_ARCHIVE_FIXTURE;
 const path=join(nativeDirectory,'native-artifact.tar'),inspection=JSON.parse(await readFile(join(nativeDirectory,'native-inspection.json'),'utf8'));
 const {digest,...body}=f.inventory,authority=inspection.authority,attestation=authority.externalWriters;
 const descriptor={id:'native-authority',engineId:'native',artifactId:inspection.artifactId,sha256:inspection.sha256,manifestDigest:inspection.manifest.sha256,declaredRootIds:authority.declaredRootIds,completeNativeAuthority:authority.completeNativeBackup,externalWritersExcluded:true,externalWriterEvidence:attestation};
 for(const id of descriptor.declaredRootIds){body.roots.push({id,ownerIds:['resources'],path:join(f.temp,'native',id),coverage:'authoritative',capture:'native-artifact'});body.owners[0].rootIds.push(id);}
 body.owners[0].externalStorage='declared';body.nativeArtifacts=[descriptor];
 const inventory=seal(body),request={...f.request,inventory,inventoryDigest:inventory.digest,includeCredentials:true,credentialsReviewed:true,nativeWriterReviews:[{artifactId:descriptor.artifactId,attestationDigest:hash(JSON.stringify(sort(attestation))),operatorAssumptionAccepted:true}]};
 const receipt=await createInstallationArchive(request,{...f.ports,readNativeArtifact:async()=>({path,inspection})});
 const destination=join(f.temp,'native-restored');const restored=await restoreInstallationArchive({archiveFile:request.outputFile,destination,...receipt,privateContentReviewed:true});
 assert.equal(restored.inactive,true);assert.equal(restored.workReplayed,false);assert.equal(hash(await readFile(join(destination,'native/native-authority'))),inspection.sha256);
});
